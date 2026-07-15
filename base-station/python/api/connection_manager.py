"""WebSocket connection manager -- replaces WebSocketServer's socket-level
client bookkeeping (api/websocket.py) with FastAPI's native WebSocket
support. Step 2 of the FastAPI migration (see
docs/CLAUDE_CODE_START_PROMPT_fastapi_migration.md).

External behavior matches the old WebSocketServer: something callable as
broadcast(message: dict) that pushes to every connected client, dropping
any client that errors on send rather than raising.
"""
import asyncio
from typing import Dict, List

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self._connections: List[WebSocket] = []
        self._locks: Dict[WebSocket, asyncio.Lock] = {}

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.append(websocket)
        self._locks[websocket] = asyncio.Lock()

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)
        self._locks.pop(websocket, None)

    async def broadcast(self, message: dict) -> None:
        """Pushes `message` as JSON to every currently-connected client.
        A client that errors on send (already gone) is dropped rather
        than raising -- one dead dashboard tab must not break the push
        for every other pipeline/client, same guarantee as the old
        WebSocketServer.broadcast()."""
        for websocket in list(self._connections):
            lock = self._locks.get(websocket)
            if lock is None:
                continue
            try:
                async with lock:
                    await websocket.send_json(message)
            except Exception:
                self.disconnect(websocket)
