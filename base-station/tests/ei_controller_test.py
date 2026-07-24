#!/usr/bin/env python3
"""
EIController verification (api/ei_controller.py, docs/
EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S4): link() idempotency,
upload()'s device_type/link-state rejection rules, the
standardize-with-node-baseline-vs-raw-fallback branch (the train/serve
skew this round exists to close -- see EIController._standardize's
docstring), and the contiguous-tail train/test split -- all against a
FakeEiClient (dependency-injected, same "hand-rolled fake, no mock
library" convention as api_test.py's FakeTelegramBot), no real network.

Run with PYTHONPATH covering base-station/python/ingestion, base-station/python/registry, base-station/python/pipeline, base-station/python/api:
    PYTHONPATH=base-station/python/ingestion:base-station/python/registry:base-station/python/pipeline:base-station/python/api \\
        python3 base-station/tests/ei_controller_test.py
"""
import os
import sys
import tempfile

import ei_client
from ei_client import EITotpRequiredError
from ei_controller import EIController, EIControllerError
from ei_projects import get_project
from capture import CaptureSession
from gate import MotorStateGate
from registry import Registry, SensorChannel
from sensor_frame import FrameSource, SensorFrame

NODE_A = "node-a"
NODE_B = "node-b"
DIM = 128  # SensorChannel.MIC's spectral bin count (registry._DIM_BY_CHANNEL)
MIC_SCALARS = {"rms_mic": 1.0, "kurtosis_mic": 1.0, "std_mic": 1.0,
               "peak_mic": 1.0, "crest_factor_mic": 1.0, "skewness_mic": 1.0}


class FakeEiClient:
    """Duck-types ei_client's module-level API -- login/create_project/
    create_impulse/set_nn_config/upload_samples are faked and recorded;
    batched()/timestamped_filename() are pure helpers with no network, so
    delegated straight to the real ei_client rather than reimplemented."""

    def __init__(self, totp_code=None):
        self.calls = []
        self.uploads = []
        self._totp_code = totp_code
        self._next_id = 100
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

    def create_impulse(self, api_key, project_id, input_dim):
        self.calls.append(("create_impulse", api_key, project_id, input_dim))
        return 3

    def set_nn_config(self, api_key, project_id, learn_id, input_dim, num_classes):
        self.calls.append(
            ("set_nn_config", api_key, project_id, learn_id, input_dim, num_classes))

    def upload_samples(self, api_key, category, label, samples):
        self.uploads.append(
            {"api_key": api_key, "category": category, "label": label, "samples": samples})
        return len(samples)


def frame(node_id, mic_bins):
    return SensorFrame(node_id=node_id, source=FrameSource.SPI, timestamp=0.0,
                        bins={"mic": mic_bins}, scalars=MIC_SCALARS)


def running_frame(node_id):
    return frame(node_id, tuple(3.0 + 0.001 * i for i in range(DIM)))  # RMS well over threshold


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


def decode_csv(csv_bytes):
    lines = csv_bytes.decode("utf-8").strip().split("\n")[1:]  # drop header row
    return tuple(float(line.split(",")[1]) for line in lines)


def new_env():
    tmp_dir = tempfile.mkdtemp()
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    projects_path = os.path.join(tmp_dir, "ei_projects.json")
    captures_dir = os.path.join(tmp_dir, "captures")
    return registry, projects_path, captures_dir


def test_link_creates_project_on_first_call():
    registry, projects_path, captures_dir = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.MIC}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, client=client)

    result = controller.link("motor001", "me@example.com", "hunter2")

    assert result["linked"] is True
    assert [c[0] for c in client.calls] == \
        ["login", "create_project", "create_impulse", "set_nn_config"]
    stored = get_project(projects_path, "motor001")
    assert stored["project_id"] == result["project_id"]
    print("link() creates project+impulse+NN-config on first call: PASS")


def test_link_is_idempotent():
    registry, projects_path, captures_dir = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.MIC}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, client=client)

    first = controller.link("motor001", "me@example.com", "hunter2")
    second = controller.link("motor001", "someone-else@example.com", "different")

    assert second == {"linked": True, "project_id": first["project_id"]}
    assert len(client.calls) == 4, "second link() must not touch the client at all"
    print("link() is a no-op for an already-linked device_type: PASS")


def test_link_raises_for_device_type_with_no_node():
    registry, projects_path, captures_dir = new_env()
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, client=client)

    try:
        controller.link("ghost_type", "me@example.com", "hunter2")
        raise AssertionError("expected EIControllerError")
    except EIControllerError:
        pass
    assert client.calls == [], "must fail before making any EI call"
    print("link() rejects a device_type with no current node: PASS")


def test_link_propagates_totp_required():
    registry, projects_path, captures_dir = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.MIC}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient(totp_code="654321")
    controller = EIController(registry, projects_path, captures_dir, client=client)

    try:
        controller.link("motor001", "me@example.com", "hunter2")
        raise AssertionError("expected EITotpRequiredError")
    except EITotpRequiredError:
        pass
    assert get_project(projects_path, "motor001") is None, \
        "a failed link() must not leave a half-created project behind"
    print("link() surfaces EITotpRequiredError without saving a project: PASS")


