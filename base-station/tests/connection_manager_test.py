#!/usr/bin/env python3
"""Verifies api/connection_manager.py's coalescing delivery.

The behavior under test is what keeps the dashboard smooth when a client
cannot drain at the ingestion frame rate: high-rate telemetry must degrade
to a lower frame rate showing *current* data, never to a growing backlog of
stale frames. The regression this guards against is the original inline-send
manager, where main.py's per-frame broadcast_threadsafe() handed the event
loop one unbounded coroutine per frame per client.

Run with PYTHONPATH covering base-station/python/api:
    PYTHONPATH=base-station/python/api python3 base-station/tests/connection_manager_test.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "python", "api"))

from connection_manager import ConnectionManager, MAX_RELIABLE_BACKLOG

FAILURES = []


def check(label, condition):
    print(f"{label}: {'PASS' if condition else 'FAIL'}")
    if not condition:
        FAILURES.append(label)


class FakeWebSocket:
    """A socket that only completes a send when the test lets it, so the
    'client is behind' window is explicit rather than timing-dependent."""

    def __init__(self):
        self.sent = []
        self.gate = asyncio.Event()
        self.in_flight = asyncio.Event()

    async def accept(self):
        pass

    async def send_json(self, message):
        self.in_flight.set()
        await self.gate.wait()
        self.gate.clear()
        self.in_flight.clear()
        self.sent.append(message)

    async def release(self):
        """Lets exactly one queued send complete."""
        await self.in_flight.wait()
        self.gate.set()
        # Yield until the writer has parked on the next send (or gone idle).
        for _ in range(50):
            await asyncio.sleep(0)
            if not self.gate.is_set():
                break


def spectrum(node_id, seq):
    return {"type": "spectrum", "node_id": node_id, "seq": seq}


async def test_stale_frames_coalesce():
    manager = ConnectionManager()
    ws = FakeWebSocket()
    await manager.connect(ws)

    # Frame 1 goes in flight; 2..20 arrive while the client is still blocked.
    for seq in range(1, 21):
        await manager.broadcast(spectrum("base_station", seq))
        await asyncio.sleep(0)

    await ws.release()  # frame 1 lands
    await ws.release()  # whatever coalesced behind it lands

    seqs = [m["seq"] for m in ws.sent]
    check("a client that is behind receives the NEWEST spectrum, not the next stale one",
          seqs == [1, 20])
    check("the 18 superseded frames are dropped, not queued",
          manager._outboxes[ws].dropped == 18)
    manager.disconnect(ws)


async def test_per_node_fairness():
    manager = ConnectionManager()
    ws = FakeWebSocket()
    await manager.connect(ws)

    await manager.broadcast(spectrum("base_station", 1))
    await asyncio.sleep(0)
    # base_station is now in flight. Both nodes then update repeatedly.
    for seq in range(2, 6):
        await manager.broadcast(spectrum("base_station", seq))
        await manager.broadcast(spectrum("e36428", seq))
        await asyncio.sleep(0)

    for _ in range(3):
        await ws.release()

    nodes = [m["node_id"] for m in ws.sent]
    check("a busy node does not starve a quieter one (both node ids delivered)",
          "e36428" in nodes and "base_station" in nodes)
    check("each node delivers only its own latest frame",
          [m["seq"] for m in ws.sent if m["node_id"] == "e36428"] == [5])
    manager.disconnect(ws)


async def test_state_transitions_are_never_dropped():
    manager = ConnectionManager()
    ws = FakeWebSocket()
    await manager.connect(ws)

    await manager.broadcast(spectrum("base_station", 1))
    await asyncio.sleep(0)
    # These are edges, not samples -- every one must survive.
    for step in range(4):
        await manager.broadcast({"type": "registry", "node_id": "base_station", "step": step})
        await manager.broadcast(spectrum("base_station", 100 + step))
        await asyncio.sleep(0)

    for _ in range(6):
        await ws.release()

    steps = [m["step"] for m in ws.sent if m["type"] == "registry"]
    check("every non-telemetry state transition is delivered despite the backlog",
          steps == [0, 1, 2, 3])
    check("state transitions overtake queued telemetry rather than sitting behind it",
          [m["type"] for m in ws.sent][1:5] == ["registry"] * 4)
    manager.disconnect(ws)


async def test_wedged_client_is_dropped_not_grown():
    manager = ConnectionManager()
    ws = FakeWebSocket()
    await manager.connect(ws)

    await manager.broadcast(spectrum("base_station", 1))
    await asyncio.sleep(0)
    for i in range(MAX_RELIABLE_BACKLOG + 50):
        await manager.broadcast({"type": "registry", "node_id": "n", "i": i})
    await asyncio.sleep(0)

    outbox = manager._outboxes[ws]
    check("a client that never drains stops accumulating at MAX_RELIABLE_BACKLOG",
          len(outbox._reliable) <= MAX_RELIABLE_BACKLOG and outbox.overflowed)

    # Releasing lets the writer notice the overflow and evict the client.
    await ws.release()
    for _ in range(50):
        await asyncio.sleep(0)
    check("the wedged client is disconnected rather than held forever",
          ws not in manager._outboxes)


async def test_broadcast_does_not_wait_for_a_slow_client():
    manager = ConnectionManager()
    slow, fast = FakeWebSocket(), FakeWebSocket()
    await manager.connect(slow)
    await manager.connect(fast)

    await manager.broadcast(spectrum("base_station", 1))
    await asyncio.sleep(0)
    # `slow` is blocked mid-send and never released.
    await asyncio.wait_for(manager.broadcast(spectrum("base_station", 2)), timeout=1.0)
    await ws_drain(fast, 2)

    check("one blocked client does not stall broadcast for the others",
          [m["seq"] for m in fast.sent] == [1, 2])
    manager.disconnect(slow)
    manager.disconnect(fast)


async def ws_drain(ws, count):
    for _ in range(count):
        await ws.release()


async def main():
    await test_stale_frames_coalesce()
    await test_per_node_fairness()
    await test_state_transitions_are_never_dropped()
    await test_wedged_client_is_dropped_not_grown()
    await test_broadcast_does_not_wait_for_a_slow_client()

    print()
    if FAILURES:
        print(f"RESULT: FAIL - {len(FAILURES)} check(s) failed: {'; '.join(FAILURES)}")
        sys.exit(1)
    print("RESULT: PASS - slow clients coalesce to the latest telemetry, "
          "state transitions always arrive, and no client can stall another")


asyncio.run(main())
