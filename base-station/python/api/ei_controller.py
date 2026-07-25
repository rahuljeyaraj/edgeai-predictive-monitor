"""EIController -- API-layer orchestration for Edge Impulse Round A
("Upload") and Round B ("Train/Build/Fetch"), per docs/
EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S4, reworked per S8 (2026-07-25):
upload() now always uploads every local recording for a device_type (never
a selection), wipes the EI project first, and fits/persists a pooled
per-device-type scalar-tail baseline (ei_scaling.py) instead of the old
per-node commissioning baseline -- see upload()'s own docstring. Mirrors
CaptureController/CommissioningController's role: a thin lifecycle shim
between REST routes and the ei_client/ei_projects/ei_scaling/capture/
features plumbing below it.

link() is a plain blocking call (no threading.Thread) -- FastAPI already
runs `def` route handlers in a worker thread (api/app.py's own docstring),
and it's a bounded request/response sequence. train(), fetch_model(), and
upload() are ALSO plain blocking calls with the same contract, but are
long-running (real EI job minutes, not a request/response) -- like
commissioning.py's own train(), the threading decision belongs to the
caller (api/app.py runs them off a background Thread and streams progress
over WS), not this class. Round B's Train button/route were dropped from
the dashboard per S8.2 (training stays EI-Studio-side), but train() itself
is left in place, still covered by ei_controller_test.py, per that
section's explicit "can stay dead code" call.

`client` is dependency-injected (defaults to the real ei_client module) so
tests can pass a fake with the same function names and never touch the
network -- same shape the project already uses for gate_factory callables
elsewhere.
"""
import json
import os
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, FrozenSet, List, Optional, Tuple

import ei_client
import ei_projects
import ei_scaling
from capture import CaptureError, list_captures, load_capture
from ei_client import EIClientError
from features import axis_names_for, scalar_dim_for, standardize_scalars
from registry import Registry, RegistryEntry, SensorChannel

_TEST_FRACTION = 0.2
# Batches were being sent one HTTP round-trip at a time -- fine for a
# handful of batches, painfully slow once a device type has a few hundred
# samples (each round-trip dominated by EI's server-side latency, not the
# tiny payload). Sent concurrently instead, same UPLOAD_BATCH_SIZE per
# request as before -- this only changes how many requests are in flight
# at once, not the request shape ei_client.py already validated live.
_UPLOAD_CONCURRENCY = 8


class EIControllerError(Exception):
    pass


