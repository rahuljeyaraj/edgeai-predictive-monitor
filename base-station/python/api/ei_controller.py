"""EIController -- API-layer orchestration for Edge Impulse Round A
("Upload"), per docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S4. Mirrors
CaptureController/CommissioningController's role: a thin lifecycle shim
between REST routes and the ei_client/ei_projects/capture/features
plumbing below it.

Both link() and upload() are plain blocking calls (no threading.Thread)
-- FastAPI already runs `def` route handlers in a worker thread (api/app.py's
own docstring), and both are bounded request/response sequences, not a
long-running job the way commissioning's run_training() is (that pattern
is what Round B's actual EI training job will need).

`client` is dependency-injected (defaults to the real ei_client module) so
tests can pass a fake with the same function names and never touch the
network -- same shape the project already uses for gate_factory callables
elsewhere.
"""
from typing import Dict, List, Optional, Tuple

import ei_client
import ei_projects
from capture import CaptureError, list_captures, load_capture
from features import scalar_dim_for, standardize_scalars
from registry import Registry, SensorChannel

_TEST_FRACTION = 0.2


class EIControllerError(Exception):
    pass


class EIController:
    def __init__(self, registry: Registry, projects_path: str, captures_dir: str,
                 client=ei_client):
        self._registry = registry
        self._projects_path = projects_path
        self._captures_dir = captures_dir
        self._client = client

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

    def _input_dim_for(self, device_type: str) -> int:
        for entry in self._registry.list().values():
            if entry.device_type == device_type:
                return entry.input_dim
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

        input_dim = self._input_dim_for(device_type)
        jwt = self._client.login(username, password, totp)
        project_id, api_key = self._client.create_project(
            jwt, f"edgeai-predictive-monitor-{device_type}")
        learn_id = self._client.create_impulse(api_key, project_id, input_dim)
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

    def _standardize(self, node_id: Optional[str], payload: dict,
                      vectors: List[Tuple[float, ...]]) -> Tuple[List[Tuple[float, ...]], bool]:
        """Mirrors pipeline/inference.py:120-122's exact standardization
        step (this node's own commissioned baseline) -- capture.py stores
        the *raw* build_feature_vector() output, but live inference always
        standardizes before scoring, so uploading raw vectors would train
        on a different distribution than the classifier sees at runtime.
        Falls back to raw (told apart by the returned bool) when the node
        was never commissioned or no longer exists, rather than blocking
        the whole upload -- S2 explicitly allows capturing on an
        uncommissioned node."""
        entry = self._registry.list().get(node_id) if node_id else None
        if entry is None or entry.scalar_mu is None:
            return vectors, False
        sensor_config = frozenset(SensorChannel(v) for v in payload["sensor_config"])
        spectral_dim = payload["input_dim"] - scalar_dim_for(sensor_config)
        standardized = [standardize_scalars(v, spectral_dim, entry.scalar_mu, entry.scalar_sigma)
                         for v in vectors]
        return standardized, True

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
    def _to_csv(vector: Tuple[float, ...]) -> bytes:
        """Same "timestamp,feature" one-column-per-row convention as
        ei_capture_upload.py's write_ei_sample() -- tells EI's ingestion
        parser this whole file is one windowed sample, not len(vector)
        separate one-row samples. 1ms/row step, arbitrary but consistent
        (these rows are feature-vector positions, not a real time axis)."""
        rows = "".join(f"{i:.6f},{v}\n" for i, v in enumerate(vector))
        return f"timestamp,feature\n{rows}".encode("utf-8")

    def upload(self, capture_ids: List[str]) -> dict:
        projects = ei_projects.load_projects(self._projects_path)
        # device_type -> label -> pooled vectors across every selected
        # capture carrying that label (timestamp order preserved by
        # capture_ids' own order -- the frontend lists newest-first, but
        # doesn't matter here: pooling then splitting the *combined*
        # sequence is what ei_capture_upload.py's per-label convention does
        # too, just per-file there instead of per-selection here).
        pooled: Dict[str, Dict[str, List[Tuple[float, ...]]]] = {}
        warnings: List[str] = []
        rejected: Dict[str, str] = {}

        for capture_id in capture_ids:
            try:
                payload = load_capture(self._captures_dir, capture_id)
            except CaptureError as e:
                rejected[capture_id] = str(e)
                continue

            device_type = payload.get("device_type")
            if not device_type:
                rejected[capture_id] = "no device_type set for this recording's node"
                continue
            if device_type not in projects:
                rejected[capture_id] = (
                    f"device_type {device_type!r} isn't linked to Edge Impulse yet")
                continue

            vectors = [tuple(v) for v in payload["vectors"]]
            standardized, was_standardized = self._standardize(
                payload.get("node_id"), payload, vectors)
            if not was_standardized:
                warnings.append(
                    f"{capture_id}: no commissioned baseline for node "
                    f"{payload.get('node_id')!r} -- uploaded with a raw "
                    "(non-standardized) scalar tail")

            label = payload["label"]
            pooled.setdefault(device_type, {}).setdefault(label, []).extend(standardized)

        uploaded: Dict[str, Dict[str, Dict[str, int]]] = {}
        for device_type, by_label in pooled.items():
            api_key = projects[device_type]["api_key"]
            uploaded[device_type] = {}
            for label, vectors in by_label.items():
                train_vectors, test_vectors = self._split(vectors)
                counts = {"training": 0, "testing": 0}
                for category, vecs in (("training", train_vectors), ("testing", test_vectors)):
                    samples = [(self._client.timestamped_filename(label, i), self._to_csv(v))
                               for i, v in enumerate(vecs)]
                    for batch in self._client.batched(samples):
                        counts[category] += self._client.upload_samples(
                            api_key, category, label, batch)
                uploaded[device_type][label] = counts

        return {"uploaded": uploaded, "warnings": warnings, "rejected": rejected}
