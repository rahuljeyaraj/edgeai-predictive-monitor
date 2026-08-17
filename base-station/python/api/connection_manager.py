"""WebSocket connection manager -- replaces WebSocketServer's socket-level
client bookkeeping (api/websocket.py) with FastAPI's native WebSocket
support. Step 2 of the FastAPI migration (see
docs/CLAUDE_CODE_START_PROMPT_fastapi_migration.md).

External behavior matches the old WebSocketServer: something callable as
broadcast(message: dict) that pushes to every connected client, dropping
any client that errors on send rather than raising.

Per-client delivery is *coalescing*, not queueing. The original version
awaited `websocket.send_json(...)` inline for every broadcast under a
per-socket lock, and main.py's on_frame fires one broadcast per ingested
frame from an ingestion thread via broadcast_threadsafe(). Each call handed
the loop another coroutine, so a client that could not drain at the full
frame rate accumulated one pending coroutine per frame, forever -- all of
them parked on the same lock, all holding their message alive. Nothing
bounded that queue.

That is what a stuttering dashboard actually looks like from this side: the
browser is not merely behind, it is being served an ever-lengthening backlog
of *stale* spectra it must still JSON.parse and draw before it can reach the
current one, while the event loop carries the whole backlog and serves every
REST poll and every other client late. It never recovers on its own, because
falling behind is what makes it fall further behind.

So high-rate telemetry is now latest-wins per (type, node_id): while a send
is in flight, a newer spectrum frame for that node *replaces* the pending
one instead of joining a queue. A slow client degrades to a lower frame rate
showing current data -- which is what a live chart wants -- rather than a
growing lag showing old data. State-transition messages (registry, setup,
capture, trip_confirm, ...) are never coalesced: those are edges, not
samples, and dropping one loses information no later message repeats.
"""
import asyncio
import logging
from collections import deque
from typing import Dict, List

from fastapi import WebSocket

logger = logging.getLogger("connection_manager")

# Message types that are periodic samples of a current value, where a newer
# message fully supersedes an older one for the same node. Anything NOT
# listed here is treated as a state transition and delivered reliably --
# that default is deliberate, so a new message type added elsewhere in the
# codebase is never silently made droppable by omission.
COALESCING_TYPES = frozenset({"spectrum", "anomaly", "classification", "perf_stats"})

# Reliable-queue depth past which a client is considered hopeless and
# dropped. Only state-transition messages land here (samples coalesce and so
# cannot grow), and they arrive at human speed -- a few per commissioning
# step -- so reaching this many outstanding means the socket is not draining
# at all, and holding the memory helps nobody.
MAX_RELIABLE_BACKLOG = 512


class _Outbox:
    """One client's pending sends: a reliable FIFO plus a latest-wins slot
    per coalescing key, with arrival order preserved across both."""

    def __init__(self) -> None:
        self._reliable = deque()
        self._latest: Dict[tuple, dict] = {}
        self._order: deque = deque()
        self._wakeup = asyncio.Event()
        self.dropped = 0
        self.overflowed = False

    def put(self, message: dict) -> None:
        message_type = message.get("type")
        if message_type in COALESCING_TYPES:
            key = (message_type, message.get("node_id"))
            if key in self._latest:
                # Client has not drained the previous one of these yet --
                # supersede it in place, keeping its position in _order so a
                # continuously-behind client still sees every node in turn
                # rather than starving the ones that update least often.
                self.dropped += 1
            else:
                self._order.append(key)
            self._latest[key] = message
        else:
            if len(self._reliable) >= MAX_RELIABLE_BACKLOG:
                self.overflowed = True
                return
            self._reliable.append(message)
        self._wakeup.set()

    def _pending(self) -> bool:
        return bool(self._reliable or self._order)

    def _pop(self):
        # Reliable first: a status change should not sit behind a queue of
        # spectra, and there are never many of them outstanding.
        if self._reliable:
            return self._reliable.popleft()
        while self._order:
            message = self._latest.pop(self._order.popleft(), None)
            if message is not None:
                return message
        return None

    async def drain(self, websocket: WebSocket) -> None:
        """Sends until empty, then sleeps until put() wakes it. Runs as one
        task per client for the client's whole lifetime, so exactly one send
        is ever in flight per socket -- which is what makes the coalescing
        above meaningful, and what the per-socket asyncio.Lock used to do."""
        while True:
            if not self._pending():
                # clear-then-recheck, not recheck-then-clear: put() sets the
                # event after enqueuing, so clearing first means a put()
                # racing this check can only ever leave the event *set*, i.e.
                # a spurious wakeup. The other order can lose one entirely
                # and park the writer on a non-empty outbox.
                self._wakeup.clear()
                if not self._pending():
                    await self._wakeup.wait()
                continue
            if self.overflowed:
                raise RuntimeError("reliable backlog overflow -- client not draining")
            message = self._pop()
            if message is not None:
                await websocket.send_json(message)


class ConnectionManager:
    def __init__(self):
        self._connections: List[WebSocket] = []
        self._outboxes: Dict[WebSocket, _Outbox] = {}
        self._writers: Dict[WebSocket, asyncio.Task] = {}

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        outbox = _Outbox()
        self._connections.append(websocket)
        self._outboxes[websocket] = outbox

        async def writer() -> None:
            try:
                await outbox.drain(websocket)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Already gone, or refusing to drain. Same contract as
                # before: one dead dashboard tab must never surface as an
                # error on the ingestion path that fed it.
                self.disconnect(websocket)

        self._writers[websocket] = asyncio.create_task(writer())

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)
        outbox = self._outboxes.pop(websocket, None)
        if outbox is not None and outbox.dropped:
            logger.info("client disconnected after coalescing %d stale telemetry message(s)",
                        outbox.dropped)
        writer = self._writers.pop(websocket, None)
        # Skipped when the writer is what called this (its own send failed):
        # it is already unwinding, and cancelling the running task from
        # inside itself only marks the completed task cancelled for nobody.
        if writer is not None and writer is not asyncio.current_task():
            writer.cancel()

    async def broadcast(self, message: dict) -> None:
        """Queues `message` for every currently-connected client.

        Returns as soon as the message is handed to each client's outbox --
        it does not wait for any socket to actually accept the bytes. That
        is the point: this is awaited from the event loop on behalf of an
        ingestion thread (api/app.py's broadcast_threadsafe), and the
        slowest connected client must not set the pace for ingestion, for
        the other clients, or for the REST handlers sharing this loop."""
        for websocket in list(self._connections):
            outbox = self._outboxes.get(websocket)
            if outbox is None:
                continue
            outbox.put(message)
