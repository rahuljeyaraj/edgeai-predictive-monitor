"""FastAPI app -- REST routes + WebSocket push, per
docs/MPU_Software_Architecture.md S5.1 ("the only thing the frontend
talks to") and S8 M10.

Steps 1-3 of the FastAPI migration (see
docs/CLAUDE_CODE_START_PROMPT_fastapi_migration.md): REST routes, the
WebSocket endpoint, and the sync->async broadcast bridge. No main.py
wiring yet (Step 4) -- this module is built and verified standalone
first.

The ConnectionManager (api/connection_manager.py) is the single owner of
broadcasting, replacing both the old WebSocketServer.broadcast and
rest.py's broadcaster-closure indirection. Two classes of caller need to
reach its async broadcast() from synchronous code running off the event
loop:
  - REST route handlers here (defined as plain `def`, so FastAPI runs
    them in a worker thread, not the event loop).
  - main.py's `on_frame`, called from ingestion threads (Step 4).
Both go through `broadcast_threadsafe`, which hands the coroutine to the
event loop via `asyncio.run_coroutine_threadsafe` -- the standard
sync->async bridge, chosen over a queue+drain-task because there's no
backpressure requirement here and this needs no new moving parts. The
loop reference is captured once at startup via `lifespan` and stored on
app.state.

Shared state (registry/history_store/commissioning/perf_monitor/
connection_manager/loop) is attached to app.state rather than closed
over, so a single app instance can be constructed with whatever objects
a given run (real main.py, or a test) provides.

API routes are registered before any StaticFiles mount is added (Step
4, main.py) so the frontend's catch-all static handler can never shadow
these paths.
"""
import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from commissioning import CommissioningError
from capture import CaptureError
from stopped_baseline import StoppedBaselineError
from setup_controller import SetupError
from ei_client import EIClientError, EITotpRequiredError
from ei_controller import EIControllerError
from registry import (InvalidTransitionError, NodeNotFoundError, Registry,
                       TripMotorInUseError)
from protection import ProtectionError
from store import HistoryStore
from retention import DEFAULT_RETENTION_SECONDS, run_retention_loop
from perf import PerformanceMonitor
from gpu_perf import GpuPerfPoller
from spi_reader import SpiConsumer
import telegram_alerts
from wifi import WifiStatusPoller, connect as wifi_connect, scan as wifi_scan
from connection_manager import ConnectionManager
from manager import PipelineManager
from alert_store import AlertStore, SubscriberNotFoundError

logger = logging.getLogger(__name__)

# Dev/perf page (docs/DEV_PERF_PAGE_PLAN.md) -- shapes returned by GET /perf
# and the "perf_stats" WS broadcast for the "ingest" tier, when the
# corresponding consumer wasn't wired in (e.g. tests constructing routes
# standalone). Kept the same shape as the populated case so the frontend
# never has to special-case a missing key.
_EMPTY_INGEST = {"seq": None, "frames_ok": 0, "frames_dup": 0, "frames_dropped": 0,
                  "crc_fail": 0, "arm_gap": 0}
_EMPTY_GPU_PERF = {"available": False, "busy_percent": None}
_EMPTY_WIFI_STATUS = {"available": False, "mode": None, "ssid": None, "ip": None}

_PERF_BROADCAST_INTERVAL_S = 1.0


class RenameBody(BaseModel):
    # Optional + defaulted rather than required: the old handler used
    # body.get("device_name") and returned a 400 (not a 422) when it was
    # missing or empty, per the existing REST contract -- see the
    # not-device_name check in rename_node() below.
    device_name: Optional[str] = None


class DeviceTypeBody(BaseModel):
    # Empty string means "clear it back to unassigned" -- normalized to
    # None before hitting Registry.set_device_type() (see the route below),
    # same "blank means unset" contract as the capture toolbar's frame-count
    # field.
    device_type: Optional[str] = None


class TripMotorBody(BaseModel):
    # None = clear the trip output back to unarmed, the default for every
    # asset -- same "blank means unset" contract as DeviceTypeBody above.
    motor_idx: Optional[int] = None


class TripMotorConfirmBody(BaseModel):
    # Required, unlike TripMotorBody's: there is no such thing as testing
    # "no output".
    motor_idx: int


class SetupStartBody(BaseModel):
    # None = open on the first step this asset hasn't satisfied. A named
    # step jumps straight there (docs/UNIFIED_COMMISSIONING_PLAN.md S10 Q2).
    step: Optional[str] = None


class SetupAdvanceBody(BaseModel):
    # Only step 1 reads these; every other step's inputs were already
    # committed by the sub-session it drives (a stopped baseline, a
    # collected condition), so its advance carries no body at all.
    device_name: Optional[str] = None
    device_type: Optional[str] = None


class SetupConditionBody(BaseModel):
    name: str


class CaptureStartBody(BaseModel):
    # None = manual-stop-only, no cap (pipeline/capture.py's default).
    # "we know how many frames a good batch needs" (2026-07-24) -- the
    # frontend always sends a value (defaults its input to 50), but this
    # stays optional so a bare POST with no body still works.
    target_frames: Optional[int] = None


class CaptureSaveBody(BaseModel):
    label: str


class CaptureRenameBody(BaseModel):
    id: str
    label: str


class CaptureDeleteBody(BaseModel):
    ids: List[str]


class CaptureRenameBulkBody(BaseModel):
    ids: List[str]
    label: str


class WifiConnectBody(BaseModel):
    ssid: str
    password: str


class EILinkBody(BaseModel):
    device_type: str
    username: str
    password: str
    totp: Optional[str] = None


class EIUnlinkBody(BaseModel):
    device_type: str


