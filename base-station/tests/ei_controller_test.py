#!/usr/bin/env python3
"""
EIController verification (api/ei_controller.py, docs/
EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S4/S8, reworked 2026-07-26): link()
idempotency, upload()'s device_type-scoped gather (fits its pooled
per-device-type scalar-tail baseline (S8.4) from EVERY local recording, but
sends only the caller-selected subset -- no more wipe-before-upload, no more
"always everything"), the uploading progress reporting, and the
contiguous-tail train/test split -- all against a FakeEiClient
(dependency-injected, same "hand-rolled fake, no mock library" convention
as api_test.py's FakeTelegramBot), no real network.

Run with PYTHONPATH covering base-station/python/ingestion, base-station/python/registry, base-station/python/pipeline, base-station/python/api:
    PYTHONPATH=base-station/python/ingestion:base-station/python/registry:base-station/python/pipeline:base-station/python/api \\
        python3 base-station/tests/ei_controller_test.py
"""
import os
import sys
import tempfile
import time

import ei_client
from ei_client import EIClientError, EITotpRequiredError
from ei_controller import EIController, EIControllerError
from ei_projects import get_project
from ei_scaling import get_scaling, save_scaling
from capture import CaptureSession
from gate import MotorStateGate
from registry import Registry, SensorChannel
from sensor_frame import FrameSource, SensorFrame

NODE_A = "node-a"
NODE_B = "node-b"
DIM = 128  # SensorChannel.ACCEL_X's spectral bin count (registry._DIM_BY_CHANNEL)
# accel_x as the generic single channel throughout this file: mic is muted
# by default (features.MUTED_CHANNELS zeroes its columns in every vector
# build_feature_vector produces), so a mic-only fixture would make every
# saved capture all zeros and the pooled mu/sigma this file asserts on
# meaningless. Nothing here is channel-specific.
ACCEL_X_SCALARS = {"rms_x": 1.0, "kurtosis_x": 1.0, "std_x": 1.0,
                   "peak_x": 1.0, "crest_factor_x": 1.0, "skewness_x": 1.0}


class FakeEiClient:
    """Duck-types ei_client's module-level API -- login/create_project/
    create_impulse/set_nn_config/upload_samples/delete_all_samples/
    generate_features/train/build_model/wait_for_job/download_model/
    extract_tflite are faked and recorded; batched()/timestamped_filename()
    are pure helpers with no network, so delegated straight to the real
    ei_client rather than reimplemented. upload_samples() records into both
    `self.uploads` (full sample payloads, for content assertions) AND
    `self.calls` (just the call name, for ordering assertions against
    delete_all_samples -- S8.3's "wipe, then upload" contract)."""

    def __init__(self, totp_code=None, fail_job=None):
        self.calls = []
        self.uploads = []
        self._totp_code = totp_code
        self._next_id = 100
        # Job name ("generate_features"/"train"/"build_model") to make
        # wait_for_job() raise for -- lets tests exercise EIController's
        # error path without a real failing HTTP call.
        self._fail_job = fail_job
        self.batched = ei_client.batched
        self.timestamped_filename = ei_client.timestamped_filename

    def login(self, username, password, totp=None):
        self.calls.append(("login", username, password, totp))
        if self._totp_code is not None and totp != self._totp_code:
            raise EITotpRequiredError("ERR_TOTP_TOKEN_IS_REQUIRED")
        return "jwt-fake"

    def create_project(self, jwt_token, project_name):
        self.calls.append(("create_project", jwt_token, project_name))
        project_id = self._next_id
        self._next_id += 1
        return project_id, f"ei_key_{project_id}"

    def create_impulse(self, api_key, project_id, input_dim, axes):
        self.calls.append(("create_impulse", api_key, project_id, input_dim, axes))
        return 3

    def set_nn_config(self, api_key, project_id, learn_id, input_dim, num_classes):
        self.calls.append(
            ("set_nn_config", api_key, project_id, learn_id, input_dim, num_classes))

    def delete_all_samples(self, api_key, project_id):
        self.calls.append(("delete_all_samples", api_key, project_id))

    def upload_samples(self, api_key, category, label, samples):
        self.calls.append(("upload_samples", category, label, len(samples)))
        self.uploads.append(
            {"api_key": api_key, "category": category, "label": label, "samples": samples})
        return len(samples)

    def generate_features(self, api_key, project_id):
        self.calls.append(("generate_features", api_key, project_id))
        return 201

    def train(self, api_key, project_id):
        self.calls.append(("train", api_key, project_id))
        return 202

    def build_model(self, api_key, project_id):
        self.calls.append(("build_model", api_key, project_id))
        return 203

    def wait_for_job(self, api_key, project_id, job_id, on_poll=None):
        self.calls.append(("wait_for_job", api_key, project_id, job_id))
        if on_poll:
            on_poll()
        job_name = {201: "generate_features", 202: "train", 203: "build_model"}.get(job_id)
        if job_name == self._fail_job:
            raise EIClientError(f"job {job_id} failed (faked)")

    def download_model(self, api_key, project_id):
        self.calls.append(("download_model", api_key, project_id))
        return b"fake-zip-bytes"

    def extract_tflite(self, zip_bytes):
        self.calls.append(("extract_tflite", zip_bytes))
        return b"fake-tflite-bytes"


