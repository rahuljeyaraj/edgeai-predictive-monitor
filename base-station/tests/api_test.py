#!/usr/bin/env python3
"""
Milestone 10 verification: connect
a WebSocket test client and confirm live updates arrive; call REST
endpoints and confirm registry changes are reflected (both directly via
GET and by the same change arriving as a WebSocket broadcast, S3.7).

Migrated by the FastAPI migration (see
docs/CLAUDE_CODE_START_PROMPT_fastapi_migration.md Step 4) from raw
sockets speaking hand-rolled RFC6455/http.client to FastAPI's
TestClient, which drives the app in-process without binding a real
socket -- same coverage, no more hand-rolled protocol code in the test
itself.

Run with PYTHONPATH covering base-station/python/ingestion, base-station/python/registry, base-station/python/pipeline,
base-station/python/history, base-station/python/api, base-station/python/monitoring:
    PYTHONPATH=base-station/python/ingestion:base-station/python/registry:base-station/python/pipeline:base-station/python/history:base-station/python/api:base-station/python/monitoring \\
        python3 base-station/tests/api_test.py
"""
import os
import sys
import tempfile

from fastapi.testclient import TestClient

from sensor_frame import FrameSource, SensorFrame
from registry import NodeNotFoundError, NodeStatus, Registry, SensorChannel
from store import HistoryStore
from app import create_app
from commissioning_controller import CommissioningController
from gate import MotorStateGate
from manager import PipelineManager

NODE_ID = "node-1"
DIM = 512
HEALTHY_BINS = tuple(1.0 for _ in range(DIM))


def gate_factory() -> MotorStateGate:
    return MotorStateGate(threshold=0.5, debounce_frames=1)


def frame(node_id, timestamp=0.0) -> SensorFrame:
    return SensorFrame(node_id=node_id, source=FrameSource.SPI, timestamp=timestamp,
                        bins={"accel": HEALTHY_BINS})


class ApiUnderTest:
    """One FastAPI app (REST + WebSocket, api/app.py) driven in-process
    via TestClient, matching how main.py wires api/app.py in production
    (S5: backend API layer). Each test gets its own instance so
    registry/history state never leaks between tests."""

    def __init__(self, tmp_dir: str, node_id=NODE_ID, sensor_config=frozenset({SensorChannel.ACCEL}),
                 min_frames=5, epochs=300):
        registry_path = os.path.join(tmp_dir, "registry.json")
        self.registry = Registry(registry_path)
        self.registry.add(node_id, sensor_config=sensor_config)
        self.history = HistoryStore(os.path.join(tmp_dir, "history.db"))
        self.models_dir = os.path.join(tmp_dir, "models")

        self.commissioning = CommissioningController(
            self.registry, self.models_dir, gate_factory, min_frames=min_frames, epochs=epochs)
        self.manager = PipelineManager(self.registry, gate_factory, history_store=self.history)
        self.app = create_app(self.registry, self.history, self.commissioning, manager=self.manager)
        self._client_cm = TestClient(self.app)
        self.client = self._client_cm.__enter__()  # runs lifespan, so broadcast_threadsafe works

    def request(self, method: str, path: str, body=None):
        resp = self.client.request(method, path, json=body)
        return resp.status_code, resp.json()

    def stop(self):
        self._client_cm.__exit__(None, None, None)


