#!/usr/bin/env python3
"""
Milestone 4 verification: feed
synthetic frames with varying energy across the stopped/running boundary
and confirm gate output matches expected state transitions, with no
flapping on single noisy frames.

Run with PYTHONPATH covering base-station/python/ingestion and base-station/python/pipeline:
    PYTHONPATH=base-station/python/ingestion:base-station/python/pipeline python3 base-station/tests/gate_test.py
"""
import sys

from sensor_frame import FrameSource, SensorFrame
from gate import MotorState, MotorStateGate, compute_energy

THRESHOLD = 1.0
DEBOUNCE = 3


def frame(accel_bins, mic_bins=None) -> SensorFrame:
    bins = {"accel": accel_bins}
    if mic_bins is not None:
        bins["mic"] = mic_bins
    return SensorFrame(node_id="node-1", source=FrameSource.SPI, timestamp=0.0, bins=bins)


LOW = frame((0.01, 0.02, 0.01))     # well under threshold -- "stopped"
HIGH = frame((3.0, 4.0, 5.0))       # well over threshold -- "running"


def main():
    gate = MotorStateGate(threshold=THRESHOLD, debounce_frames=DEBOUNCE)
    assert gate.state == MotorState.STOPPED, gate.state
    print("initial state defaults to STOPPED: PASS")

    # Fewer than debounce_frames high-energy frames must not flip state yet.
    for _ in range(DEBOUNCE - 1):
        state = gate.update(HIGH)
    assert state == MotorState.STOPPED, state
    print("state does not flip before debounce_frames consecutive frames: PASS")

    # The Nth consecutive high-energy frame flips it.
    state = gate.update(HIGH)
    assert state == MotorState.RUNNING, state
    print("state flips to RUNNING after debounce_frames consecutive high-energy frames: PASS")

    # A single low-energy frame amid running frames must not flap the state.
    state = gate.update(LOW)
    assert state == MotorState.RUNNING, state
    state = gate.update(HIGH)
    assert state == MotorState.RUNNING, state
    print("single noisy frame does not flap confirmed RUNNING state: PASS")

    # debounce_frames consecutive low-energy frames flip it back.
    for _ in range(DEBOUNCE - 1):
        state = gate.update(LOW)
    assert state == MotorState.RUNNING, state
    state = gate.update(LOW)
    assert state == MotorState.STOPPED, state
    print("state flips back to STOPPED after debounce_frames consecutive low-energy frames: PASS")

    # A candidate flip must restart, not accumulate, if the raw signal
    # alternates rather than staying consistently on the new side.
    gate2 = MotorStateGate(threshold=THRESHOLD, debounce_frames=DEBOUNCE)
    for _ in range(10):
        gate2.update(HIGH)
        gate2.update(LOW)
    assert gate2.state == MotorState.STOPPED, gate2.state
    print("alternating energy never accumulates into a flip: PASS")

    # Bins-absent frame (both sensors disabled) reads as zero energy, no crash.
    empty = SensorFrame(node_id="node-1", source=FrameSource.SPI, timestamp=0.0, bins={})
    assert compute_energy(empty) == 0.0
    print("frame with no bins present computes zero energy without error: PASS")

    print("RESULT: PASS - gate transitions correctly, no flapping")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