class SlowFakeEiClient(FakeEiClient):
    """Adds a fixed per-call delay to upload_samples() -- simulates real
    network/EI-server latency per HTTP round-trip, so a test can tell
    concurrent batch dispatch (S8's fix for real-account uploads being
    "very slow") apart from serial one-batch-at-a-time dispatch by wall
    clock time, without needing a real network."""

    def __init__(self, delay_s, **kwargs):
        super().__init__(**kwargs)
        self._delay_s = delay_s

    def upload_samples(self, api_key, category, label, samples):
        time.sleep(self._delay_s)
        return super().upload_samples(api_key, category, label, samples)


def frame(node_id, accel_x_bins):
    return SensorFrame(node_id=node_id, source=FrameSource.SPI, timestamp=0.0,
                        bins={"accel_x": accel_x_bins}, scalars=ACCEL_X_SCALARS)


def running_frame(node_id):
    return frame(node_id, tuple(3.0 + 0.001 * i for i in range(DIM)))  # RMS well over threshold


def scalar_frame(node_id, scalar_value):
    """Like running_frame(), but every scalar column is set to
    scalar_value instead of the fixed ACCEL_X_SCALARS -- lets a test control
    exactly what a pooled mu/sigma should come out to."""
    bins = tuple(3.0 + 0.001 * i for i in range(DIM))
    scalars = {name: scalar_value for name in ACCEL_X_SCALARS}
    return SensorFrame(node_id=node_id, source=FrameSource.SPI, timestamp=0.0,
                        bins={"accel_x": bins}, scalars=scalars)


def new_gate():
    return MotorStateGate(threshold=1.0, debounce_frames=1)


def save_capture(registry, captures_dir, node_id, label, count=3):
    session = CaptureSession(registry, captures_dir, node_id, new_gate())
    session.start()
    for _ in range(count):
        session.feed_frame(running_frame(node_id))
    session.stop()
    path = session.save(label)
    return os.path.relpath(path, captures_dir).replace(os.sep, "/")


def save_capture_with_scalar(registry, captures_dir, node_id, label, scalar_value, count=4):
    session = CaptureSession(registry, captures_dir, node_id, new_gate())
    session.start()
    for _ in range(count):
        session.feed_frame(scalar_frame(node_id, scalar_value))
    session.stop()
    path = session.save(label)
    return os.path.relpath(path, captures_dir).replace(os.sep, "/")


def decode_csv(csv_bytes):
    # Wide single-row format -- one header of real axis names, one data
    # row -- mirrors _to_csv()'s real shape (Edge Impulse's documented
    # "single, multi-axis reading" CSV). Header content isn't checked
    # here (see test_link_passes_real_axis_names_to_create_impulse for
    # that), only that it lines up 1:1 with the data row.
    lines = csv_bytes.decode("utf-8").strip().split("\n")
    assert len(lines) == 2, f"expected 1 header row + 1 data row, got {len(lines)} lines"
    header, data = lines[0].split(","), lines[1].split(",")
    assert len(header) == len(data), (header, data)
    return tuple(float(v) for v in data)


def new_env():
    tmp_dir = tempfile.mkdtemp()
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    projects_path = os.path.join(tmp_dir, "ei_projects.json")
    captures_dir = os.path.join(tmp_dir, "captures")
    models_dir = os.path.join(tmp_dir, "ei_models")
    scaling_path = os.path.join(tmp_dir, "ei_scaling.json")
    return registry, projects_path, captures_dir, models_dir, scaling_path