def test_unlink_clears_project_and_allows_relink():
    registry, projects_path, captures_dir = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.MIC}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, client=client)
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
    registry, projects_path, captures_dir = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.MIC}))
    registry.set_device_type(NODE_A, "motor001")
    registry.add(NODE_B, sensor_config=frozenset({SensorChannel.MIC}))
    registry.set_device_type(NODE_B, "pump002")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, client=client)

    controller.link("motor001", "me@example.com", "hunter2")

    assert controller.status() == {"motor001": True, "pump002": False}
    print("status() reports linked/not-linked per current device_type: PASS")

    assert controller.project_ids() == {"motor001": 100}
    print("project_ids() reports the linked device_type's EI project_id only: PASS")

    create_call = next(c for c in client.calls if c[0] == "create_project")
    assert create_call[2] == "edgeai-predictive-monitor-motor001", create_call
    print("link() names the EI project 'edgeai-predictive-monitor-<device_type>': PASS")


def test_upload_standardizes_using_node_baseline():
    registry, projects_path, captures_dir = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.MIC}))
    registry.set_device_type(NODE_A, "motor001")
    entry = registry.get(NODE_A)
    entry.scalar_mu = (0.5,) * 6
    entry.scalar_sigma = (2.0,) * 6  # (1.0 - 0.5) / 2.0 == 0.25 expected tail
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    capture_id = save_capture(registry, captures_dir, NODE_A, "bearing_fault", count=3)

    result = controller.upload([capture_id])

    assert result["warnings"] == [], result["warnings"]
    assert result["rejected"] == {}
    counts = result["uploaded"]["motor001"]["bearing_fault"]
    assert counts["training"] + counts["testing"] == 3
    all_samples = [s for u in client.uploads for s in u["samples"]]
    assert len(all_samples) == 3
    for _filename, csv_bytes in all_samples:
        vector = decode_csv(csv_bytes)
        assert len(vector) == DIM + 6
        tail = vector[DIM:]
        assert all(abs(v - 0.25) < 1e-9 for v in tail), tail
    print("upload() standardizes the scalar tail against the node's commissioned baseline: PASS")


def test_upload_falls_back_to_raw_when_uncommissioned():
    registry, projects_path, captures_dir = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.MIC}))
    registry.set_device_type(NODE_A, "motor001")
    # No scalar_mu/scalar_sigma set -- node was never commissioned.
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    capture_id = save_capture(registry, captures_dir, NODE_A, "bearing_fault", count=3)

    result = controller.upload([capture_id])

    assert len(result["warnings"]) == 1, result["warnings"]
    assert "never commissioned" in result["warnings"][0] or "no commissioned baseline" in result["warnings"][0]
    all_samples = [s for u in client.uploads for s in u["samples"]]
    for _filename, csv_bytes in all_samples:
        tail = decode_csv(csv_bytes)[DIM:]
        assert all(v == 1.0 for v in tail), tail  # raw MIC_SCALARS value, untouched
    print("upload() falls back to a raw scalar tail (with a warning) for an "
          "uncommissioned node, instead of blocking: PASS")


def test_upload_rejects_capture_with_no_device_type():
    registry, projects_path, captures_dir = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.MIC}))
    # device_type deliberately left unset.
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, client=client)
    capture_id = save_capture(registry, captures_dir, NODE_A, "bearing_fault", count=1)

    result = controller.upload([capture_id])

    assert capture_id in result["rejected"]
    assert result["uploaded"] == {}
    assert client.uploads == []
    print("upload() rejects a capture whose node has no device_type: PASS")


def test_upload_rejects_capture_for_unlinked_device_type():
    registry, projects_path, captures_dir = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.MIC}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, client=client)
    # link() deliberately never called for motor001.
    capture_id = save_capture(registry, captures_dir, NODE_A, "bearing_fault", count=1)

    result = controller.upload([capture_id])

    assert capture_id in result["rejected"]
    assert "linked" in result["rejected"][capture_id]
    print("upload() rejects a capture for a device_type with no EI project yet: PASS")


def test_upload_pools_same_label_across_captures_before_splitting():
    registry, projects_path, captures_dir = new_env()
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.MIC}))
    registry.set_device_type(NODE_A, "motor001")
    client = FakeEiClient()
    controller = EIController(registry, projects_path, captures_dir, client=client)
    controller.link("motor001", "me@example.com", "hunter2")
    id_1 = save_capture(registry, captures_dir, NODE_A, "bearing_fault", count=2)
    id_2 = save_capture(registry, captures_dir, NODE_A, "bearing_fault", count=2)

    result = controller.upload([id_1, id_2])

    counts = result["uploaded"]["motor001"]["bearing_fault"]
    # 4 pooled vectors, test_fraction=0.2 -> n_test=max(1, round(4*0.2))=1.
    assert counts == {"training": 3, "testing": 1}, counts
    print("upload() pools every selected capture sharing a label before the "
          "contiguous train/test split, not one split per file: PASS")


def main():
    test_link_creates_project_on_first_call()
    test_link_is_idempotent()
    test_link_raises_for_device_type_with_no_node()
    test_link_propagates_totp_required()
    test_unlink_clears_project_and_allows_relink()
    test_status_reflects_linked_device_types()
    test_upload_standardizes_using_node_baseline()
    test_upload_falls_back_to_raw_when_uncommissioned()
    test_upload_rejects_capture_with_no_device_type()
    test_upload_rejects_capture_for_unlinked_device_type()
    test_upload_pools_same_label_across_captures_before_splitting()
    print("RESULT: PASS - EIController links/uploads correctly against a "
          "faked ei_client, with no real network")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