class EIController:
    def __init__(self, registry: Registry, projects_path: str, captures_dir: str,
                 models_dir: str, scaling_path: str, client=ei_client):
        self._registry = registry
        self._projects_path = projects_path
        self._captures_dir = captures_dir
        self._models_dir = models_dir
        self._scaling_path = scaling_path
        self._client = client
        # device_type -> "train"/"fetch"/"upload" currently in flight --
        # in-memory only (like CaptureSession/CommissioningSession's own
        # state), so a restart clears it; guards against a double-click
        # firing two background threads at the same project.
        self._active_jobs: Dict[str, str] = {}

    def _device_types(self) -> List[str]:
        return sorted({entry.device_type for entry in self._registry.list().values()
                       if entry.device_type})

    def status(self) -> Dict[str, bool]:
        projects = ei_projects.load_projects(self._projects_path)
        return {dt: dt in projects for dt in self._device_types()}

    def project_ids(self) -> Dict[str, int]:
        """device_type -> Edge Impulse project_id, so the dashboard can
        link straight to the Studio project next to the "Linked" pill."""
        return {dt: p["project_id"]
                for dt, p in ei_projects.load_projects(self._projects_path).items()}

    def _entry_for(self, device_type: str) -> RegistryEntry:
        for entry in self._registry.list().values():
            if entry.device_type == device_type:
                return entry
        raise EIControllerError(f"no node currently has device_type {device_type!r}")

    def _labels_for(self, device_type: str) -> List[str]:
        return sorted({c["label"] for c in list_captures(self._captures_dir)
                       if c.get("device_type") == device_type})

    def link(self, device_type: str, username: str, password: str,
              totp: Optional[str] = None) -> dict:
        """Idempotent: a device_type already in ei_projects.json is a
        no-op, so re-linking (or linking a second device type right
        after) never re-creates a project. Not gated on a real node
        existing with that exact device_type beyond needing one to derive
        input_dim from -- same "device_type is scoping key" contract as
        the rest of S1.

        Named link()/"linked" rather than connect()/"connected" -- in EI's
        own vocabulary "connected" means a device is live on the ingestion
        WebSocket streaming data for inference, which is not what this
        does (this only creates/remembers a Studio project). Reusing that
        word here read as a claim about a live data connection that isn't
        true."""
        existing = ei_projects.get_project(self._projects_path, device_type)
        if existing is not None:
            return {"linked": True, "project_id": existing["project_id"]}

        entry = self._entry_for(device_type)
        input_dim = entry.input_dim
        axes = axis_names_for(entry.sensor_config)
        if len(axes) != input_dim:
            # Only possible if a node's committed input_dim disagrees with
            # registry's default per-channel bin count (axis_names_for()'s
            # assumption) -- e.g. a node that negotiated a non-default FFT
            # size. Raise loudly rather than silently uploading axis names
            # that don't line up with the actual vector columns.
            raise EIControllerError(
                f"device_type {device_type!r}: computed {len(axes)} axis names for a "
                f"{input_dim}-dim vector -- per-channel bin count must differ from the "
                f"registry default")
        jwt = self._client.login(username, password, totp)
        project_id, api_key = self._client.create_project(
            jwt, f"edgeai-predictive-monitor-{device_type}")
        learn_id = self._client.create_impulse(api_key, project_id, input_dim, axes)
        # Best-effort num_classes from labels already captured for this
        # device type; a lone class if none yet -- EI accepts changing
        # this via the same endpoint again before the first real train.
        num_classes = max(1, len(self._labels_for(device_type)))
        self._client.set_nn_config(api_key, project_id, learn_id, input_dim, num_classes)
        ei_projects.save_project(self._projects_path, device_type, project_id, api_key)
        return {"linked": True, "project_id": project_id}

    def unlink(self, device_type: str) -> dict:
        """Drops device_type's saved project mapping without calling EI's
        API -- there's nothing left to delete there once the project was
        already removed by hand in EI Studio (the scenario this exists
        for), and even if it wasn't, deleting someone's Studio project as
        a side effect of a local "forget this" click would be a surprising
        blast radius. A later link() then creates a brand new project
        rather than treating the stale local entry as still-linked."""
        removed = ei_projects.remove_project(self._projects_path, device_type)
        return {"removed": removed}

    def _model_path(self, device_type: str) -> str:
        return os.path.join(self._models_dir, f"{device_type}.tflite")

    def _labels_path(self, device_type: str) -> str:
        return os.path.join(self._models_dir, f"{device_type}.labels.json")

    def labels_for(self, device_type: str) -> Optional[List[str]]:
        """Class-label order for the model at _model_path(device_type),
        written by fetch_model() below -- None if no model has been
        fetched (or fetched before this existed). Read by whoever
        constructs a pipeline/classifier.py FaultClassifier for this
        device_type (pipeline/manager.py)."""
        path = self._labels_path(device_type)
        if not os.path.isfile(path):
            return None
        with open(path) as f:
            return json.load(f)

    def model_status(self) -> Dict[str, Optional[float]]:
        """device_type -> fetched model's mtime (epoch seconds), or None if
        fetch_model() has never completed for it. Whether the file exists
        on disk is the source of truth (mirrors capture.py's own
        on-disk-is-truth convention for its saved recordings) rather than a
        second JSON record that could drift from what's actually there."""
        return {dt: os.path.getmtime(self._model_path(dt)) if os.path.isfile(self._model_path(dt))
                else None for dt in self._device_types()}

    def job_state(self) -> Dict[str, str]:
        """device_type -> "train"/"fetch"/"upload" for whichever device
        types currently have one of those in flight -- lets the dashboard
        show progress even after a page refresh, not just while its own WS
        connection is open."""
        return dict(self._active_jobs)

    def train(self, device_type: str, on_progress: Optional[Callable[[str], None]] = None) -> dict:
        """Runs S4 steps 5-6 (generate features, then train) to completion,
        blocking the calling thread -- api/app.py runs this off a
        background Thread and passes on_progress to re-broadcast every
        stage/poll tick over WS, the same "long fixed-duration job, stream
        progress in between" shape as commissioning.py's own train().

        Raises EIControllerError synchronously (before any network call)
        if device_type isn't linked yet or a train/fetch job is already
        running for it. Since this method itself typically runs inside a
        background thread, callers that want those two cases surfaced as
        an immediate HTTP error rather than only via an async progress
        broadcast should also check status()/job_state() from the request
        thread first (api/app.py does both)."""
        if device_type in self._active_jobs:
            raise EIControllerError(
                f"a {self._active_jobs[device_type]!r} job is already running for {device_type!r}")
        project = ei_projects.get_project(self._projects_path, device_type)
        if project is None:
            raise EIControllerError(f"device_type {device_type!r} isn't linked to Edge Impulse yet")
        api_key, project_id = project["api_key"], project["project_id"]

        def progress(stage: str) -> None:
            if on_progress:
                on_progress(stage)

        self._active_jobs[device_type] = "train"
        try:
            progress("generating_features")
            job_id = self._client.generate_features(api_key, project_id)
            self._client.wait_for_job(
                api_key, project_id, job_id, on_poll=lambda: progress("generating_features"))

            progress("training")
            job_id = self._client.train(api_key, project_id)
            self._client.wait_for_job(
                api_key, project_id, job_id, on_poll=lambda: progress("training"))
        finally:
            del self._active_jobs[device_type]

        return {"trained": True}

    def fetch_model(self, device_type: str,
                     on_progress: Optional[Callable[[str], None]] = None) -> dict:
        """Runs S4 steps 8-9 (build the on-device TFLite model, then
        download it) and saves the result to
        <models_dir>/<device_type>.tflite, overwriting any model
        previously fetched for that type -- same "no versioning, single
        current file, nothing downstream reads more than the current one"
        call as commissioning.py's own per-node model_path. Same
        synchronous-error/threading contract as train() above; also
        requires a completed train() (build-ondevice-model has nothing to
        build otherwise, though this method doesn't itself track that --
        EI's own build job is the source of truth and will fail there).

        Also writes <models_dir>/<device_type>.labels.json: the model's
        class-label order, needed by FaultClassifier (pipeline/
        classifier.py) to turn its output vector back into named
        confidences. EI doesn't expose this on the deployment ZIP itself
        (extract_tflite() only pulls the .tflite entry) or via a separate
        documented endpoint we've used yet, so this derives it locally as
        sorted(distinct labels across every local capture for this
        device_type) -- the same population upload() (below) already
        trains on. This assumes EI's Keras classification block orders its
        output classes to match that same sorted order; unverified against
        a live account (same caveat as every other Round B endpoint --
        see the module docstring), the first thing to check if predictions
        come back plausible but mislabeled."""
        if device_type in self._active_jobs:
            raise EIControllerError(
                f"a {self._active_jobs[device_type]!r} job is already running for {device_type!r}")
        project = ei_projects.get_project(self._projects_path, device_type)
        if project is None:
            raise EIControllerError(f"device_type {device_type!r} isn't linked to Edge Impulse yet")
        api_key, project_id = project["api_key"], project["project_id"]

        def progress(stage: str) -> None:
            if on_progress:
                on_progress(stage)

        self._active_jobs[device_type] = "fetch"
        try:
            progress("building")
            job_id = self._client.build_model(api_key, project_id)
            self._client.wait_for_job(
                api_key, project_id, job_id, on_poll=lambda: progress("building"))

            progress("downloading")
            zip_bytes = self._client.download_model(api_key, project_id)
            model_bytes = self._client.extract_tflite(zip_bytes)

            labels = sorted({c["label"] for c in list_captures(self._captures_dir)
                              if c.get("device_type") == device_type})
            if not labels:
                raise EIControllerError(
                    f"no local recordings for device_type {device_type!r} -- can't determine "
                    f"the fetched model's class labels")

            os.makedirs(self._models_dir, exist_ok=True)
            path = self._model_path(device_type)
            # write-then-rename, same crash-safety convention as
            # ei_projects.save_project()'s own tmp-file dance.
            tmp_path = f"{path}.tmp"
            with open(tmp_path, "wb") as f:
                f.write(model_bytes)
            os.replace(tmp_path, path)

            labels_path = self._labels_path(device_type)
            labels_tmp_path = f"{labels_path}.tmp"
            with open(labels_tmp_path, "w") as f:
                json.dump(labels, f)
            os.replace(labels_tmp_path, labels_path)
        finally:
            del self._active_jobs[device_type]

        return {"fetched": True, "model_path": self._model_path(device_type)}

    @staticmethod
    def _split(vectors: List[Tuple[float, ...]]):
        """Contiguous-tail train/test split, same convention as
        tools/ei_capture_upload.py's split_contiguous() -- adjacent
        windows in one continuous recording are highly correlated, so an
        interleaved split would leak train information into test almost
        as badly as the file-level leakage bug this project already found
        once (docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md)."""
        n = len(vectors)
        n_test = max(1, round(n * _TEST_FRACTION)) if n > 1 else 0
        n_train = n - n_test
        return vectors[:n_train], vectors[n_train:]

    @staticmethod
    def _to_csv(vector: Tuple[float, ...], axes: List[str]) -> bytes:
        """Wide single-row CSV -- one header row of real per-column axis
        names (axes, from features.axis_names_for(), same order as
        vector), one data row -- Edge Impulse's documented "single,
        multi-axis reading" format for a non-time-series sample (no
        timestamp column; exactly 1 header + 1 data row). Must match
        _impulse_body()'s declared `axes` exactly (pipeline/ei_client.py)
        since EI names each axis from a CSV's header row."""
        header = ",".join(axes)
        row = ",".join(str(v) for v in vector)
        return f"{header}\n{row}\n".encode("utf-8")

    def upload(self, device_type: str,
               on_progress: Optional[Callable[..., None]] = None) -> dict:
        """docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S8.3/8.4: uploads
        EVERY local recording for this device_type -- never a selection,
        since the pooled scalar-tail baseline (below) needs to fit against
        the whole local population, not an incomplete slice. Wipes the EI
        project first (S8.3's "every Upload is: delete all existing
        samples, then push every local recording fresh" -- eliminates any
        need to track what's already been sent). Same synchronous-error/
        _active_jobs contract as train()/fetch_model(): raises before any
        network call for an unlinked device_type or one with a job already
        running; long-running, callers run it off a background Thread.

        on_progress, if given, is called as on_progress(stage, **extra) --
        "deleting" with no extra, then "uploading" with uploaded/total/
        failures on every batch (api/app.py's _run_ei_job spreads **extra
        into the WS broadcast)."""
        if device_type in self._active_jobs:
            raise EIControllerError(
                f"a {self._active_jobs[device_type]!r} job is already running for {device_type!r}")
        project = ei_projects.get_project(self._projects_path, device_type)
        if project is None:
            raise EIControllerError(f"device_type {device_type!r} isn't linked to Edge Impulse yet")
        api_key, project_id = project["api_key"], project["project_id"]

        def progress(stage: str, **extra) -> None:
            if on_progress:
                on_progress(stage, **extra)

        self._active_jobs[device_type] = "upload"
        try:
            # label -> pooled raw vectors across every local capture for
            # this device_type carrying that label (S8.4 step 1-2). A
            # capture that fails to load, or whose spectral/scalar split
            # disagrees with the others already pooled, is rejected
            # instead of corrupting the pool.
            by_label: Dict[str, List[Tuple[float, ...]]] = {}
            rejected: Dict[str, str] = {}
            spectral_dim: Optional[int] = None
            sensor_config: Optional[FrozenSet[SensorChannel]] = None
            capture_ids = [c["id"] for c in list_captures(self._captures_dir)
                           if c.get("device_type") == device_type]
            for capture_id in capture_ids:
                try:
                    payload = load_capture(self._captures_dir, capture_id)
                except CaptureError as e:
                    rejected[capture_id] = str(e)
                    continue

                this_sensor_config = frozenset(SensorChannel(v) for v in payload["sensor_config"])
                this_spectral_dim = payload["input_dim"] - scalar_dim_for(this_sensor_config)
                if spectral_dim is None:
                    spectral_dim = this_spectral_dim
                    sensor_config = this_sensor_config
                elif this_spectral_dim != spectral_dim:
                    rejected[capture_id] = (
                        f"input shape ({this_spectral_dim} spectral columns) doesn't match "
                        f"this asset class's other recordings ({spectral_dim})")
                    continue

                vectors = [tuple(v) for v in payload["vectors"]]
                by_label.setdefault(payload["label"], []).extend(vectors)

            if not by_label:
                raise EIControllerError(f"no local recordings for device_type {device_type!r}")

            # Real per-column axis names (pipeline/features.py), same order
            # as every accepted capture's vectors -- must match
            # _impulse_body()'s declared axes (pipeline/ei_client.py) for
            # EI to recognize each column, and must be identical across
            # every capture pooled above (already guaranteed: spectral_dim/
            # sensor_config are only set once, from the first accepted
            # capture, and later ones are rejected on any mismatch).
            axis_names = axis_names_for(sensor_config)

            train_by_label: Dict[str, List[Tuple[float, ...]]] = {}
            test_by_label: Dict[str, List[Tuple[float, ...]]] = {}
            for label, vectors in by_label.items():
                train_by_label[label], test_by_label[label] = self._split(vectors)

            # Fit the scalar-tail baseline on the union of TRAIN vectors
            # across every label only (S8.4 step 3) -- unioning test in
            # too would leak validation data into the scaling stats.
            fit_set = [v for vecs in train_by_label.values() for v in vecs]
            columns = list(zip(*(v[spectral_dim:] for v in fit_set))) if fit_set else []
            mu = tuple(statistics.fmean(col) for col in columns)
            sigma = tuple(statistics.pstdev(col) for col in columns)
            ei_scaling.save_scaling(self._scaling_path, device_type, spectral_dim, mu, sigma)

            progress("deleting")
            self._client.delete_all_samples(api_key, project_id)

            total = sum(len(vecs) for vecs in train_by_label.values()) + \
                sum(len(vecs) for vecs in test_by_label.values())
            uploaded_count = 0
            failures: List[str] = [f"{cid}: {reason}" for cid, reason in rejected.items()]
            progress("uploading", uploaded=uploaded_count, total=total, failures=list(failures))

            uploaded: Dict[str, Dict[str, int]] = {}
            progress_lock = threading.Lock()
            for label in by_label:
                counts = {"training": 0, "testing": 0}
                for category, vecs in (("training", train_by_label[label]),
                                        ("testing", test_by_label[label])):
                    standardized = [standardize_scalars(v, spectral_dim, mu, sigma) for v in vecs]
                    samples = [(self._client.timestamped_filename(label, i),
                                self._to_csv(v, axis_names))
                               for i, v in enumerate(standardized)]
                    batches = list(self._client.batched(samples))

                    def send(batch, category=category) -> None:
                        nonlocal uploaded_count
                        try:
                            sent = self._client.upload_samples(api_key, category, label, batch)
                        except EIClientError as e:
                            # Best-effort: one bad batch shouldn't force
                            # redoing the whole (potentially long) upload --
                            # local disk stays the source of truth either
                            # way (S8.6), so a partial remote state just
                            # gets fixed by the next Upload.
                            with progress_lock:
                                failures.append(f"{label}/{category}: {e}")
                                progress("uploading", uploaded=uploaded_count, total=total,
                                          failures=list(failures))
                            return
                        with progress_lock:
                            counts[category] += sent
                            uploaded_count += sent
                            progress("uploading", uploaded=uploaded_count, total=total,
                                      failures=list(failures))

                    with ThreadPoolExecutor(max_workers=_UPLOAD_CONCURRENCY) as pool:
                        list(pool.map(send, batches))
                uploaded[label] = counts

            return {"uploaded": {device_type: uploaded}, "rejected": rejected,
                    "failures": failures}
        finally:
            del self._active_jobs[device_type]