def test_link_creates_project_on_first_call():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)

    result = controller.link("motor001", "me@example.com", "hunter2")

    assert result["linked"] is True
    assert result["project_name"] == "edgeai-predictive-monitor-motor001", result
    assert [c[0] for c in client.calls] == \
        ["login", "create_project", "create_impulse", "set_nn_config"]
    stored = get_project(projects_path, "motor001")
    assert stored["project_id"] == result["project_id"]
    assert stored["project_name"] == result["project_name"]
    print("link() creates project+impulse+NN-config on first call: PASS")


def test_link_passes_real_axis_names_to_create_impulse():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)

    controller.link("motor001", "me@example.com", "hunter2")

    create_call = next(c for c in client.calls if c[0] == "create_impulse")
    axes = create_call[4]
    assert len(axes) == DIM + 6, axes
    assert axes[0] == "accel_x_bin0", axes[0]
    assert axes[DIM - 1] == f"accel_x_bin{DIM - 1}", axes[DIM - 1]
    assert axes[DIM:] == ["accel_x_rms", "accel_x_kurtosis", "accel_x_std", "accel_x_peak",
                           "accel_x_crest_factor", "accel_x_skewness"], axes[DIM:]
    print("link() passes real per-column axis names (accel_x_binN, accel_x_rms, ...) "
          "to create_impulse(), not generic feature_N ones: PASS")


def test_link_is_idempotent():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)

    first = controller.link("motor001", "me@example.com", "hunter2")
    second = controller.link("motor001", "someone-else@example.com", "different")

    assert second == {"linked": True, "project_id": first["project_id"],
                       "project_name": first["project_name"]}
    assert len(client.calls) == 4, "second link() must not touch the client at all"
    print("link() is a no-op for an already-linked device_type: PASS")


def test_link_raises_for_device_type_with_no_node():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)

    try:
        controller.link("ghost_type", "me@example.com", "hunter2")
        raise AssertionError("expected EIControllerError")
    except EIControllerError:
        pass
    assert client.calls == [], "must fail before making any EI call"
    print("link() rejects a device_type with no current node: PASS")


def test_link_propagates_totp_required():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient(totp_code="654321")
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)

    try:
        controller.link("motor001", "me@example.com", "hunter2")
        raise AssertionError("expected EITotpRequiredError")
    except EITotpRequiredError:
        pass
    assert get_project(projects_path, "motor001") is None, \
        "a failed link() must not leave a half-created project behind"
    print("link() surfaces EITotpRequiredError without saving a project: PASS")


def test_unlink_clears_project_and_allows_relink():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    first = controller.link("motor001", "me@example.com", "hunter2")

    result = controller.unlink("motor001")

    assert result == {"removed": True}
    assert get_project(projects_path, "motor001") is None
    assert controller.unlink("motor001") == {"removed": False}, \
        "unlinking an already-unlinked device_type is a no-op, not an error"

    second = controller.link("motor001", "me@example.com", "hunter2")
    assert second["project_id"] != first["project_id"], \
        "relinking after unlink() must create a brand new project (the " \
        "old one may already be gone from EI Studio's side, e.g. deleted " \
        "by hand), not silently reuse the stale project_id"
    print("unlink() drops the saved project so a later link() creates a "
          "fresh one instead of treating it as still-linked: PASS")


def test_status_reflects_linked_device_types():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    registry.add(NODE_B, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_B, "pump002")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)

    controller.link("motor001", "me@example.com", "hunter2")

    assert controller.status() == {"motor001": True, "pump002": False}
    print("status() reports linked/not-linked per current device_type: PASS")

    assert controller.project_ids() == {"motor001": 100}
    print("project_ids() reports the linked device_type's EI project_id only: PASS")

    assert controller.project_names() == {"motor001": "edgeai-predictive-monitor-motor001"}
    print("project_names() reports the linked device_type's EI project name only: PASS")

    create_call = next(c for c in client.calls if c[0] == "create_project")
    assert create_call[2] == "edgeai-predictive-monitor-motor001", create_call
    print("link() names the EI project 'edgeai-predictive-monitor-<device_type>': PASS")