def test_get_nodes_lists_registry_entries(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, body = api.request("GET", "/nodes")
        assert status == 200, (status, body)
        assert NODE_ID in body, body
        assert body[NODE_ID]["sensor_config"] == ["accel"], body
        print("GET /nodes lists registry entries: PASS")
    finally:
        api.stop()


def test_get_node_404_for_unknown(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, body = api.request("GET", "/nodes/no-such-node")
        assert status == 404, (status, body)
        print("GET /nodes/<unknown> returns 404: PASS")
    finally:
        api.stop()


def test_rename_updates_registry_and_broadcasts(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        with api.client.websocket_connect("/ws") as ws:
            status, body = api.request("POST", f"/nodes/{NODE_ID}/rename",
                                        {"display_name": "Front Left Motor"})
            assert status == 200, (status, body)
            assert body["display_name"] == "Front Left Motor", body

            status, body = api.request("GET", f"/nodes/{NODE_ID}")
            assert body["display_name"] == "Front Left Motor", body

            message = ws.receive_json()
            assert message["type"] == "registry", message
            assert message["node_id"] == NODE_ID, message
            assert message["entry"]["display_name"] == "Front Left Motor", message
        print("POST rename updates the registry and broadcasts over WebSocket: PASS")
    finally:
        api.stop()


def test_pause_updates_registry_status(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        api.registry.start_commissioning(NODE_ID)
        api.registry.stop_collecting(NODE_ID)
        api.registry.complete_commissioning(NODE_ID, model_path="unused.pt")

        status, body = api.request("POST", f"/nodes/{NODE_ID}/pause")
        assert status == 200 and body["status"] == "paused", (status, body)
        print("POST pause updates registry status: PASS")
    finally:
        api.stop()


def test_pause_uncommissioned_node_is_409(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, body = api.request("POST", f"/nodes/{NODE_ID}/pause")
        assert status == 409, (status, body)
        assert api.registry.get(NODE_ID).status == NodeStatus.UNCOMMISSIONED
        print("POST pause on an uncommissioned node is 409: PASS")
    finally:
        api.stop()


def test_resume_updates_registry_status(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        api.registry.start_commissioning(NODE_ID)
        api.registry.stop_collecting(NODE_ID)
        api.registry.complete_commissioning(NODE_ID, model_path="unused.pt")

        status, body = api.request("POST", f"/nodes/{NODE_ID}/pause")
        assert status == 200 and body["status"] == "paused", (status, body)

        status, body = api.request("POST", f"/nodes/{NODE_ID}/resume")
        assert status == 200 and body["status"] == "healthy", (status, body)
        print("POST resume updates registry status: PASS")
    finally:
        api.stop()


def test_decommission_removes_node_and_history(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        # Node must be provisioned (has completed commissioning at least
        # once) before it's eligible for decommission -- ApiUnderTest's
        # fixture only adds it (UNCOMMISSIONED).
        api.registry.start_commissioning(NODE_ID)
        api.registry.stop_collecting(NODE_ID)
        api.registry.complete_commissioning(NODE_ID, model_path="unused.pt")
        api.history.record(NODE_ID, 1.0, 0.02, NodeStatus.HEALTHY)

        status, body = api.request("POST", f"/nodes/{NODE_ID}/decommission")
        assert status == 200 and body == {"node_id": NODE_ID, "removed": True}, (status, body)

        status, body = api.request("GET", f"/nodes/{NODE_ID}")
        assert status == 404, (status, body)

        assert NODE_ID not in api.registry.list(), api.registry.list()
        try:
            api.registry.get(NODE_ID)
            assert False, "expected NodeNotFoundError"
        except NodeNotFoundError:
            pass

        assert api.history.query(NODE_ID) == [], api.history.query(NODE_ID)
        print("POST decommission removes the node from the registry and its history: PASS")
    finally:
        api.stop()


def test_decommission_mid_commissioning_node_succeeds(tmp_dir):
    """S3.9 dashboard redesign: the bin icon is always enabled, including
    for a node still mid-commissioning -- decommission must actually
    remove it (and its history), not 409."""
    api = ApiUnderTest(tmp_dir)
    try:
        api.registry.start_commissioning(NODE_ID)
        api.history.record(NODE_ID, 1.0, 0.02, NodeStatus.HEALTHY)

        status, body = api.request("POST", f"/nodes/{NODE_ID}/decommission")
        assert status == 200 and body == {"node_id": NODE_ID, "removed": True}, (status, body)

        assert NODE_ID not in api.registry.list(), api.registry.list()
        assert api.history.query(NODE_ID) == [], api.history.query(NODE_ID)
        print("POST decommission on a mid-commissioning node succeeds and removes it: PASS")
    finally:
        api.stop()


def test_commissioning_start_feed_stop_trains_model(tmp_dir):
    # Small epochs so the background training thread (started by
    # commission/stop, see api/app.py) finishes quickly -- the split into
    # stop_collecting()/train() means completion is no longer synchronous
    # with the REST response, so this test observes it over /ws instead.
    api = ApiUnderTest(tmp_dir, min_frames=5, epochs=10)
    try:
        with api.client.websocket_connect("/ws") as ws:
            status, body = api.request("POST", f"/nodes/{NODE_ID}/commission/start")
            assert status == 200 and body["status"] == "commissioning_collecting", (status, body)
            message = ws.receive_json()  # start's broadcast
            assert message["entry"]["status"] == "commissioning_collecting"

            for i in range(10):
                api.commissioning.feed_frame(frame(NODE_ID, timestamp=float(i)))

            status, body = api.request("POST", f"/nodes/{NODE_ID}/commission/stop")
            assert status == 200, (status, body)
            assert body["status"] == "commissioning_training", body

            message = ws.receive_json()  # stop_collecting's immediate broadcast
            assert message["entry"]["status"] == "commissioning_training", message

            saw_training_progress = False
            final_entry = None
            # Bounded loop: fails loudly instead of hanging the test suite
            # if training never completes.
            for _ in range(200):
                message = ws.receive_json()
                if message["type"] == "training_progress":
                    assert message["node_id"] == NODE_ID, message
                    saw_training_progress = True
                elif message["type"] == "registry" and message["entry"]["status"] == "healthy":
                    final_entry = message["entry"]
                    break
            assert saw_training_progress, "expected at least one training_progress message"
            assert final_entry is not None, "training never completed"
            assert final_entry["model_path"], final_entry
            assert os.path.exists(final_entry["model_path"]), final_entry
        print("commission start -> feed -> stop_collecting -> async train trains and "
              "updates registry+WS with training_progress in between: PASS")
    finally:
        api.stop()


def test_commissioning_stop_without_start_is_409(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, body = api.request("POST", f"/nodes/{NODE_ID}/commission/stop")
        assert status == 409, (status, body)
        print("commission/stop without an active session returns 409: PASS")
    finally:
        api.stop()


def test_commissioning_double_start_is_409(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, _ = api.request("POST", f"/nodes/{NODE_ID}/commission/start")
        assert status == 200
        status, body = api.request("POST", f"/nodes/{NODE_ID}/commission/start")
        assert status == 409, (status, body)
        print("commission/start while already training returns 409: PASS")
    finally:
        api.stop()


def test_history_endpoint_returns_recorded_scores(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        api.history.record(NODE_ID, 1.0, 0.02, NodeStatus.HEALTHY)
        api.history.record(NODE_ID, 2.0, 0.03, NodeStatus.HEALTHY)

        status, body = api.request("GET", f"/nodes/{NODE_ID}/history")
        assert status == 200, (status, body)
        assert [r["timestamp"] for r in body] == [1.0, 2.0], body
        assert [r["anomaly_score"] for r in body] == [0.02, 0.03], body
        print("GET /nodes/<id>/history returns recorded scores: PASS")
    finally:
        api.stop()


def test_websocket_broadcast_reaches_connected_client(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        with api.client.websocket_connect("/ws") as ws:
            from app import broadcast_threadsafe
            broadcast_threadsafe(api.app, {"type": "score", "node_id": NODE_ID, "anomaly_score": 0.42})
            message = ws.receive_json()
            assert message == {"type": "score", "node_id": NODE_ID, "anomaly_score": 0.42}, message
        print("live push over WebSocket reaches a connected client: PASS")
    finally:
        api.stop()


def main():
    tmp_dir = tempfile.mkdtemp(prefix="api_test_")

    test_get_nodes_lists_registry_entries(tempfile.mkdtemp(dir=tmp_dir))
    test_get_node_404_for_unknown(tempfile.mkdtemp(dir=tmp_dir))
    test_rename_updates_registry_and_broadcasts(tempfile.mkdtemp(dir=tmp_dir))
    test_pause_updates_registry_status(tempfile.mkdtemp(dir=tmp_dir))
    test_pause_uncommissioned_node_is_409(tempfile.mkdtemp(dir=tmp_dir))
    test_resume_updates_registry_status(tempfile.mkdtemp(dir=tmp_dir))
    test_decommission_removes_node_and_history(tempfile.mkdtemp(dir=tmp_dir))
    test_decommission_mid_commissioning_node_succeeds(tempfile.mkdtemp(dir=tmp_dir))
    test_commissioning_start_feed_stop_trains_model(tempfile.mkdtemp(dir=tmp_dir))
    test_commissioning_stop_without_start_is_409(tempfile.mkdtemp(dir=tmp_dir))
    test_commissioning_double_start_is_409(tempfile.mkdtemp(dir=tmp_dir))
    test_history_endpoint_returns_recorded_scores(tempfile.mkdtemp(dir=tmp_dir))
    test_websocket_broadcast_reaches_connected_client(tempfile.mkdtemp(dir=tmp_dir))

    print("RESULT: PASS - REST endpoints reflect registry/commissioning changes, and "
          "live updates push to connected WebSocket clients")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
