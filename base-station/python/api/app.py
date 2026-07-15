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
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from commissioning import CommissioningError
from registry import InvalidTransitionError, NodeNotFoundError, Registry
from store import HistoryStore
from retention import DEFAULT_RETENTION_SECONDS, run_retention_loop
from perf import PerformanceMonitor
from connection_manager import ConnectionManager
from manager import PipelineManager

logger = logging.getLogger(__name__)


class RenameBody(BaseModel):
    # Optional + defaulted rather than required: the old handler used
    # body.get("display_name") and returned a 400 (not a 422) when it was
    # missing or empty, per the existing REST contract -- see the
    # not-display_name check in rename_node() below.
    display_name: Optional[str] = None


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
                manager: PipelineManager,
                perf_monitor: Optional[PerformanceMonitor] = None,
                on_startup: Optional[callable] = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.loop = asyncio.get_running_loop()
        if on_startup is not None:
            on_startup()
        retention_task = asyncio.create_task(
            run_retention_loop(history_store, DEFAULT_RETENTION_SECONDS))
        try:
            yield
        finally:
            retention_task.cancel()

    app = FastAPI(lifespan=lifespan)
    app.state.registry = registry
    app.state.history_store = history_store
    app.state.commissioning = commissioning
    app.state.manager = manager
    app.state.perf_monitor = perf_monitor if perf_monitor is not None else PerformanceMonitor()
    app.state.connection_manager = ConnectionManager()
    app.state.loop = None

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
        return app.state.perf_monitor.snapshot().to_dict()

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

    @app.post("/nodes/{node_id}/rename")
    def rename_node(node_id: str, body: RenameBody = RenameBody()):
        if not body.display_name:
            raise HTTPException(status_code=400, detail="display_name is required")
        try:
            entry = app.state.registry.rename(node_id, body.display_name)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        broadcast_threadsafe(app, {"type": "registry", "node_id": node_id, "entry": entry.to_dict()})
        return entry.to_dict()

    @app.post("/nodes/{node_id}/pause")
    def pause_node(node_id: str):
        try:
            entry = app.state.registry.pause(node_id)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        except InvalidTransitionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        broadcast_threadsafe(app, {"type": "registry", "node_id": node_id, "entry": entry.to_dict()})
        return entry.to_dict()

    @app.post("/nodes/{node_id}/resume")
    def resume_node(node_id: str):
        try:
            entry = app.state.registry.resume(node_id)
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown node_id {node_id!r}")
        except InvalidTransitionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        broadcast_threadsafe(app, {"type": "registry", "node_id": node_id, "entry": entry.to_dict()})
        return entry.to_dict()

    @app.post("/nodes/{node_id}/decommission")
    def decommission_node(node_id: str):
        # Drop any in-flight commissioning session before removing the
        # registry entry -- otherwise a node deleted mid-collection/training
        # leaves an orphaned session in CommissioningController forever
        # (see its discard()'s docstring).
        app.state.commissioning.discard(node_id)
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
        broadcast_threadsafe(app, {"type": "registry", "node_id": node_id, "entry": entry.to_dict()})
        return entry.to_dict()

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
        broadcast_threadsafe(app, {"type": "registry", "node_id": node_id, "entry": entry.to_dict()})

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
            except (CommissioningError, InvalidTransitionError):
                # Left in COMMISSIONING_TRAINING with the session retained
                # (CommissioningController.run_training's contract) -- no
                # established retry path for a mid-training failure yet,
                # so surface it in the logs rather than silently stranding
                # or auto-recovering the node.
                logger.exception("training failed for node %r", node_id)
                return
            trained_entry = app.state.registry.get(node_id)
            broadcast_threadsafe(app, {"type": "registry", "node_id": node_id,
                                        "entry": trained_entry.to_dict()})

        threading.Thread(target=run_training, daemon=True).start()
        return entry.to_dict()

    return app