def test_upload_standardizes_using_pooled_device_type_baseline():
    # Two DIFFERENT nodes, same device_type -- proves the baseline is
    # pooled per-device-type (S8.4), not per-node the way it used to be.
    # Each label's scalar value is homogeneous across its own 4 frames, so
    # which single frame the 80/20 split shaves off as "test" can't shift
    # that label's contribution to the pooled train-only fit -- the pooled
    # mean/stdev below is fully deterministic.
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    registry.add(NODE_B, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_B, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    healthy_id = save_capture_with_scalar(registry, captures_dir, NODE_A, "healthy", 1.0, count=4)
    fault_id = save_capture_with_scalar(registry, captures_dir, NODE_B, "bearing_fault", 5.0, count=4)

    result = controller.upload("motor001", [healthy_id, fault_id])

    assert result["rejected"] == {}, result
    # train-only pool: 3x healthy@1.0 + 3x bearing_fault@5.0 -> mean 3.0,
    # population stdev 2.0, on every one of the 6 scalar columns.
    scaling = get_scaling(scaling_path, "motor001")
    assert scaling["spectral_dim"] == DIM, scaling
    assert all(abs(m - 3.0) < 1e-9 for m in scaling["mu"]), scaling
    assert all(abs(s - 2.0) < 1e-9 for s in scaling["sigma"]), scaling

    all_samples = [s for u in client.uploads for s in u["samples"]]
    assert len(all_samples) == 8, all_samples
    for entry in client.uploads:
        expected_tail = -1.0 if entry["label"] == "healthy" else 1.0
        for _filename, csv_bytes in entry["samples"]:
            tail = decode_csv(csv_bytes)[DIM:]
            assert all(abs(v - expected_tail) < 1e-9 for v in tail), (entry["label"], tail)
    print("upload() standardizes the scalar tail against a pooled "
          "per-device-type baseline (train-only, across every node/label), "
          "not a per-node one: PASS")


def test_upload_only_includes_captures_for_the_given_device_type():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    registry.add(NODE_B, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_B, "pump002")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    capture_id = save_capture(registry, captures_dir, NODE_A, "bearing_fault", count=2)
    save_capture(registry, captures_dir, NODE_B, "bearing_fault", count=2)  # pump002 -- must stay out

    result = controller.upload("motor001", [capture_id])

    counts = result["uploaded"]["motor001"]["bearing_fault"]
    assert counts["training"] + counts["testing"] == 2, counts
    print("upload() only pools local recordings for the requested device_type: PASS")


def test_upload_pools_same_label_across_captures_before_splitting():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    id1 = save_capture(registry, captures_dir, NODE_A, "bearing_fault", count=2)
    id2 = save_capture(registry, captures_dir, NODE_A, "bearing_fault", count=2)

    result = controller.upload("motor001", [id1, id2])

    counts = result["uploaded"]["motor001"]["bearing_fault"]
    # 4 pooled vectors, test_fraction=0.2 -> n_test=max(1, round(4*0.2))=1.
    assert counts == {"training": 3, "testing": 1}, counts
    print("upload() pools every local capture sharing a label before the "
          "contiguous train/test split, not one split per file: PASS")


def test_upload_never_wipes_project_and_reports_uploading_progress():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    capture_id = save_capture(registry, captures_dir, NODE_A, "bearing_fault", count=3)
    ticks = []

    result = controller.upload(
        "motor001", [capture_id],
        on_progress=lambda stage, **extra: ticks.append((stage, extra)))

    assert ticks and all(stage == "uploading" for stage, _extra in ticks), ticks
    last_extra = ticks[-1][1]
    assert last_extra["uploaded"] == last_extra["total"] == 3, ticks
    assert last_extra["failures"] == [], ticks
    assert result["failures"] == []

    assert "delete_all_samples" not in [c[0] for c in client.calls], \
        "upload() no longer wipes the project first (2026-07-26 -- would " \
        "destroy previously-uploaded, currently-unselected samples)"
    assert controller.job_state() == {}, "job must be cleared once upload() returns"
    print("upload() no longer wipes the project first, and reports "
          "uploading(uploaded/total/failures) progress: PASS")


def test_upload_sends_only_selected_recordings_but_fits_baseline_from_all():
    # Two different labels, each internally homogeneous (like
    # test_upload_standardizes_using_pooled_device_type_baseline above) so
    # the pooled train-only baseline is deterministic regardless of split
    # position -- proves the baseline is fit from BOTH labels' local
    # recordings even though only one label's capture is selected for
    # upload.
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    healthy_id = save_capture_with_scalar(registry, captures_dir, NODE_A, "healthy", 1.0, count=4)
    save_capture_with_scalar(registry, captures_dir, NODE_A, "bearing_fault", 5.0, count=4)
    ticks = []

    result = controller.upload(
        "motor001", [healthy_id],
        on_progress=lambda stage, **extra: ticks.append((stage, extra)))

    # Same pooled baseline as when every local recording is selected
    # (mirrors test_upload_standardizes_using_pooled_device_type_baseline's
    # math: train-only pool of 3x healthy@1.0 + 3x bearing_fault@5.0).
    scaling = get_scaling(scaling_path, "motor001")
    assert all(abs(m - 3.0) < 1e-9 for m in scaling["mu"]), scaling
    assert all(abs(s - 2.0) < 1e-9 for s in scaling["sigma"]), scaling

    # But only the selected ("healthy") capture's vectors were sent.
    assert result["uploaded"]["motor001"]["bearing_fault"] == {"training": 0, "testing": 0}, result
    healthy_counts = result["uploaded"]["motor001"]["healthy"]
    assert healthy_counts["training"] + healthy_counts["testing"] == 4, healthy_counts
    assert all(u["label"] == "healthy" for u in client.uploads), client.uploads

    last_uploading = [t for t in ticks if t[0] == "uploading"][-1]
    assert last_uploading[1]["uploaded"] == last_uploading[1]["total"] == 4, ticks
    print("upload() sends only the caller-selected recordings but still "
          "fits the normalization baseline from every local recording: PASS")


def test_upload_raises_when_no_recordings_selected():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    save_capture(registry, captures_dir, NODE_A, "bearing_fault", count=1)
    calls_before = len(client.calls)

    try:
        controller.upload("motor001", [])
        raise AssertionError("expected EIControllerError")
    except EIControllerError:
        pass
    assert len(client.calls) == calls_before, "must fail before making any EI call"
    print("upload() rejects an empty selection synchronously: PASS")


def test_upload_sends_batches_concurrently():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = SlowFakeEiClient(delay_s=0.3)
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    # 60 frames, one label -> _split gives 48 train / 12 test; batched at
    # UPLOAD_BATCH_SIZE=25 makes training 2 batches + testing 1 batch.
    capture_id = save_capture(registry, captures_dir, NODE_A, "bearing_fault", count=60)

    start = time.monotonic()
    result = controller.upload("motor001", [capture_id])
    elapsed = time.monotonic() - start

    counts = result["uploaded"]["motor001"]["bearing_fault"]
    assert counts["training"] + counts["testing"] == 60, counts
    # Serial (one HTTP round-trip at a time, the pre-fix behavior real
    # users hit as "very slow for this small data") would take
    # >= 3 batches * 0.3s = 0.9s. Concurrent dispatch (S8's fix, up to
    # _UPLOAD_CONCURRENCY in flight per label/category) keeps the 2
    # training batches running together, so the whole upload finishes
    # well under that.
    assert elapsed < 0.75, f"batches ran one-at-a-time, not concurrently: {elapsed:.2f}s"
    print("upload() sends a label/category's batches concurrently, not "
          "one HTTP round-trip at a time: PASS")


def test_upload_raises_for_unlinked_device_type():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    # link() deliberately never called for motor001.
    capture_id = save_capture(registry, captures_dir, NODE_A, "bearing_fault", count=1)

    try:
        controller.upload("motor001", [capture_id])
        raise AssertionError("expected EIControllerError")
    except EIControllerError:
        pass
    assert client.calls == [] and client.uploads == [], "must fail before making any EI call"
    print("upload() rejects a device_type with no linked EI project: PASS")


def test_upload_raises_when_no_local_recordings():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    # No captures saved at all.

    try:
        controller.upload("motor001", ["ghost-label/ghost.json"])
        raise AssertionError("expected EIControllerError")
    except EIControllerError:
        pass
    print("upload() rejects a device_type with no local recordings: PASS")


def test_upload_rejects_concurrent_job_for_same_device_type():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    capture_id = save_capture(registry, captures_dir, NODE_A, "bearing_fault", count=1)
    controller._active_jobs["motor001"] = "fetch"  # simulate a fetch already in flight
    calls_before = len(client.calls)

    try:
        controller.upload("motor001", [capture_id])
        raise AssertionError("expected EIControllerError")
    except EIControllerError:
        pass
    assert len(client.calls) == calls_before, \
        "must reject before starting a second job for the same device_type"
    print("upload() rejects a device_type that already has a job running: PASS")


def test_train_runs_generate_features_then_train_in_order():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    stages = []

    result = controller.train("motor001", on_progress=stages.append)

    assert result == {"trained": True}
    call_names = [c[0] for c in client.calls if c[0] in ("generate_features", "train", "wait_for_job")]
    assert call_names == ["generate_features", "wait_for_job", "train", "wait_for_job"], call_names
    assert stages == ["generating_features", "generating_features", "training", "training"], stages
    assert controller.job_state() == {}, "job must be cleared once train() returns"
    print("train() runs generate_features -> wait -> train -> wait, in order, "
          "reporting each stage via on_progress: PASS")


def test_train_raises_for_unlinked_device_type():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)

    try:
        controller.train("ghost_type")
        raise AssertionError("expected EIControllerError")
    except EIControllerError:
        pass
    assert client.calls == [], "must fail before making any EI call"
    print("train() rejects a device_type with no linked EI project: PASS")


