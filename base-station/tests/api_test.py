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
base-station/python/history, base-station/python/api, base-station/python/monitoring, base-station/python/alerts:
    PYTHONPATH=base-station/python/ingestion:base-station/python/registry:base-station/python/pipeline:base-station/python/history:base-station/python/api:base-station/python/monitoring:base-station/python/alerts \\
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
from capture_controller import CaptureController
from gate import MotorStateGate
from manager import PipelineManager
from alert_store import AlertStore
import ei_client
from ei_client import EIClientError, EITotpRequiredError
from ei_controller import EIController

NODE_ID = "node-1"
DIM = 128  # SensorChannel.MIC's spectral bin count (registry._DIM_BY_CHANNEL)
HEALTHY_BINS = tuple(1.0 for _ in range(DIM))

# Fixed -- these tests are about the REST/WebSocket/commissioning-workflow
# layer, not the scalar tail's own signal.
MIC_SCALARS = {"rms_mic": 1.0, "kurtosis_mic": 1.0, "std_mic": 1.0,
               "peak_mic": 1.0, "crest_factor_mic": 1.0, "skewness_mic": 1.0}


def gate_factory() -> MotorStateGate:
    return MotorStateGate(threshold=0.5, debounce_frames=1)


def frame(node_id, timestamp=0.0) -> SensorFrame:
    return SensorFrame(node_id=node_id, source=FrameSource.SPI, timestamp=timestamp,
                        bins={"mic": HEALTHY_BINS}, scalars=MIC_SCALARS)


class FakeTelegramBot:
    """Duck-types just enough of the real arduino:telegram_bot brick
    (add_command/send_message) for api/app.py's routes to treat Telegram
    alerts as "configured" -- the routes only ever check `telegram_bot is
    not None` and never call these, since the actual /start-token-redeem
    and status-change wiring is alerts/telegram_alerts.py's own concern
    (see tests/telegram_alerts_test.py), not api/app.py's REST layer."""

    def add_command(self, command, callback, description=""):
        pass

    def send_message(self, chat_id, text):
        return True


class FakeEiClient:
    """Same role as FakeTelegramBot above, for EIController's injected
    `client` (api/ei_controller.py, docs/
    EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S4) -- lets the REST layer's
    connect()/upload() routes be exercised end-to-end with no real network.
    Fuller behavioral coverage (idempotency, standardization branch, split)
    lives in tests/ei_controller_test.py; this file only needs enough to
    confirm the routes themselves wire through correctly."""

    def __init__(self, totp_code=None, fail_job=None):
        self.uploads = []
        self.calls = []
        self._totp_code = totp_code
        self._next_id = 100
        # Job name ("generate_features"/"train"/"build_model") to make
        # wait_for_job() raise for, so a route test can exercise the
        # "ei_progress" error broadcast without a real failing HTTP call.
        self._fail_job = fail_job
        self.batched = ei_client.batched
        self.timestamped_filename = ei_client.timestamped_filename

    def login(self, username, password, totp=None):
        if self._totp_code is not None and totp != self._totp_code:
            raise EITotpRequiredError("ERR_TOTP_TOKEN_IS_REQUIRED")
        return "jwt-fake"

    def create_project(self, jwt_token, project_name):
        project_id = self._next_id
        self._next_id += 1
        return project_id, f"ei_key_{project_id}"

    def create_impulse(self, api_key, project_id, input_dim, axes):
        return 3

    def set_nn_config(self, api_key, project_id, learn_id, input_dim, num_classes):
        pass

    def delete_all_samples(self, api_key, project_id):
        self.calls.append(("delete_all_samples", api_key, project_id))
        if self._fail_job == "delete_all_samples":
            raise EIClientError("delete-all failed (faked)")

    def upload_samples(self, api_key, category, label, samples):
        self.calls.append(("upload_samples", category, label, len(samples)))
        self.uploads.append(
            {"api_key": api_key, "category": category, "label": label, "samples": samples})
        return len(samples)

    # Round B (S4 steps 5-9) -- fixed job ids per step name are enough for
    # wait_for_job() below to know which one (if any) should fail; no real
    # network, no real delay (poll_interval/timeout aren't accepted here at
    # all -- EIController.train()/fetch_model() never pass them, they're
    # ei_client.wait_for_job()'s own kwargs with defaults, faked away
    # entirely here).
    def generate_features(self, api_key, project_id):
        return 1 if self._fail_job == "generate_features" else 201

    def train(self, api_key, project_id):
        return 1 if self._fail_job == "train" else 202

    def build_model(self, api_key, project_id):
        return 1 if self._fail_job == "build_model" else 203

    def wait_for_job(self, api_key, project_id, job_id, on_poll=None):
        if on_poll:
            on_poll()
        if job_id == 1:
            raise EIClientError("job failed (faked)")

    def download_model(self, api_key, project_id):
        return b"fake-zip-bytes"

    def extract_tflite(self, zip_bytes):
        return b"fake-tflite-bytes"