class EIUploadBody(BaseModel):
    device_type: str
    ids: List[str]


class EIFetchModelBody(BaseModel):
    device_type: str


class TelegramPrefsBody(BaseModel):
    # Whole-object PUT, not a partial PATCH: the frontend always has the
    # subscriber's current prefs from GET /alerts/telegram/subscribers
    # already, so both fields are sent on every update -- this sidesteps
    # needing to distinguish "field omitted" from "field explicitly null"
    # (node_ids=null is a real value, meaning "every node") the way a
    # partial-update contract would.
    fault_only: bool
    node_ids: Optional[List[str]] = None


def broadcast_threadsafe(app: FastAPI, message: dict) -> None:
    """The sync->async bridge: callable from any thread (a REST handler's
    worker thread, or an ingestion thread calling main.py's on_frame in
    Step 4) to push `message` through app.state.connection_manager. A
    no-op if the app hasn't started yet (app.state.loop unset) -- only
    relevant to tests that construct routes without running the app."""
    loop = getattr(app.state, "loop", None)
    if loop is None:
        return
    asyncio.run_coroutine_threadsafe(app.state.connection_manager.broadcast(message), loop)


def create_app(registry: Registry, history_store: HistoryStore,
                commissioning,
                capture,
                manager: PipelineManager,
                perf_monitor: Optional[PerformanceMonitor] = None,
                gpu_perf: Optional[GpuPerfPoller] = None,
                spi_consumer: Optional[SpiConsumer] = None,
                alert_store: Optional[AlertStore] = None,
                telegram_bot=None,
                telegram_bot_username: Optional[str] = None,
                ei=None,
                wifi_status: Optional[WifiStatusPoller] = None,
                protection=None,
                stopped_baseline=None,
                setup=None,
                trip_outputs=None,
                on_startup: Optional[callable] = None) -> FastAPI:
    def _perf_payload() -> dict:
        data = app.state.perf_monitor.snapshot().to_dict()
        data["gpu"] = (app.state.gpu_perf.snapshot()
                        if app.state.gpu_perf is not None else _EMPTY_GPU_PERF)
        if app.state.spi_consumer is not None:
            ingest = app.state.spi_consumer.snapshot()
            data["ingest"] = {k: ingest[k] for k in _EMPTY_INGEST}
        else:
            data["ingest"] = _EMPTY_INGEST
        return data

    async def run_perf_broadcast_loop(interval_seconds: float = _PERF_BROADCAST_INTERVAL_S) -> None:
        # Dev/perf page's frontend tiers stay live-updating with no polling
        # of their own (docs/DEV_PERF_PAGE_PLAN.md S4's UX rule) -- same
        # "cache, then push on a timer" shape as run_retention_loop below,
        # just broadcasting over /ws instead of pruning a table.
        #
        # Unlike run_retention_loop, this body is guarded per-tick: any
        # exception here (e.g. a transient psutil/Bridge hiccup) would
        # otherwise kill this asyncio.Task permanently -- the loop never
        # gets a second chance the way a thread's while-loop does, so one
        # bad tick would silently end live perf updates for the rest of
        # the process's life while GET /perf kept working fine, which is
        # exactly the "reload fixes it, nothing else does" bug this
        # logging-and-continuing guard exists to prevent.
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await app.state.connection_manager.broadcast({"type": "perf_stats", **_perf_payload()})
            except Exception:
                logger.exception("perf broadcast tick failed")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.loop = asyncio.get_running_loop()
        if on_startup is not None:
            on_startup()
        retention_task = asyncio.create_task(
            run_retention_loop(history_store, DEFAULT_RETENTION_SECONDS))
        perf_broadcast_task = asyncio.create_task(run_perf_broadcast_loop())
        try:
            yield
        finally:
            retention_task.cancel()
            perf_broadcast_task.cancel()

    app = FastAPI(lifespan=lifespan)
    app.state.registry = registry
    app.state.history_store = history_store
    app.state.commissioning = commissioning
    app.state.capture = capture
    app.state.manager = manager
    app.state.perf_monitor = perf_monitor if perf_monitor is not None else PerformanceMonitor()
    app.state.gpu_perf = gpu_perf
    app.state.spi_consumer = spi_consumer
    app.state.alert_store = alert_store
    app.state.telegram_bot = telegram_bot
    app.state.telegram_bot_username = telegram_bot_username
    app.state.ei = ei
    app.state.wifi_status = wifi_status
    app.state.protection = protection
    app.state.stopped_baseline = stopped_baseline
    app.state.setup = setup
    app.state.trip_outputs = trip_outputs
    app.state.connection_manager = ConnectionManager()
    app.state.loop = None

    def _on_registry_status_change(node_id: str, status) -> None:
        # Registry.on_status_change fires for every status transition
        # regardless of source -- REST actions (pause/resume/commission
        # start-stop) and PipelineManager's automatic inference-driven
        # confirm alike. Before this, only the REST handlers below
        # broadcast "registry" themselves, so a fault confirmed live by
        # inference.py's set_status() (a plain motor running its normal
        # course, no REST call involved) never reached a connected
        # dashboard until the next 5s GET /nodes poll -- this one listener
        # replaces every one-off broadcast_threadsafe call that used to
        # follow a status-changing registry method below.
        entry = registry.get(node_id)
        # _node_dict, not entry.to_dict(): a FAULT transition also starts the
        # protection countdown, and the dashboard needs trip_in_s in the same
        # push that told it about the fault. Waiting for the next 5s poll would
        # mean showing FAULT for several seconds with no sign that a trip was
        # already counting down against it.
        #
        # Safe despite _node_dict being defined further down: this only runs on
        # a real status change, long after create_app() has finished. main.py
        # registers protection's own listener before create_app, so by the time
        # this fires the countdown is already recorded.
        broadcast_threadsafe(app, {"type": "registry", "node_id": node_id,
                                    "entry": _node_dict(node_id, entry)})

    registry.on_status_change(_on_registry_status_change)

    def _on_capture_state_change(node_id: str, state: str, collected: int,
                                  target_frames: Optional[int]) -> None:
        # Same shape of problem as _on_registry_status_change above, and
        # the same fix: a capture's state can change from more than one
        # trigger source -- a REST start/stop/save/cancel call, or the
        # auto-stop-at-target_frames transition (CaptureController.
        # feed_frame(), called from the ingestion thread, no REST request
        # involved at all) -- so this one listener is the only broadcaster,
        # not each route handler individually (that shape would silently
        # miss the auto-stop case, which never goes through a route).
        broadcast_threadsafe(app, {
            "type": "capture", "node_id": node_id, "state": state,
            "collected": collected, "target_frames": target_frames,
        })

    capture.on_state_change(_on_capture_state_change)

    def _on_stopped_baseline_state_change(node_id: str, state: str, collected: int,
                                           min_frames: int) -> None:
        # Same centralization reason as the two listeners above: the
        # collected count advances from the ingestion thread, not from the
        # REST handler that started the capture.
        broadcast_threadsafe(app, {
            "type": "stopped_baseline", "node_id": node_id, "state": state,
            "collected": collected, "min_frames": min_frames,
        })

    if stopped_baseline is not None:
        stopped_baseline.on_state_change(_on_stopped_baseline_state_change)

    def _on_setup_change(node_id: str, snapshot: Optional[dict]) -> None:
        # Same centralization reason as the three listeners above: a setup
        # step also completes from outside a REST handler (training
        # finishing on its own background thread), so no route handler can
        # be the one broadcasting. `setup: null` means the flow ended --
        # cancelled, or the node was decommissioned underneath it.
        broadcast_threadsafe(app, {"type": "setup", "node_id": node_id, "setup": snapshot})

    if setup is not None:
        setup.on_change(_on_setup_change)

    # Permissive CORS: the dashboard frontend is served from a different
    # origin/port than this REST API in the pre-consolidation deployment,
    # and there's no auth/TLS anywhere in this stack (see
    # api/websocket.py's docstring) -- no origin worth restricting to.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        # Old rest.py's error shape is {"error": ...}, not FastAPI's
        # default {"detail": ...} -- keep the contract identical for the
        # dashboard (frontend/app.js) rather than renegotiating it here.
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        manager: ConnectionManager = app.state.connection_manager
        await manager.connect(websocket)
        try:
            while True:
                # Push-only channel (S2): the dashboard never sends data
                # back, so any inbound message/close is only used to
                # detect disconnect, same as the old WebSocketServer
                # draining and ignoring client frames.
                await websocket.receive_text()
        except WebSocketDisconnect:
            manager.disconnect(websocket)

    @app.get("/perf")
    def get_perf():
        return _perf_payload()

    def _node_dict(node_id: str, entry) -> dict:
        d = entry.to_dict()
        # Only present while a commissioning session is active for this
        # node -- lets the dashboard's train icon (frontend/app.js) unlock
        # once enough frames are in, without duplicating frame-counting
        # logic client-side.
        progress = app.state.commissioning.progress(node_id)
        if progress is not None:
            collected, min_frames = progress
            d["commissioning_progress"] = {"collected": collected, "min_frames": min_frames}
        # Only present once a node has captured at least once, and only
        # while that session isn't idle -- same "absent means nothing to
        # show" contract as commissioning_progress above.
        capture_progress = app.state.capture.progress(node_id)
        if capture_progress is not None and capture_progress[0] != "idle":
            state, collected, target_frames = capture_progress
            d["capture_progress"] = {"state": state, "collected": collected,
                                      "target_frames": target_frames}
        # Machinery protection (protection/). Always present so the frontend
        # can render the Protection section without a null check, unlike the
        # two "absent means nothing to show" blocks above -- "armed: false" is
        # itself the thing an operator needs to see for an asset with no trip
        # output, and trip_in_s counts a live countdown down on each poll.
        if app.state.protection is not None:
            d["protection"] = dict(app.state.protection.snapshot(node_id),
                                    armed=app.state.protection.armed(node_id))
        # Only present while a stopped-baseline capture is running, same
        # "absent means nothing to show" contract as the two progress blocks
        # above. Whether a node *has* a baseline is already on the entry
        # (stopped_energy_ref), so the frontend doesn't need this to answer
        # that -- this is only the live "collecting N/30" readout.
        if app.state.stopped_baseline is not None:
            baseline_progress = app.state.stopped_baseline.progress(node_id)
            if baseline_progress is not None:
                collected, min_frames = baseline_progress
                d["stopped_baseline_progress"] = {"collected": collected,
                                                   "min_frames": min_frames}
        # Which step this asset's guided setup is on, if any -- same "absent
        # means nothing to show" contract as the progress blocks above, and
        # the same reason they ride here: the tile's one setup button
        # ("Set up" / "Setup - step 4 of 6") needs no second fetch.
        if app.state.setup is not None:
            setup_progress = app.state.setup.progress(node_id)
            if setup_progress is not None:
                d["setup_progress"] = setup_progress
        return d

    @app.get("/nodes")
    def list_nodes():
        return {node_id: _node_dict(node_id, entry)
                for node_id, entry in app.state.registry.list().items()}

    @app.get("/nodes/{node_id}/history")
    def get_node_history(node_id: str):
        return [r.to_dict() for r in app.state.history_store.query(node_id)]

    @app.get("/nodes/{node_id}")
    def get_node(node_id: str):
        try:
            entry = app.state.registry.get(node_id)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        return _node_dict(node_id, entry)

    @app.post("/perf/enable")
    def enable_perf():
        app.state.perf_monitor.enable()
        broadcast_threadsafe(app, {"type": "perf", "enabled": True})
        return {"enabled": True}

    @app.post("/perf/disable")
    def disable_perf():
        app.state.perf_monitor.disable()
        broadcast_threadsafe(app, {"type": "perf", "enabled": False})
        return {"enabled": False}

    # WiFi onboarding (docs/WIFI_ONBOARDING_PLAN.md S1). status GET reads
    # the background poller's cache (WifiStatusPoller, same "poll-not-push"
    # shape as gpu_perf -- see python/network/wifi.py); scan and connect
    # both block on a real nmcli call via host/wifi_bridge.py (a scan, and
    # a join attempt respectively), same as /classifier/ei/link's blocking
    # EI login call -- scan always returns 200 with an `error` field on
    # failure (never breaks the form, but still distinguishable from a
    # genuinely empty scan), connect's 503 distinguishes "the bridge
    # itself isn't provisioned" from a normal 400 join failure (wrong
    # password, out of range).
    @app.get("/network/wifi/status")
    def get_wifi_status():
        if app.state.wifi_status is None:
            return _EMPTY_WIFI_STATUS
        return app.state.wifi_status.snapshot()

    @app.get("/network/wifi/scan")
    def scan_wifi():
        return wifi_scan()

    @app.post("/network/wifi/connect")
    def connect_wifi(body: WifiConnectBody):
        try:
            result = wifi_connect(body.ssid, body.password)
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error") or "Connection failed")
        return result

    def _telegram_subscribers_dict() -> dict:
        if app.state.alert_store is None:
            return {}
        return {chat_id: sub.to_dict()
                for chat_id, sub in app.state.alert_store.list_subscribers().items()}

    @app.get("/alerts/telegram/status")
    def telegram_status():
        # bot_username comes from Telegram's own getMe API at startup
        # (main.py's build_telegram_alerts), not a second env var --
        # surfaced here so the frontend can build a helpful "not
        # configured yet" message rather than just hiding the connect
        # button with no explanation.
        return {
            "configured": app.state.telegram_bot is not None,
            "bot_username": app.state.telegram_bot_username,
        }

    @app.post("/alerts/telegram/connect")
    def telegram_connect():
        if app.state.telegram_bot is None or app.state.alert_store is None:
            raise HTTPException(status_code=503,
                                 detail="Telegram alerts not configured (TELEGRAM_BOT_TOKEN unset)")
        bot_username = app.state.telegram_bot_username
        if not bot_username:
            raise HTTPException(status_code=503,
                                 detail="Telegram bot username unavailable (getMe failed at startup)")
        token = app.state.alert_store.create_connect_token()
        deep_link = f"https://t.me/{bot_username}?start={token}"
        return {"token": token, "deep_link": deep_link,
                "qr_code": telegram_alerts.build_connect_qr(deep_link)}

    @app.get("/alerts/telegram/subscribers")
    def list_telegram_subscribers():
        return _telegram_subscribers_dict()

    @app.post("/alerts/telegram/subscribers/{chat_id}/prefs")
    def update_telegram_subscriber_prefs(chat_id: int, body: TelegramPrefsBody):
        if app.state.alert_store is None:
            raise HTTPException(status_code=503, detail="Telegram alerts not configured")
        try:
            app.state.alert_store.update_prefs(chat_id, fault_only=body.fault_only, node_ids=body.node_ids)
        except SubscriberNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown chat_id {chat_id!r}")
        subscribers = _telegram_subscribers_dict()
        broadcast_threadsafe(app, {"type": "telegram_subscribers", "subscribers": subscribers})
        return subscribers[str(chat_id)]

    @app.post("/alerts/telegram/subscribers/{chat_id}/disconnect")
    def disconnect_telegram_subscriber(chat_id: int):
        if app.state.alert_store is None:
            raise HTTPException(status_code=503, detail="Telegram alerts not configured")
        if not app.state.alert_store.remove_subscriber(chat_id):
            raise HTTPException(status_code=404, detail=f"unknown chat_id {chat_id!r}")
        broadcast_threadsafe(app, {"type": "telegram_subscribers", "subscribers": _telegram_subscribers_dict()})
        return {"chat_id": chat_id, "disconnected": True}

    @app.post("/nodes/{node_id}/rename")
    def rename_node(node_id: str, body: RenameBody = RenameBody()):
        if not body.device_name:
            raise HTTPException(status_code=400, detail="device_name is required")
        try:
            entry = app.state.registry.rename(node_id, body.device_name)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        broadcast_threadsafe(app, {"type": "registry", "node_id": node_id, "entry": entry.to_dict()})
        return entry.to_dict()

    @app.post("/nodes/{node_id}/device_type")
    def set_device_type(node_id: str, body: DeviceTypeBody = DeviceTypeBody()):
        device_type = body.device_type.strip() if body.device_type else None
        try:
            entry = app.state.registry.set_device_type(node_id, device_type)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        broadcast_threadsafe(app, {"type": "registry", "node_id": node_id, "entry": entry.to_dict()})
        return entry.to_dict()

    @app.post("/nodes/{node_id}/trip_motor")
    def set_trip_motor(node_id: str, body: TripMotorBody = TripMotorBody()):
        """Arms this asset's machinery-protection trip output against one motor
        on the rig, or clears it (docs/MOTOR_STOP_PLAN.md)."""
        try:
            entry = app.state.registry.set_trip_motor(node_id, body.motor_idx)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        except TripMotorInUseError as e:
            # 409, not 400: the request is well-formed, it just conflicts with
            # another node's existing claim on that motor.
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        broadcast_threadsafe(app, {"type": "registry", "node_id": node_id,
                                    "entry": _node_dict(node_id, entry)})
        return _node_dict(node_id, entry)

    @app.post("/nodes/{node_id}/protection/hold")
    def hold_protection(node_id: str):
        """Operator override: cancel a pending trip countdown."""
        if app.state.protection is None:
            raise HTTPException(status_code=503, detail="protection is not enabled")
        try:
            app.state.registry.get(node_id)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        if not app.state.protection.hold(node_id):
            # Nothing pending -- most likely the countdown already expired
            # between the operator seeing the button and pressing it. Saying so
            # is better than reporting a success that stopped nothing.
            raise HTTPException(status_code=409,
                                 detail="no trip is pending for this node")
        entry = app.state.registry.get(node_id)
        broadcast_threadsafe(app, {"type": "registry", "node_id": node_id,
                                    "entry": _node_dict(node_id, entry)})
        return _node_dict(node_id, entry)

    def _require_stopped_baseline():
        if app.state.stopped_baseline is None:
            raise HTTPException(status_code=503,
                                 detail="stopped-baseline capture is not enabled")
        return app.state.stopped_baseline

    @app.post("/nodes/{node_id}/stopped_baseline/start")
    def start_stopped_baseline(node_id: str):
        """Begins measuring this node's sensor noise floor. The machine must
        be OFF for the whole capture -- nothing here can verify that, see
        pipeline/stopped_baseline.py."""
        controller = _require_stopped_baseline()
        try:
            controller.start(node_id)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        except StoppedBaselineError as e:
            raise HTTPException(status_code=409, detail=str(e))
        entry = app.state.registry.get(node_id)
        return _node_dict(node_id, entry)

    @app.post("/nodes/{node_id}/stopped_baseline/stop")
    def stop_stopped_baseline(node_id: str):
        """Fits the baseline from what's been collected and stores it on the
        node. 409 (with the reason) if there aren't enough frames yet, or if
        what was collected doesn't look like a stopped machine -- the
        capture stays live in both cases so the operator can keep collecting
        and try again."""
        controller = _require_stopped_baseline()
        try:
            energy_ref, frames = controller.stop(node_id)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        except StoppedBaselineError as e:
            raise HTTPException(status_code=409, detail=str(e))
        entry = app.state.registry.get(node_id)
        broadcast_threadsafe(app, {"type": "registry", "node_id": node_id,
                                    "entry": _node_dict(node_id, entry)})
        return dict(_node_dict(node_id, entry),
                     stopped_baseline_result={"energy_ref": energy_ref, "frames": frames})

    @app.post("/nodes/{node_id}/stopped_baseline/cancel")
    def cancel_stopped_baseline(node_id: str):
        """Abandons an in-progress capture. The node keeps whatever baseline
        it already had."""
        controller = _require_stopped_baseline()
        try:
            app.state.registry.get(node_id)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        controller.cancel(node_id)
        return _node_dict(node_id, app.state.registry.get(node_id))

    @app.post("/nodes/{node_id}/stopped_baseline/clear")
    def clear_stopped_baseline(node_id: str):
        """Forgets a stored baseline, putting the node back on the
        running_energy_ref gate path. Separate from cancel: that drops an
        in-progress capture, this drops a finished one -- an operator who
        re-mounts a sensor needs the second."""
        _require_stopped_baseline()
        try:
            entry = app.state.registry.set_stopped_baseline(node_id, None, None)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        broadcast_threadsafe(app, {"type": "registry", "node_id": node_id,
                                    "entry": _node_dict(node_id, entry)})
        return _node_dict(node_id, entry)

    @app.post("/nodes/{node_id}/pause")
    def pause_node(node_id: str):
        try:
            entry = app.state.registry.pause(node_id)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        except InvalidTransitionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        # No broadcast_threadsafe here -- registry.pause() already fired
        # _on_registry_status_change above.
        return entry.to_dict()

    @app.post("/nodes/{node_id}/resume")
    def resume_node(node_id: str):
        try:
            entry = app.state.registry.resume(node_id)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        except InvalidTransitionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        # No broadcast_threadsafe here -- registry.resume() already fired
        # _on_registry_status_change above.
        return entry.to_dict()

    @app.post("/nodes/{node_id}/decommission")
    def decommission_node(node_id: str):
        # Drop any in-flight commissioning session before removing the
        # registry entry -- otherwise a node deleted mid-collection/training
        # leaves an orphaned session in CommissioningController forever
        # (see its discard()'s docstring).
        app.state.commissioning.discard(node_id)
        app.state.capture.discard(node_id)
        if app.state.stopped_baseline is not None:
            app.state.stopped_baseline.discard(node_id)
        if app.state.setup is not None:
            app.state.setup.discard(node_id)
        try:
            app.state.manager.decommission(node_id)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        broadcast_threadsafe(app, {"type": "removed", "node_id": node_id})
        return {"node_id": node_id, "removed": True}

    @app.post("/nodes/{node_id}/commission/start")
    def start_commissioning(node_id: str):
        try:
            app.state.registry.get(node_id)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        try:
            app.state.commissioning.start(node_id)
        except (CommissioningError, InvalidTransitionError) as e:
            raise HTTPException(status_code=409, detail=str(e))
        entry = app.state.registry.get(node_id)
        # No broadcast_threadsafe here -- commissioning.start() already
        # fired _on_registry_status_change above (via registry.start_commissioning()).
        return entry.to_dict()

    def _start_training(node_id: str) -> None:
        """Runs the (slow, fixed-epoch) fit on a background thread, streaming
        `training_progress` over /ws. Shared by POST /commission/stop and
        setup's own step 4 -> 5 advance, so setup doesn't grow a second
        training path that could drift from this one."""

        def on_epoch(epoch: int, total_epochs: int) -> None:
            # Throttled to roughly 20 broadcasts regardless of epoch count
            # (always epoch 1 and the final epoch) so a 300+ epoch run
            # doesn't flood the socket.
            step = max(1, total_epochs // 20)
            if epoch == 1 or epoch == total_epochs or epoch % step == 0:
                broadcast_threadsafe(app, {
                    "type": "training_progress", "node_id": node_id,
                    "epoch": epoch, "total_epochs": total_epochs,
                })

        def run_training() -> None:
            try:
                app.state.commissioning.run_training(node_id, on_epoch=on_epoch)
            except (CommissioningError, InvalidTransitionError) as e:
                # Left in COMMISSIONING_TRAINING with the session retained
                # (CommissioningController.run_training's contract) -- no
                # established retry path for a mid-training failure yet,
                # so surface it in the logs rather than silently stranding
                # or auto-recovering the node. A setup in flight is told
                # too, so the drawer shows the reason on the step instead
                # of a spinner that never ends.
                logger.exception("training failed for node %r", node_id)
                if app.state.setup is not None:
                    app.state.setup.finish_training(node_id, error=str(e))
                return
            # Wipe this node's history now that a new model/calibration is in
            # place -- a recommission overwrites model_path in place (S6 open
            # question #6) rather than versioning it, but the old anomaly-
            # score trend was scored against the *previous* model/thresholds
            # and would otherwise sit in the dashboard's graph looking like
            # current data. Covers a first-time commission too (no-op: there's
            # nothing to delete yet). No broadcast_threadsafe here --
            # complete_commissioning() already fired _on_registry_status_change
            # above (HEALTHY), which the frontend uses as its own signal to
            # clear the client-side buffer in step (frontend/charts.js's
            # applyThresholds()).
            app.state.history_store.delete(node_id)
            if app.state.setup is not None:
                app.state.setup.finish_training(node_id)

        threading.Thread(target=run_training, daemon=True).start()

    @app.post("/nodes/{node_id}/commission/stop")
    def stop_commissioning(node_id: str):
        # Two-phase per dashboard redesign S6: stop_collecting() is fast
        # and synchronous (freezes the batch, flips to
        # COMMISSIONING_TRAINING), so the REST response returns immediately
        # reflecting that state. The actual (slow, fixed-epoch) fit runs on
        # a background thread; its eventual HEALTHY transition and
        # training_progress messages arrive over /ws, not this response --
        # a deliberate API contract change from the old synchronous
        # "stop() also trains and returns model_path" shape.
        try:
            app.state.commissioning.stop_collecting(node_id)
        except (CommissioningError, InvalidTransitionError) as e:
            raise HTTPException(status_code=409, detail=str(e))
        entry = app.state.registry.get(node_id)
        # No broadcast_threadsafe here -- stop_collecting() already fired
        # _on_registry_status_change above (COMMISSIONING_TRAINING).
        _start_training(node_id)
        return entry.to_dict()

    # Guided setup (docs/UNIFIED_COMMISSIONING_PLAN.md) -- one flow that
    # sequences the four scattered ones. Additions only: every route these
    # steps drive (stopped_baseline/*, commission/*, trip_motor) still works
    # standalone, which is what lets a single step be re-entered on its own
    # (S2.1) without walking the whole wizard.
    def _require_setup():
        if app.state.setup is None:
            raise HTTPException(status_code=503, detail="guided setup is not enabled")
        return app.state.setup

    def _setup_response(node_id: str, snapshot) -> dict:
        # Always paired with the node entry: every step's controls read from
        # the registry (name, asset class, trip output, baseline), so
        # returning the step state alone would make the drawer immediately
        # re-fetch the node anyway.
        return {"setup": snapshot, "node": _node_dict(node_id, app.state.registry.get(node_id))}

    @app.get("/nodes/{node_id}/setup")
    def get_setup(node_id: str):
        controller = _require_setup()
        try:
            app.state.registry.get(node_id)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        return _setup_response(node_id, controller.snapshot(node_id))

    @app.post("/nodes/{node_id}/setup/start")
    def start_setup(node_id: str, body: SetupStartBody = SetupStartBody()):
        """Enters setup, or re-enters it (`Re-run setup`)."""
        controller = _require_setup()
        try:
            snapshot = controller.start(node_id, body.step)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        except SetupError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return _setup_response(node_id, snapshot)

    @app.post("/nodes/{node_id}/setup/advance")
    def advance_setup(node_id: str, body: SetupAdvanceBody = SetupAdvanceBody()):
        """Completes the current step. 409 (with an operator-readable
        reason) when its precondition isn't met -- the step stays open for a
        retry, same contract stopped_baseline/stop and commission/stop
        already have."""
        controller = _require_setup()
        was_conditions = (controller.progress(node_id) or {}).get("step")
        try:
            snapshot = controller.advance(node_id, device_name=body.device_name,
                                           device_type=body.device_type)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        except SetupError as e:
            raise HTTPException(status_code=409, detail=str(e))
        # Leaving step 4 froze the batch; the fit itself runs off the request
        # thread exactly as POST /commission/stop's does, and the flow lands
        # on Done when it finishes (SetupController.finish_training).
        if was_conditions == "conditions":
            _start_training(node_id)
        return _setup_response(node_id, snapshot)

    @app.post("/nodes/{node_id}/setup/skip")
    def skip_setup_step(node_id: str):
        controller = _require_setup()
        try:
            snapshot = controller.skip(node_id)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        except SetupError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return _setup_response(node_id, snapshot)

    @app.post("/nodes/{node_id}/setup/cancel")
    def cancel_setup(node_id: str):
        controller = _require_setup()
        controller.cancel(node_id)
        return _setup_response(node_id, controller.snapshot(node_id))

    @app.post("/nodes/{node_id}/setup/condition")
    def start_setup_condition(node_id: str, body: SetupConditionBody):
        """Starts collecting one named operating condition, closing whichever
        one was collecting before it (S2.3)."""
        controller = _require_setup()
        try:
            snapshot = controller.add_condition(node_id, body.name)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        except SetupError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return _setup_response(node_id, snapshot)

    @app.get("/trip_outputs")
    def get_trip_outputs():
        """What the rig says it has, plus who already claims each output
        (docs/UNIFIED_COMMISSIONING_PLAN.md S3.2). An empty list is a normal
        answer -- no rig has announced (or it's running an older
        motor_driver.py), which is what setup's manual fallback covers."""
        outputs = app.state.trip_outputs.snapshot() if app.state.trip_outputs else []
        claimed = {entry.trip_motor_idx: node_id
                   for node_id, entry in app.state.registry.list().items()
                   if entry.trip_motor_idx is not None}
        return {"outputs": [dict(output, claimed_by=claimed.get(output["idx"]))
                            for output in outputs]}

    @app.post("/nodes/{node_id}/trip_motor/confirm")
    def confirm_trip_motor(node_id: str, body: TripMotorConfirmBody):
        """Runs the stop-and-watch test (S3.3): send this output a stop and
        watch this node's own gate. Returns as soon as the stop is published
        -- the answer takes seconds and arrives as a `trip_confirm` broadcast,
        so it can't sit on a request thread.

        A 409 here means the test couldn't be run at all (most often: the
        machine isn't running, and a stopped machine would appear to confirm
        whichever output we tried). A test that runs and fails is a normal
        result, not an error -- it says this is the wrong output."""
        if app.state.protection is None:
            raise HTTPException(status_code=503, detail="protection is not enabled")
        try:
            app.state.registry.get(node_id)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")

        def on_result(confirmed: bool, message: str) -> None:
            if confirmed:
                # Recorded only on success. A failed test deliberately leaves
                # the mapping alone rather than storing an unconfirmed guess
                # the operator didn't ask for.
                try:
                    app.state.registry.set_trip_motor(node_id, body.motor_idx,
                                                       confirmed_at=time.time())
                except (NodeNotFoundError, TripMotorInUseError, ValueError) as e:
                    broadcast_threadsafe(app, {
                        "type": "trip_confirm", "node_id": node_id,
                        "motor_idx": body.motor_idx, "confirmed": False,
                        "message": str(e)})
                    return
            broadcast_threadsafe(app, {
                "type": "trip_confirm", "node_id": node_id, "motor_idx": body.motor_idx,
                "confirmed": confirmed, "message": message})
            entry = app.state.registry.get(node_id)
            broadcast_threadsafe(app, {"type": "registry", "node_id": node_id,
                                        "entry": _node_dict(node_id, entry)})

        try:
            app.state.protection.confirm_trip_output(node_id, body.motor_idx, on_result)
        except ProtectionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"node_id": node_id, "motor_idx": body.motor_idx, "testing": True}

    # Capture + label (docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S2) --
    # independent of commissioning/NodeStatus entirely, so unlike every
    # route above there's no registry status to check or transition here.
    # No broadcast_threadsafe calls in the handlers below -- unlike the
    # commission routes, EVERY capture state change (including the
    # auto-stop-at-target_frames one, which never goes through a route at
    # all) already fires _on_capture_state_change above.
    @app.post("/nodes/{node_id}/capture/start")
    def start_capture(node_id: str, body: CaptureStartBody = CaptureStartBody()):
        try:
            app.state.capture.start(node_id, target_frames=body.target_frames)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        except CaptureError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"node_id": node_id, "state": "capturing"}

    @app.post("/nodes/{node_id}/capture/stop")
    def stop_capture(node_id: str):
        try:
            collected = app.state.capture.stop(node_id)
        except CaptureError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"node_id": node_id, "state": "stopped", "collected": collected}

    @app.post("/nodes/{node_id}/capture/save")
    def save_capture(node_id: str, body: CaptureSaveBody):
        try:
            app.state.capture.save(node_id, body.label)
        except CaptureError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"node_id": node_id, "state": "idle", "saved": True}

    @app.post("/nodes/{node_id}/capture/cancel")
    def cancel_capture(node_id: str):
        app.state.capture.cancel(node_id)
        return {"node_id": node_id, "state": "idle"}

    @app.get("/captures/labels")
    def get_capture_labels():
        return {"labels": app.state.capture.list_labels()}

    @app.get("/captures")
    def get_captures():
        # Classifier tab's sample table (docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md
        # S3) -- every saved batch across every node/label, not scoped to
        # one node the way the /nodes/{node_id}/capture/* routes above are.
        return {"captures": app.state.capture.list_captures()}

    @app.post("/captures/rename")
    def rename_capture_route(body: CaptureRenameBody):
        try:
            new_id = app.state.capture.rename_capture(body.id, body.label)
        except CaptureError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"id": new_id}

    @app.post("/captures/delete")
    def delete_captures_route(body: CaptureDeleteBody):
        deleted = 0
        for capture_id in body.ids:
            try:
                app.state.capture.delete_capture(capture_id)
                deleted += 1
            except CaptureError:
                pass  # already gone / bad id -- deleting a selection is best-effort
        return {"deleted": deleted}

    @app.post("/captures/rename_bulk")
    def rename_captures_bulk_route(body: CaptureRenameBulkBody):
        # Classifier tab's "Edit label (N)" action (docs/
        # EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S8.7.1) -- one new label
        # applied to every selected row in a single call rather than N
        # sequential /captures/rename round-trips. Best-effort per id, same
        # shape as /captures/delete above.
        renamed = 0
        for capture_id in body.ids:
            try:
                app.state.capture.rename_capture(capture_id, body.label)
                renamed += 1
            except CaptureError:
                pass
        return {"renamed": renamed}

    @app.get("/device_types")
    def get_device_types():
        # Distinct types already assigned across the fleet, for the same
        # kind of suggestions dropdown /captures/labels backs -- so a second
        # node of a machine kind that's already been typed once doesn't
        # risk a near-duplicate ("Motor" vs "motor ") fragmenting the
        # capture/label grouping this field exists for.
        types = {entry.device_type for entry in app.state.registry.list().values()
                 if entry.device_type}
        return {"device_types": sorted(types)}

    # Edge Impulse (docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S4/S8).
    # Unlike Telegram (env-var-configured, legitimately absent on some
    # deployments), app.state.ei being unset means the controller was never
    # wired at all -- main.py always constructs one, so this only guards
    # against test/dev call sites that construct routes standalone.
    def _require_ei():
        if app.state.ei is None:
            raise HTTPException(status_code=503, detail="Edge Impulse integration not configured")
        return app.state.ei

    @app.get("/classifier/ei/status")
    def ei_status():
        ctrl = _require_ei()
        return {"device_types": ctrl.status(), "project_ids": ctrl.project_ids(),
                "project_names": ctrl.project_names(),
                "models": ctrl.model_status(), "jobs": ctrl.job_state()}

    @app.post("/classifier/ei/link")
    def ei_link(body: EILinkBody):
        try:
            return _require_ei().link(
                body.device_type, body.username, body.password, body.totp)
        except EITotpRequiredError:
            raise HTTPException(status_code=400, detail={"totp_required": True})
        except (EIClientError, EIControllerError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/classifier/ei/unlink")
    def ei_unlink(body: EIUnlinkBody):
        return _require_ei().unlink(body.device_type)

    # Edge Impulse -- Upload/Fetch (S4 steps 4/8-9, upload reworked per S8).
    # All three long-running actions (upload/train/fetch -- train is no
    # longer reachable from a route, see EIController's module docstring)
    # share this shape: a fast synchronous 409 if the device_type isn't
    # linked yet or a job's already running for it (checked here so that
    # common case is an immediate HTTP error, not just an async broadcast),
    # then the actual EI work runs on a background Thread and streams
    # "ei_progress" over /ws the same way commission/stop streams
    # "training_progress", since a FastAPI route handler blocking for real
    # minutes would tie up a worker thread and leave the request hanging.
    # on_progress(stage, **extra) -- extra is spread into the broadcast so
    # upload()'s uploaded/total/failures counters ride along the same
    # message shape train()/fetch_model()'s bare stage strings use.
    def _run_ei_job(device_type: str, action: str, job_fn) -> None:
        def on_progress(stage: str, **extra) -> None:
            broadcast_threadsafe(app, {
                "type": "ei_progress", "device_type": device_type,
                "action": action, "stage": stage, **extra,
            })

        def run() -> None:
            try:
                job_fn(on_progress)
            except Exception as e:
                # Broad on purpose, not just (EIClientError, EIControllerError):
                # this runs off a background Thread, so an exception this
                # doesn't catch doesn't propagate anywhere -- it just kills the
                # thread silently and leaves the dashboard stuck on its last
                # progress stage forever with no error shown. Bit us for real
                # once already (extract_tflite()'s zipfile.BadZipFile, from the
                # deployment/download `type` query-param bug fixed alongside
                # this in ei_client.py) so anything unexpected must still reach
                # the "error" broadcast below.
                logger.exception("EI %s failed for device_type %r", action, device_type)
                broadcast_threadsafe(app, {
                    "type": "ei_progress", "device_type": device_type,
                    "action": action, "stage": "error", "error": str(e),
                })
                return
            broadcast_threadsafe(app, {
                "type": "ei_progress", "device_type": device_type,
                "action": action, "stage": "done",
            })

        threading.Thread(target=run, daemon=True).start()

    @app.post("/classifier/ei/upload")
    def ei_upload(body: EIUploadBody):
        ctrl = _require_ei()
        if not body.ids:
            raise HTTPException(status_code=400, detail="select at least one recording to upload")
        if not ctrl.status().get(body.device_type):
            raise HTTPException(status_code=409,
                                 detail=f"device_type {body.device_type!r} isn't linked to Edge Impulse yet")
        if ctrl.job_state().get(body.device_type):
            raise HTTPException(status_code=409,
                                 detail=f"a job is already running for {body.device_type!r}")
        _run_ei_job(body.device_type, "upload",
                    lambda on_progress: ctrl.upload(body.device_type, body.ids, on_progress=on_progress))
        return {"started": True}

    @app.post("/classifier/ei/fetch_model")
    def ei_fetch_model(body: EIFetchModelBody):
        ctrl = _require_ei()
        if not ctrl.status().get(body.device_type):
            raise HTTPException(status_code=409,
                                 detail=f"device_type {body.device_type!r} isn't linked to Edge Impulse yet")
        if ctrl.job_state().get(body.device_type):
            raise HTTPException(status_code=409,
                                 detail=f"a job is already running for {body.device_type!r}")
        _run_ei_job(body.device_type, "fetch",
                    lambda on_progress: ctrl.fetch_model(body.device_type, on_progress=on_progress))
        return {"started": True}

    return app