def test_train_propagates_job_failure_and_clears_active_job():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient(fail_job="train")
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")

    try:
        controller.train("motor001")
        raise AssertionError("expected EIClientError")
    except EIClientError:
        pass
    assert controller.job_state() == {}, \
        "a failed job must still clear _active_jobs (the finally: block), not strand it"
    print("train() propagates a failed EI job and still clears job_state(): PASS")


def test_train_rejects_concurrent_job_for_same_device_type():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    controller._active_jobs["motor001"] = "fetch"  # simulate a fetch already in flight
    calls_before = len(client.calls)

    try:
        controller.train("motor001")
        raise AssertionError("expected EIControllerError")
    except EIControllerError:
        pass
    assert len(client.calls) == calls_before, \
        "must reject before starting a second job for the same device_type"
    print("train() rejects a device_type that already has a job running: PASS")


def test_fetch_model_builds_downloads_and_saves_tflite_file():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    save_capture(registry, captures_dir, NODE_A, "bearing")
    save_capture(registry, captures_dir, NODE_A, "healthy")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    stages = []

    result = controller.fetch_model("motor001", on_progress=stages.append)

    expected_path = os.path.join(models_dir, "motor001.tflite")
    assert result == {"fetched": True, "model_path": expected_path}, result
    assert os.path.isfile(expected_path)
    with open(expected_path, "rb") as f:
        assert f.read() == b"fake-tflite-bytes"
    assert controller.labels_for("motor001") == ["bearing", "healthy"], controller.labels_for("motor001")
    call_names = [c[0] for c in client.calls
                  if c[0] in ("build_model", "wait_for_job", "download_model", "extract_tflite")]
    assert call_names == ["build_model", "wait_for_job", "download_model", "extract_tflite"], call_names
    assert stages == ["building", "building", "downloading"], stages
    assert controller.model_status()["motor001"] is not None
    print("fetch_model() builds -> waits -> downloads -> extracts -> saves "
          "<models_dir>/<device_type>.tflite: PASS")