class ApiUnderTest:
    """One FastAPI app (REST + WebSocket, api/app.py) driven in-process
    via TestClient, matching how main.py wires api/app.py in production
    (S5: backend API layer). Each test gets its own instance so
    registry/history state never leaks between tests."""

    def __init__(self, tmp_dir: str, node_id=NODE_ID, sensor_config=frozenset({SensorChannel.MIC}),
                 min_frames=5, epochs=300, telegram_bot=None, ei_totp_code=None, ei_fail_job=None):
        registry_path = os.path.join(tmp_dir, "registry.json")
        self.registry = Registry(registry_path)
        self.registry.add(node_id, sensor_config=sensor_config)
        self.history = HistoryStore(os.path.join(tmp_dir, "history.db"))
        self.models_dir = os.path.join(tmp_dir, "models")
        self.alert_store = AlertStore(os.path.join(tmp_dir, "alerts.json"))

        self.commissioning = CommissioningController(
            self.registry, self.models_dir, gate_factory, min_frames=min_frames, epochs=epochs)
        self.captures_dir = os.path.join(tmp_dir, "captures")
        self.capture = CaptureController(self.registry, self.captures_dir, gate_factory)
        self.manager = PipelineManager(self.registry, gate_factory, history_store=self.history)
        self.ei_client = FakeEiClient(totp_code=ei_totp_code, fail_job=ei_fail_job)
        self.ei_models_dir = os.path.join(tmp_dir, "ei_models")
        self.ei_scaling_path = os.path.join(tmp_dir, "ei_scaling.json")
        self.ei = EIController(
            self.registry, os.path.join(tmp_dir, "ei_projects.json"), self.captures_dir,
            self.ei_models_dir, self.ei_scaling_path, client=self.ei_client)
        self.app = create_app(self.registry, self.history, self.commissioning, self.capture,
                               manager=self.manager,
                               alert_store=self.alert_store, telegram_bot=telegram_bot, ei=self.ei)
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
        assert body[NODE_ID]["sensor_config"] == ["mic"], body
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
                                        {"device_name": "Front Left Motor"})
            assert status == 200, (status, body)
            assert body["device_name"] == "Front Left Motor", body

            status, body = api.request("GET", f"/nodes/{NODE_ID}")
            assert body["device_name"] == "Front Left Motor", body

            message = ws.receive_json()
            assert message["type"] == "registry", message
            assert message["node_id"] == NODE_ID, message
            assert message["entry"]["device_name"] == "Front Left Motor", message
        print("POST rename updates the registry and broadcasts over WebSocket: PASS")
    finally:
        api.stop()


