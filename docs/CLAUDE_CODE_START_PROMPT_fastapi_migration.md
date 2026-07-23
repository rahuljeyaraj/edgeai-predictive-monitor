# Migrate MPU API layer to FastAPI

## Context

- The MPU-side API layer (`rest.py`, `websocket.py`, plus the static-file server
  started in `main.py`) is currently entirely hand-rolled:
  - `rest.py` uses stdlib `http.server` with a regex routing table
    (`_NODE_RENAME_RE`, `_NODE_PAUSE_RE`, ...), manual JSON encode/decode, and
    manually-written CORS headers on every response.
  - `websocket.py` hand-implements the RFC6455 handshake and text-frame
    protocol over raw `socket`/`struct`, because no WebSocket library was
    believed to be installed.
- That "nothing is installed" assumption was wrong. `python3-fastapi` is
  available via `apt-get` on this image (confirmed: `apt-cache search fastapi`
  returns `python3-fastapi`; `import fastapi` currently fails only because it
  hasn't been installed yet). Same pattern already resolved for `paho-mqtt`
  and `pyserial`, and recently discovered to also apply to `websockets`.
- Goal: replace the hand-rolled REST server, WebSocket server, and static
  frontend server with a single FastAPI/ASGI app — and use the occasion to
  leave the API layer in the best shape reasonably achievable, not just a
  mechanical swap.
- Original scope reference: docs/MPU_Software_Architecture.md S5.1 ("the only
  thing the frontend talks to") and S8 M10 (WebSocket push) / M13 (frontend
  serving). This doc supersedes the *implementation* of those milestones, not
  their intent.

## Non-goals — do not touch

- No changes to domain logic: `registry.py`, `store.py`, `gate.py`,
  `features.py`, `autoencoder.py`, `inference.py`, `commissioning.py`,
  `manager.py`, `perf.py`, `sensor_frame.py`, `wire_protocol.py`, or ingestion
  (`uart_reader.py`, `mqtt_subscriber.py`). This is an API-layer swap only.
- No changes to REST route paths, methods, or JSON request/response shapes
  unless Phase 0 surfaces a specific reason to change one. The dashboard
  (`frontend/app.js`, `index.html`) depends on the current contract and
  should not need edits as a side effect of this migration.
- No change to CORS policy (permissive, `*`) or the fact this stack is
  unauthenticated/no-TLS. That tradeoff is documented elsewhere and is out of
  scope here.

## Phase 0 — discovery only, no edits

Answer all of these. Show findings and stop for review before writing any code.

1. Confirm what actually needs installing, beyond `python3-fastapi`:
   - An ASGI server to run the app (`uvicorn` or equivalent) — check apt
     availability first, same convention as everything else in this project.
   - Whatever WebSocket implementation FastAPI/Starlette pulls in under the
     hood (`websockets` or `wsproto`) — confirm present or installable via apt.
2. For anything not available via apt, confirm `pip install
   --break-system-packages` is the fallback, per this project's existing
   PEP 668 convention.
3. Identify every place a background ingestion thread (`on_frame` in
   `main.py`, or anything inside `manager.py`/`commissioning.py` triggered by
   it) needs to push a WebSocket broadcast. These calls happen on plain
   Python threads today; FastAPI's WebSocket handling is async, so each of
   these needs a sync→async bridge. Identify the bridge mechanism
   (`asyncio.run_coroutine_threadsafe`, a thread-safe queue drained by a
   background async task, or other) and where it lives.
4. Decide, and present as a tradeoff rather than picking silently: should
   REST + WebSocket + static frontend consolidate onto **one port**, or stay
   on the current three (`--rest-port` 8080, `--ws-port` 8081,
   `--frontend-port` 8082)? One port is the more idiomatic FastAPI/ASGI
   architecture, but changes `main.py`'s CLI surface and the deployment
   story. Do not decide this silently — flag it for RS.
5. Pull the exact current REST route list and request/response shapes
   straight from `rest.py`'s docstring and code. This is the acceptance
   contract for Phase 1 — every route must behave identically afterward
   unless explicitly renegotiated.

**Stop here. Show findings. Wait for review before Phase 1.**

## Phase 1 — implementation (after Phase 0 sign-off)

Target architecture:

- One FastAPI app replacing `rest.py`, `websocket.py`, and `main.py`'s
  `serve_frontend()`.
- REST routes as FastAPI path operations. Every request body gets a Pydantic
  model (e.g. `class RenameBody(BaseModel): display_name: str`) instead of
  manual `body.get(...)` + hand-written 400 responses.
- One WebSocket endpoint using FastAPI's native `WebSocket` support,
  replacing the hand-rolled handshake and frame encode/decode in
  `websocket.py`.
- A small connection-manager class (list of active connections + an async
  `broadcast()`), replacing `WebSocketServer`'s socket-level bookkeeping.
  External behavior must stay the same: something callable as
  `broadcast(message: dict)` that pushes to every connected client.
- Static frontend served via FastAPI's `StaticFiles` mount, replacing
  `serve_frontend()`.
- CORS via `fastapi.middleware.cors.CORSMiddleware`, replacing the manual
  `Access-Control-Allow-*` headers currently written by hand in every
  handler and in `do_OPTIONS`.
- `CommissioningController` keeps its current responsibilities; it's just
  called from FastAPI route handlers instead of a `BaseHTTPRequestHandler`
  subclass.
- Shared state (`registry`, `history_store`, `perf_monitor`,
  `commissioning`, the connection manager) attached via FastAPI dependency
  injection or `app.state`, replacing the closure-based
  `make_rest_handler(...)` factory pattern.
- `main.py` updated to build and run the FastAPI app (`uvicorn.run` or
  equivalent) instead of three separate `ThreadingHTTPServer`/socket-loop
  instances. Exact shape depends on the Phase 0 port-consolidation decision.

Follow the existing gated-diff convention — implement in the numbered steps
below, stop and show the diff at each step before moving to the next.
Adjust the breakdown based on Phase 0 findings if needed:

1. FastAPI app skeleton + Pydantic models for the existing REST routes,
   wired to the current `registry`/`history_store`/`commissioning` objects.
   No WebSocket yet, no `main.py` changes yet. Verify every route against the
   Phase 0 contract (same paths, same response shapes).
2. WebSocket endpoint + connection manager. Verify broadcast works against a
   simple test client before touching real ingestion.
3. Sync→async bridge for the `on_frame` broadcast call, per the Phase 0
   decision on mechanism.
4. `main.py` wiring updated to start the FastAPI app. Remove `rest.py`,
   `websocket.py`, and `serve_frontend()` only once the FastAPI app is
   confirmed to fully replace all three.
5. Update `CLAUDE.md` and any architecture-doc references if module names or
   responsibilities changed.

## Verification

- Every existing REST route responds with the same shape as before —
  spot-check against `rest.py`'s docstring route list.
- Dashboard (`frontend/app.js`, `index.html`) works unmodified against the
  new backend. If it doesn't, that's a contract break to flag, not something
  to silently patch around in `app.js`.
- WebSocket push still delivers `spectrum` / `registry` / `perf` messages
  live, with the same `type` tags the frontend already expects.
- `--simulate` mode still runs end-to-end: ingestion → pipeline → history →
  broadcast → dashboard, with no manual intervention.

## Sequencing note

This is independent of M14 (real-hardware validation) — it's a pure
API-layer swap, not a transport or ingestion change. Do this fully before or
fully after M14, not interleaved with it, so any bugs surfaced during
real-hardware testing aren't confounded with an API-layer rewrite happening
at the same time. If timing is unclear, confirm with RS before starting
Phase 0.