def test_fetch_model_raises_for_unlinked_device_type():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)

    try:
        controller.fetch_model("ghost_type")
        raise AssertionError("expected EIControllerError")
    except EIControllerError:
        pass
    assert client.calls == [], "must fail before making any EI call"
    print("fetch_model() rejects a device_type with no linked EI project: PASS")


def test_model_status_reports_none_before_any_fetch():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)

    assert controller.model_status() == {"motor001": None}
    print("model_status() reports None for a device_type with no fetched model yet: PASS")


def test_labels_for_reports_none_before_any_fetch():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=FakeEiClient())
    assert controller.labels_for("motor001") is None
    print("labels_for() reports None for a device_type with no fetched model yet: PASS")


def test_fetch_model_rejects_device_type_with_no_local_recordings():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")

    try:
        controller.fetch_model("motor001")
        raise AssertionError("expected EIControllerError")
    except EIControllerError as e:
        assert "no local recordings" in str(e), e
    assert controller.model_status()["motor001"] is None, \
        "must not save a .tflite file when labels can't be determined"
    print("fetch_model() rejects a linked device_type with no local recordings "
          "(can't determine class labels): PASS")


def test_rename_device_type_moves_project_scaling_and_model():
    # Asset-class rename (api/app.py's /device_types/rename) -- everything
    # EIController owns for old_device_type must land under new_device_type
    # so a renamed class keeps its Studio link, its fitted normalization
    # baseline, and its already-fetched model instead of stranding them
    # under a name nothing points to anymore.
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.MIC}))
    registry.set_device_type(NODE_A, "motor001")
    save_capture(registry, captures_dir, NODE_A, "bearing")
    save_capture(registry, captures_dir, NODE_A, "healthy")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    controller.fetch_model("motor001")
    save_scaling(scaling_path, "motor001", spectral_dim=DIM, mu=(1.0,), sigma=(0.5,))

    moved = controller.rename_device_type("motor001", "conveyor001")

    assert moved is True
    assert get_project(projects_path, "motor001") is None
    assert get_project(projects_path, "conveyor001") is not None
    assert get_scaling(scaling_path, "motor001") is None
    assert get_scaling(scaling_path, "conveyor001") == {
        "spectral_dim": DIM, "mu": [1.0], "sigma": [0.5]}
    assert not os.path.isfile(os.path.join(models_dir, "motor001.tflite"))
    assert os.path.isfile(os.path.join(models_dir, "conveyor001.tflite"))
    assert controller.labels_for("motor001") is None
    assert controller.labels_for("conveyor001") == ["bearing", "healthy"]
    print("rename_device_type() moves the linked project, scaling baseline, "
          "and fetched model onto the new name: PASS")