def test_device_type_updates_registry_and_broadcasts(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        with api.client.websocket_connect("/ws") as ws:
            status, body = api.request("POST", f"/nodes/{NODE_ID}/device_type",
                                        {"device_type": "Conveyor Motor"})
            assert status == 200, (status, body)
            assert body["device_type"] == "Conveyor Motor", body

            message = ws.receive_json()
            assert message["type"] == "registry", message
            assert message["entry"]["device_type"] == "Conveyor Motor", message

            status, body = api.request("GET", "/device_types")
            assert status == 200, (status, body)
            assert body["device_types"] == ["Conveyor Motor"], body

            # Blank clears it back to unassigned.
            status, body = api.request("POST", f"/nodes/{NODE_ID}/device_type",
                                        {"device_type": ""})
            assert status == 200, (status, body)
            assert body["device_type"] is None, body
        print("POST device_type updates the registry, broadcasts, and blank clears it: PASS")
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


def test_recommissioning_clears_stale_history(tmp_dir):
    """A recommission overwrites the model in place (S6 open question #6)
    rather than versioning it -- the dashboard's anomaly-score history
    (durable in history/store.py, separate from the registry entry) must
    be wiped alongside it, or the chart that reappears once training
    finishes would show the *previous* model's scores merged in as if
    they were current (frontend/charts.js's UNCOMMISSIONED_STATUSES
    gating hides the chart entirely while status is mid-recommission, then
    expects a clean slate once it reappears)."""
    api = ApiUnderTest(tmp_dir, min_frames=5, epochs=10)
    try:
        # Commission once already (bypassing the REST flow -- that's
        # covered by the test above) and leave a stale score behind, as if
        # this node had been running healthy for a while already.
        api.registry.start_commissioning(NODE_ID)
        api.registry.stop_collecting(NODE_ID)
        api.registry.complete_commissioning(NODE_ID, model_path="unused.pt")
        api.history.record(NODE_ID, 1.0, 99.0, NodeStatus.FAULT)
        assert len(api.history.query(NODE_ID)) == 1, api.history.query(NODE_ID)

        with api.client.websocket_connect("/ws") as ws:
            status, body = api.request("POST", f"/nodes/{NODE_ID}/commission/start")
            assert status == 200 and body["status"] == "commissioning_collecting", (status, body)
            ws.receive_json()  # start's broadcast

            for i in range(10):
                api.commissioning.feed_frame(frame(NODE_ID, timestamp=float(i)))

            status, body = api.request("POST", f"/nodes/{NODE_ID}/commission/stop")
            assert status == 200, (status, body)
            ws.receive_json()  # stop_collecting's immediate broadcast

            final_entry = None
            for _ in range(200):
                message = ws.receive_json()
                if message["type"] == "registry" and message["entry"]["status"] == "healthy":
                    final_entry = message["entry"]
                    break
            assert final_entry is not None, "training never completed"

        assert api.history.query(NODE_ID) == [], api.history.query(NODE_ID)
        print("recommissioning clears the previous model's stale anomaly-score "
              "history: PASS")
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


def test_capture_start_feed_stop_save_persists_labeled_batch(tmp_dir):
    # Capture is independent of commissioning end to end (S2, 2026-07-24
    # decision) -- this node is never commissioned at all. target_frames=20
    # (well above the 5 fed) so this test exercises the manual-stop path,
    # not auto-stop -- see test_capture_auto_stops_at_target_frames below
    # for that one.
    api = ApiUnderTest(tmp_dir)
    try:
        with api.client.websocket_connect("/ws") as ws:
            status, body = api.request("POST", f"/nodes/{NODE_ID}/capture/start",
                                        {"target_frames": 20})
            assert status == 200 and body["state"] == "capturing", (status, body)
            message = ws.receive_json()
            assert message == {"type": "capture", "node_id": NODE_ID, "state": "capturing",
                                "collected": 0, "target_frames": 20}, message

            for i in range(5):
                api.capture.feed_frame(frame(NODE_ID, timestamp=float(i)))

            status, body = api.request("GET", f"/nodes/{NODE_ID}")
            assert body["capture_progress"] == {"state": "capturing", "collected": 5,
                                                 "target_frames": 20}, body
            assert body["status"] == "uncommissioned", body

            status, body = api.request("POST", f"/nodes/{NODE_ID}/capture/stop")
            assert status == 200 and body["collected"] == 5, (status, body)
            message = ws.receive_json()
            assert message == {"type": "capture", "node_id": NODE_ID, "state": "stopped",
                                "collected": 5, "target_frames": 20}, message

            status, body = api.request("GET", f"/nodes/{NODE_ID}")
            assert body["capture_progress"] == {"state": "stopped", "collected": 5,
                                                 "target_frames": 20}, body

            status, body = api.request("POST", f"/nodes/{NODE_ID}/capture/save",
                                        {"label": "Bearing Fault"})
            assert status == 200 and body["saved"] is True, (status, body)
            message = ws.receive_json()
            assert message == {"type": "capture", "node_id": NODE_ID, "state": "idle",
                                "collected": 0, "target_frames": None}, message

            # Absent once idle again, same "nothing to show" contract as
            # commissioning_progress.
            status, body = api.request("GET", f"/nodes/{NODE_ID}")
            assert "capture_progress" not in body, body
            assert body["status"] == "uncommissioned", body

            status, body = api.request("GET", "/captures/labels")
            assert status == 200 and body["labels"] == ["bearing_fault"], (status, body)
        print("capture start -> feed -> stop -> save persists a labeled batch, broadcasts "
              "over WS, never touches NodeStatus: PASS")
    finally:
        api.stop()


def test_capture_auto_stops_at_target_frames(tmp_dir):
    # The auto-stop transition happens inside CaptureController.feed_frame
    # (ingestion thread), not any REST handler -- this confirms it still
    # broadcasts over WS and is visible via GET /nodes without an explicit
    # capture/stop call ("we know how many frames a good batch needs, let
    # the count drive it," 2026-07-24).
    api = ApiUnderTest(tmp_dir)
    try:
        with api.client.websocket_connect("/ws") as ws:
            status, body = api.request("POST", f"/nodes/{NODE_ID}/capture/start",
                                        {"target_frames": 3})
            assert status == 200, (status, body)
            ws.receive_json()  # start's own broadcast

            for i in range(3):
                api.capture.feed_frame(frame(NODE_ID, timestamp=float(i)))

            message = ws.receive_json()
            assert message == {"type": "capture", "node_id": NODE_ID, "state": "stopped",
                                "collected": 3, "target_frames": 3}, message

            status, body = api.request("GET", f"/nodes/{NODE_ID}")
            assert body["capture_progress"] == {"state": "stopped", "collected": 3,
                                                 "target_frames": 3}, body

            # capture/stop is no longer valid -- already auto-stopped.
            status, body = api.request("POST", f"/nodes/{NODE_ID}/capture/stop")
            assert status == 409, (status, body)
        print("capture auto-stops at target_frames and broadcasts over WS with no "
              "explicit capture/stop call: PASS")
    finally:
        api.stop()


def test_capture_stop_without_start_is_409(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, body = api.request("POST", f"/nodes/{NODE_ID}/capture/stop")
        assert status == 409, (status, body)
        print("capture/stop without an active session returns 409: PASS")
    finally:
        api.stop()


def test_capture_save_before_stop_is_409(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, _ = api.request("POST", f"/nodes/{NODE_ID}/capture/start")
        assert status == 200
        status, body = api.request("POST", f"/nodes/{NODE_ID}/capture/save", {"label": "healthy"})
        assert status == 409, (status, body)
        print("capture/save before capture/stop returns 409: PASS")
    finally:
        api.stop()


def test_capture_cancel_discards_batch(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, _ = api.request("POST", f"/nodes/{NODE_ID}/capture/start")
        assert status == 200
        api.capture.feed_frame(frame(NODE_ID))
        status, body = api.request("POST", f"/nodes/{NODE_ID}/capture/cancel")
        assert status == 200 and body["state"] == "idle", (status, body)

        status, body = api.request("GET", f"/nodes/{NODE_ID}")
        assert "capture_progress" not in body, body

        # Session is reusable immediately after a cancel.
        status, _ = api.request("POST", f"/nodes/{NODE_ID}/capture/start")
        assert status == 200
        print("capture/cancel discards the batch and the session is reusable after: PASS")
    finally:
        api.stop()


def test_capture_start_unknown_node_is_404(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, body = api.request("POST", "/nodes/no-such-node/capture/start")
        assert status == 404, (status, body)
        print("capture/start for an unknown node returns 404: PASS")
    finally:
        api.stop()


def test_decommission_discards_capture_session(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, _ = api.request("POST", f"/nodes/{NODE_ID}/capture/start")
        assert status == 200
        status, body = api.request("POST", f"/nodes/{NODE_ID}/decommission")
        assert status == 200, (status, body)
        assert api.capture.progress(NODE_ID) is None, "capture session should be discarded"
        print("decommission discards any in-flight capture session: PASS")
    finally:
        api.stop()


def test_captures_list_rename_delete(tmp_dir):
    # Classifier tab's sample table (docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md
    # S3): GET /captures lists every saved batch, POST /captures/rename
    # moves one into a new label bucket, POST /captures/delete removes a
    # selection -- all fleet-wide, not scoped under /nodes/{node_id}.
    api = ApiUnderTest(tmp_dir)
    try:
        status, _ = api.request("POST", f"/nodes/{NODE_ID}/capture/start")
        assert status == 200
        api.capture.feed_frame(frame(NODE_ID))
        status, _ = api.request("POST", f"/nodes/{NODE_ID}/capture/stop")
        assert status == 200
        status, _ = api.request("POST", f"/nodes/{NODE_ID}/capture/save", {"label": "bearing_fault"})
        assert status == 200

        status, body = api.request("GET", "/captures")
        assert status == 200 and len(body["captures"]) == 1, (status, body)
        entry = body["captures"][0]
        assert entry["label"] == "bearing_fault", entry
        assert entry["node_id"] == NODE_ID, entry
        assert entry["frame_count"] == 1, entry
        capture_id = entry["id"]

        status, body = api.request("POST", "/captures/rename", {"id": capture_id, "label": "Loose Mount"})
        assert status == 200, (status, body)
        new_id = body["id"]
        assert new_id.startswith("loose_mount/"), body

        status, body = api.request("GET", "/captures")
        assert status == 200 and len(body["captures"]) == 1, (status, body)
        assert body["captures"][0]["id"] == new_id, body
        assert body["captures"][0]["label"] == "loose_mount", body

        status, body = api.request("POST", "/captures/delete", {"ids": [new_id]})
        assert status == 200 and body["deleted"] == 1, (status, body)

        status, body = api.request("GET", "/captures")
        assert status == 200 and body["captures"] == [], (status, body)
        print("GET/POST /captures list/rename/delete saved batches: PASS")
    finally:
        api.stop()


def test_captures_rename_unknown_id_is_400(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, body = api.request("POST", "/captures/rename",
                                    {"id": "healthy/does-not-exist.json", "label": "x"})
        assert status == 400, (status, body)
        print("POST /captures/rename for an unknown id returns 400: PASS")
    finally:
        api.stop()


def test_captures_rename_bulk(tmp_dir):
    # Classifier tab's "Edit label (N)" bulk action (docs/
    # EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S8.7.1) -- one new label
    # applied to every selected recording in a single call, best-effort
    # against a mix of valid and unknown ids (same shape as /captures/delete).
    api = ApiUnderTest(tmp_dir)
    try:
        for label in ("bearing_fault", "healthy"):
            status, _ = api.request("POST", f"/nodes/{NODE_ID}/capture/start")
            assert status == 200
            api.capture.feed_frame(frame(NODE_ID))
            status, _ = api.request("POST", f"/nodes/{NODE_ID}/capture/stop")
            assert status == 200
            status, body = api.request("POST", f"/nodes/{NODE_ID}/capture/save", {"label": label})
            assert status == 200, (status, body)

        status, body = api.request("GET", "/captures")
        ids = [c["id"] for c in body["captures"]]
        assert len(ids) == 2, body

        status, body = api.request("POST", "/captures/rename_bulk",
                                    {"ids": ids + ["healthy/does-not-exist.json"], "label": "Loose Mount"})
        assert status == 200 and body == {"renamed": 2}, (status, body)

        status, body = api.request("GET", "/captures")
        assert status == 200 and len(body["captures"]) == 2, body
        assert all(c["label"] == "loose_mount" for c in body["captures"]), body
        print("POST /captures/rename_bulk relabels every selected recording in "
              "one call, best-effort against an unknown id mixed in: PASS")
    finally:
        api.stop()


def test_ei_status_reports_unlinked_by_default(tmp_dir):
    # Round A "Upload" (docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S4) --
    # a node's device_type shows up in GET /classifier/ei/status as
    # unlinked until POST /classifier/ei/link has run for it.
    api = ApiUnderTest(tmp_dir)
    try:
        status, body = api.request("POST", f"/nodes/{NODE_ID}/device_type", {"device_type": "motor001"})
        assert status == 200, (status, body)

        status, body = api.request("GET", "/classifier/ei/status")
        assert status == 200 and body == {
            "device_types": {"motor001": False}, "project_ids": {},
            "models": {"motor001": None}, "jobs": {}}, (status, body)
        print("GET /classifier/ei/status reports an assigned-but-unlinked device_type: PASS")
    finally:
        api.stop()


def test_ei_link_creates_project_then_upload_pushes_samples(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, body = api.request("POST", f"/nodes/{NODE_ID}/device_type", {"device_type": "motor001"})
        assert status == 200, (status, body)

        status, body = api.request("POST", "/classifier/ei/link",
                                    {"device_type": "motor001", "username": "me@example.com",
                                     "password": "hunter2"})
        assert status == 200 and body["linked"] is True, (status, body)

        status, body = api.request("GET", "/classifier/ei/status")
        assert body == {"device_types": {"motor001": True},
                         "project_ids": {"motor001": 100},
                         "models": {"motor001": None}, "jobs": {}}, body

        status, _ = api.request("POST", f"/nodes/{NODE_ID}/capture/start")
        assert status == 200
        api.capture.feed_frame(frame(NODE_ID))
        status, _ = api.request("POST", f"/nodes/{NODE_ID}/capture/stop")
        assert status == 200
        status, body = api.request("POST", f"/nodes/{NODE_ID}/capture/save", {"label": "bearing_fault"})
        assert status == 200, (status, body)

        # Upload runs as a background job now (S8.3 -- always every local
        # recording for the device_type, wiping the project first), same
        # "observe over /ws, not the REST response" shape as train/fetch.
        with api.client.websocket_connect("/ws") as ws:
            status, body = api.request("POST", "/classifier/ei/upload", {"device_type": "motor001"})
            assert status == 200 and body == {"started": True}, (status, body)

            stages = []
            for _ in range(50):
                message = ws.receive_json()
                if message["type"] == "ei_progress" and message["device_type"] == "motor001" \
                        and message["action"] == "upload":
                    stages.append(message["stage"])
                    if message["stage"] in ("done", "error"):
                        break
            assert stages[0] == "deleting", stages
            assert "uploading" in stages, stages
            assert stages[-1] == "done", stages

        call_names = [c[0] for c in api.ei_client.calls]
        assert call_names.index("delete_all_samples") < call_names.index("upload_samples"), call_names
        assert len(api.ei_client.uploads) >= 1
        print("POST /classifier/ei/link + /classifier/ei/upload wipe the EI "
              "project then push every local recording for that device_type "
              "through to the (faked) client, streaming deleting -> "
              "uploading -> done over /ws: PASS")
    finally:
        api.stop()


def test_ei_link_totp_required_returns_400_marker(tmp_dir):
    api = ApiUnderTest(tmp_dir, ei_totp_code="654321")
    try:
        status, body = api.request("POST", f"/nodes/{NODE_ID}/device_type", {"device_type": "motor001"})
        assert status == 200, (status, body)

        status, body = api.request("POST", "/classifier/ei/link",
                                    {"device_type": "motor001", "username": "me@example.com",
                                     "password": "hunter2"})
        # api/app.py's exception_handler rewrites HTTPException.detail into
        # a {"error": ...} response body, not FastAPI's default {"detail": ...}.
        assert status == 400 and body["error"] == {"totp_required": True}, (status, body)

        status, body = api.request("GET", "/classifier/ei/status")
        assert body == {"device_types": {"motor001": False}, "project_ids": {},
                         "models": {"motor001": None}, "jobs": {}}, \
            "a failed link() must not report the device_type as linked"
        print("POST /classifier/ei/link surfaces a totp_required marker "
              "(not a generic error) when EI's login needs a 2FA code: PASS")
    finally:
        api.stop()


def test_ei_unlink_clears_project_and_allows_relink(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, body = api.request("POST", f"/nodes/{NODE_ID}/device_type", {"device_type": "motor001"})
        assert status == 200, (status, body)
        status, body = api.request("POST", "/classifier/ei/link",
                                    {"device_type": "motor001", "username": "me@example.com",
                                     "password": "hunter2"})
        assert status == 200 and body["linked"] is True, (status, body)

        status, body = api.request("POST", "/classifier/ei/unlink", {"device_type": "motor001"})
        assert status == 200 and body == {"removed": True}, (status, body)

        status, body = api.request("GET", "/classifier/ei/status")
        assert body == {"device_types": {"motor001": False}, "project_ids": {},
                         "models": {"motor001": None}, "jobs": {}}, body

        # Covers "I deleted the project in EI Studio, now what" -- unlinking
        # locally lets a fresh link() create a brand new project rather than
        # being stuck with a dead project_id and no way to recreate it.
        status, body = api.request("POST", "/classifier/ei/link",
                                    {"device_type": "motor001", "username": "me@example.com",
                                     "password": "hunter2"})
        assert status == 200 and body["linked"] is True, (status, body)
        print("POST /classifier/ei/unlink clears the saved project so a "
              "device_type can be linked again: PASS")
    finally:
        api.stop()


def test_ei_fetch_model_over_ws(tmp_dir):
    # POST /classifier/ei/fetch_model starts a background job and returns
    # {"started": True} immediately -- actual progress/completion arrives
    # as "ei_progress" WS messages, same "observe over /ws, not the REST
    # response" shape as commission/stop's training_progress. (Train, S4
    # steps 5-6, no longer has a route -- S8.2 dropped it from the tab
    # since training now happens in EI Studio itself; Fetch is the only
    # glue left to pull the compiled model back down.)
    api = ApiUnderTest(tmp_dir)
    try:
        status, body = api.request("POST", f"/nodes/{NODE_ID}/device_type", {"device_type": "motor001"})
        assert status == 200, (status, body)
        status, body = api.request("POST", "/classifier/ei/link",
                                    {"device_type": "motor001", "username": "me@example.com",
                                     "password": "hunter2"})
        assert status == 200 and body["linked"] is True, (status, body)

        # fetch_model() now derives the fetched model's class-label order
        # from local captures (pipeline/classifier.py needs real label
        # names, not just a count) -- needs at least one on disk first,
        # same setup as test_ei_link_creates_project_then_upload_pushes_samples.
        status, _ = api.request("POST", f"/nodes/{NODE_ID}/capture/start")
        assert status == 200
        api.capture.feed_frame(frame(NODE_ID))
        status, _ = api.request("POST", f"/nodes/{NODE_ID}/capture/stop")
        assert status == 200
        status, body = api.request("POST", f"/nodes/{NODE_ID}/capture/save", {"label": "bearing_fault"})
        assert status == 200, (status, body)

        with api.client.websocket_connect("/ws") as ws:
            status, body = api.request("POST", "/classifier/ei/fetch_model", {"device_type": "motor001"})
            assert status == 200 and body == {"started": True}, (status, body)

            stages = []
            for _ in range(50):
                message = ws.receive_json()
                if message["type"] == "ei_progress" and message["device_type"] == "motor001" \
                        and message["action"] == "fetch":
                    stages.append(message["stage"])
                    if message["stage"] == "done":
                        break
            assert stages == ["building", "building", "downloading", "done"], stages

            status, body = api.request("GET", "/classifier/ei/status")
            assert body["models"]["motor001"] is not None, \
                "fetch_model must record a fetched-model timestamp once done"
            model_path = os.path.join(api.ei_models_dir, "motor001.tflite")
            assert os.path.isfile(model_path)
            labels_path = os.path.join(api.ei_models_dir, "motor001.labels.json")
            assert os.path.isfile(labels_path)
        print("POST /classifier/ei/fetch_model runs its background job and "
              "streams ei_progress over /ws through to done: PASS")
    finally:
        api.stop()


def test_ei_upload_rejects_unlinked_device_type_synchronously(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, body = api.request("POST", f"/nodes/{NODE_ID}/device_type", {"device_type": "motor001"})
        assert status == 200, (status, body)

        status, body = api.request("POST", "/classifier/ei/upload", {"device_type": "motor001"})
        assert status == 409, (status, body)
        print("POST /classifier/ei/upload synchronously rejects an unlinked "
              "device_type instead of starting a doomed background job: PASS")
    finally:
        api.stop()


def test_ei_upload_job_failure_broadcasts_error_over_ws(tmp_dir):
    # delete-all (upload's first step, S8.3) failing must abort before any
    # sample is pushed -- otherwise a failed wipe followed by a successful
    # upload could double up data in the project (S8.7.4).
    api = ApiUnderTest(tmp_dir, ei_fail_job="delete_all_samples")
    try:
        status, body = api.request("POST", f"/nodes/{NODE_ID}/device_type", {"device_type": "motor001"})
        assert status == 200, (status, body)
        status, body = api.request("POST", "/classifier/ei/link",
                                    {"device_type": "motor001", "username": "me@example.com",
                                     "password": "hunter2"})
        assert status == 200 and body["linked"] is True, (status, body)
        status, _ = api.request("POST", f"/nodes/{NODE_ID}/capture/start")
        assert status == 200
        api.capture.feed_frame(frame(NODE_ID))
        status, _ = api.request("POST", f"/nodes/{NODE_ID}/capture/stop")
        assert status == 200
        status, body = api.request("POST", f"/nodes/{NODE_ID}/capture/save", {"label": "bearing_fault"})
        assert status == 200, (status, body)

        with api.client.websocket_connect("/ws") as ws:
            status, body = api.request("POST", "/classifier/ei/upload", {"device_type": "motor001"})
            assert status == 200, (status, body)

            error_message = None
            for _ in range(50):
                message = ws.receive_json()
                if message["type"] == "ei_progress" and message["stage"] == "error":
                    error_message = message
                    break
            assert error_message is not None, "expected an ei_progress error broadcast"
            assert error_message["device_type"] == "motor001"
            assert error_message["action"] == "upload"
            assert "error" in error_message and error_message["error"]

            status, body = api.request("GET", "/classifier/ei/status")
            assert body["jobs"] == {}, \
                "a failed job must still clear job_state(), not strand the device_type as busy"
        assert api.ei_client.uploads == [], \
            "a failed delete-all must abort before any sample is uploaded"
        print("A failed EI delete-all broadcasts an ei_progress error over "
              "/ws, clears job_state(), and uploads nothing: PASS")
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


def test_telegram_status_reports_not_configured_by_default(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, body = api.request("GET", "/alerts/telegram/status")
        assert status == 200, (status, body)
        assert body["configured"] is False, body
        print("GET /alerts/telegram/status reports not configured with no bot wired: PASS")
    finally:
        api.stop()


def test_telegram_connect_requires_configured_bot(tmp_dir):
    api = ApiUnderTest(tmp_dir)
    try:
        status, body = api.request("POST", "/alerts/telegram/connect")
        assert status == 503, (status, body)
        print("POST /alerts/telegram/connect 503s when Telegram isn't configured: PASS")
    finally:
        api.stop()


def test_telegram_connect_returns_token_and_deep_link(tmp_dir):
    api = ApiUnderTest(tmp_dir, telegram_bot=FakeTelegramBot())
    old_username = os.environ.get("TELEGRAM_BOT_USERNAME")
    os.environ["TELEGRAM_BOT_USERNAME"] = "test_epm_bot"
    try:
        status, body = api.request("GET", "/alerts/telegram/status")
        assert status == 200 and body["configured"] is True, (status, body)

        status, body = api.request("POST", "/alerts/telegram/connect")
        assert status == 200, (status, body)
        assert body["deep_link"] == f"https://t.me/test_epm_bot?start={body['token']}", body
        # A token is one-shot (alert_store_test.py covers consume_token's own
        # semantics in depth) -- just confirm this endpoint actually mints a
        # real, consumable one rather than a placeholder string.
        assert api.alert_store.consume_token(body["token"]) is True
        print("POST /alerts/telegram/connect returns a real token + deep link: PASS")
    finally:
        if old_username is None:
            os.environ.pop("TELEGRAM_BOT_USERNAME", None)
        else:
            os.environ["TELEGRAM_BOT_USERNAME"] = old_username
        api.stop()


def test_telegram_subscriber_prefs_update_broadcasts_and_disconnect_removes(tmp_dir):
    api = ApiUnderTest(tmp_dir, telegram_bot=FakeTelegramBot())
    try:
        api.alert_store.add_subscriber(chat_id=111, user_id=222, first_name="Alice")

        status, body = api.request("GET", "/alerts/telegram/subscribers")
        assert status == 200 and body["111"]["first_name"] == "Alice", (status, body)

        with api.client.websocket_connect("/ws") as ws:
            status, body = api.request(
                "POST", "/alerts/telegram/subscribers/111/prefs",
                body={"fault_only": True, "node_ids": ["node-1"]})
            assert status == 200, (status, body)
            assert body["fault_only"] is True and body["node_ids"] == ["node-1"], body

            msg = ws.receive_json()
            assert msg["type"] == "telegram_subscribers", msg
            assert msg["subscribers"]["111"]["fault_only"] is True, msg

        status, body = api.request("POST", "/alerts/telegram/subscribers/999/prefs",
                                    body={"fault_only": True})
        assert status == 404, (status, body)

        status, body = api.request("POST", "/alerts/telegram/subscribers/111/disconnect")
        assert status == 200, (status, body)
        status, body = api.request("GET", "/alerts/telegram/subscribers")
        assert "111" not in body, body

        status, body = api.request("POST", "/alerts/telegram/subscribers/111/disconnect")
        assert status == 404, (status, body)
        print("Telegram subscriber prefs update (+WS broadcast) and disconnect: PASS")
    finally:
        api.stop()


def main():
    tmp_dir = tempfile.mkdtemp(prefix="api_test_")

    test_get_nodes_lists_registry_entries(tempfile.mkdtemp(dir=tmp_dir))
    test_get_node_404_for_unknown(tempfile.mkdtemp(dir=tmp_dir))
    test_rename_updates_registry_and_broadcasts(tempfile.mkdtemp(dir=tmp_dir))
    test_device_type_updates_registry_and_broadcasts(tempfile.mkdtemp(dir=tmp_dir))
    test_pause_updates_registry_status(tempfile.mkdtemp(dir=tmp_dir))
    test_pause_uncommissioned_node_is_409(tempfile.mkdtemp(dir=tmp_dir))
    test_resume_updates_registry_status(tempfile.mkdtemp(dir=tmp_dir))
    test_decommission_removes_node_and_history(tempfile.mkdtemp(dir=tmp_dir))
    test_decommission_mid_commissioning_node_succeeds(tempfile.mkdtemp(dir=tmp_dir))
    test_commissioning_start_feed_stop_trains_model(tempfile.mkdtemp(dir=tmp_dir))
    test_recommissioning_clears_stale_history(tempfile.mkdtemp(dir=tmp_dir))
    test_commissioning_stop_without_start_is_409(tempfile.mkdtemp(dir=tmp_dir))
    test_commissioning_double_start_is_409(tempfile.mkdtemp(dir=tmp_dir))
    test_capture_start_feed_stop_save_persists_labeled_batch(tempfile.mkdtemp(dir=tmp_dir))
    test_capture_auto_stops_at_target_frames(tempfile.mkdtemp(dir=tmp_dir))
    test_capture_stop_without_start_is_409(tempfile.mkdtemp(dir=tmp_dir))
    test_capture_save_before_stop_is_409(tempfile.mkdtemp(dir=tmp_dir))
    test_capture_cancel_discards_batch(tempfile.mkdtemp(dir=tmp_dir))
    test_capture_start_unknown_node_is_404(tempfile.mkdtemp(dir=tmp_dir))
    test_decommission_discards_capture_session(tempfile.mkdtemp(dir=tmp_dir))
    test_captures_list_rename_delete(tempfile.mkdtemp(dir=tmp_dir))
    test_captures_rename_unknown_id_is_400(tempfile.mkdtemp(dir=tmp_dir))
    test_captures_rename_bulk(tempfile.mkdtemp(dir=tmp_dir))
    test_ei_status_reports_unlinked_by_default(tempfile.mkdtemp(dir=tmp_dir))
    test_ei_link_creates_project_then_upload_pushes_samples(tempfile.mkdtemp(dir=tmp_dir))
    test_ei_link_totp_required_returns_400_marker(tempfile.mkdtemp(dir=tmp_dir))
    test_ei_unlink_clears_project_and_allows_relink(tempfile.mkdtemp(dir=tmp_dir))
    test_ei_fetch_model_over_ws(tempfile.mkdtemp(dir=tmp_dir))
    test_ei_upload_rejects_unlinked_device_type_synchronously(tempfile.mkdtemp(dir=tmp_dir))
    test_ei_upload_job_failure_broadcasts_error_over_ws(tempfile.mkdtemp(dir=tmp_dir))
    test_history_endpoint_returns_recorded_scores(tempfile.mkdtemp(dir=tmp_dir))
    test_websocket_broadcast_reaches_connected_client(tempfile.mkdtemp(dir=tmp_dir))
    test_telegram_status_reports_not_configured_by_default(tempfile.mkdtemp(dir=tmp_dir))
    test_telegram_connect_requires_configured_bot(tempfile.mkdtemp(dir=tmp_dir))
    test_telegram_connect_returns_token_and_deep_link(tempfile.mkdtemp(dir=tmp_dir))
    test_telegram_subscriber_prefs_update_broadcasts_and_disconnect_removes(tempfile.mkdtemp(dir=tmp_dir))

    print("RESULT: PASS - REST endpoints reflect registry/commissioning changes, and "
          "live updates push to connected WebSocket clients")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