def test_rename_device_type_nothing_to_move_returns_false():
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path,
                               client=FakeEiClient())
    assert controller.rename_device_type("ghost_type", "conveyor001") is False
    print("rename_device_type() with nothing on disk for old_device_type returns False: PASS")


def test_known_device_types_includes_project_scaling_and_model_only_entries():
    # /device_types/rename's collision check needs to see a device_type
    # even if no node currently carries it -- e.g. its only node was
    # reassigned/decommissioned but the Studio project is still linked.
    registry, projects_path, captures_dir, models_dir, scaling_path = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.MIC}))
    registry.set_device_type(NODE_A, "motor001")
    save_capture(registry, captures_dir, NODE_A, "bearing")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, models_dir, scaling_path, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    controller.fetch_model("motor001")
    save_scaling(scaling_path, "scaling_only_type", spectral_dim=DIM, mu=(1.0,), sigma=(0.5,))

    known = controller.known_device_types()
    assert known == ["motor001", "scaling_only_type"], known
    print("known_device_types() reports every device_type with a project, "
          "scaling baseline, or fetched model, live node or not: PASS")


def main():
    test_link_creates_project_on_first_call()
    test_link_passes_real_axis_names_to_create_impulse()
    test_link_is_idempotent()
    test_link_raises_for_device_type_with_no_node()
    test_link_propagates_totp_required()
    test_unlink_clears_project_and_allows_relink()
    test_status_reflects_linked_device_types()
    test_upload_standardizes_using_pooled_device_type_baseline()
    test_upload_only_includes_captures_for_the_given_device_type()
    test_upload_pools_same_label_across_captures_before_splitting()
    test_upload_never_wipes_project_and_reports_uploading_progress()
    test_upload_sends_only_selected_recordings_but_fits_baseline_from_all()
    test_upload_raises_when_no_recordings_selected()
    test_upload_sends_batches_concurrently()
    test_upload_raises_for_unlinked_device_type()
    test_upload_raises_when_no_local_recordings()
    test_upload_rejects_concurrent_job_for_same_device_type()
    test_train_runs_generate_features_then_train_in_order()
    test_train_raises_for_unlinked_device_type()
    test_train_propagates_job_failure_and_clears_active_job()
    test_train_rejects_concurrent_job_for_same_device_type()
    test_fetch_model_builds_downloads_and_saves_tflite_file()
    test_fetch_model_raises_for_unlinked_device_type()
    test_fetch_model_rejects_device_type_with_no_local_recordings()
    test_model_status_reports_none_before_any_fetch()
    test_labels_for_reports_none_before_any_fetch()
    test_rename_device_type_moves_project_scaling_and_model()
    test_rename_device_type_nothing_to_move_returns_false()
    test_known_device_types_includes_project_scaling_and_model_only_entries()
    print("RESULT: PASS - EIController links/uploads/trains/fetches correctly "
          "against a faked ei_client, with no real network")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
