"""Motor state gate -- running/stopped detection with debounce, per
docs/MPU_Software_Architecture.md S3.2/S8 M4.

Resolves open question #3 (S6): wire_protocol.py's SPECTRUM payload
carries only mic/accel bins, no MCU-supplied running/stopped flag (and
MCU_Software_Architecture.md has no such field either), so gating is a
Python-side RMS-energy threshold over whichever bins are present on the
frame instead.

One MotorStateGate per motor pipeline. update() is fed every SensorFrame
for that node and returns the *confirmed* MotorState -- a single frame
crossing the threshold is not enough to flip it; `debounce_frames`
consecutive frames must agree first, so one noisy frame at the
running/stopped boundary can't flap the state (S3.2).
"""
import math
from enum import Enum
from typing import Optional

from sensor_frame import SensorFrame


class MotorState(Enum):
    STOPPED = "stopped"
    RUNNING = "running"


def compute_energy(frame: SensorFrame) -> float:
    """RMS across whatever channels are present in frame.bins (mic, accel,
    or any future channel) -- a channel absent from the dict (S4.1)
    simply doesn't contribute."""
    bins = [b for chan_bins in frame.bins.values() for b in chan_bins]
    if not bins:
        return 0.0
    return math.sqrt(sum(b * b for b in bins) / len(bins))


class MotorStateGate:
    def __init__(self, threshold: float, debounce_frames: int = 3,
                 initial_state: MotorState = MotorState.STOPPED):
        if debounce_frames < 1:
            raise ValueError("debounce_frames must be >= 1")
        self._threshold = threshold
        self._debounce_frames = debounce_frames
        self._state = initial_state
        self._candidate_state: Optional[MotorState] = None
        self._candidate_count = 0

    @property
    def state(self) -> MotorState:
        return self._state

    def update(self, frame: SensorFrame) -> MotorState:
        raw_state = (MotorState.RUNNING if compute_energy(frame) >= self._threshold
                     else MotorState.STOPPED)

        if raw_state == self._state:
            # Back in line with the confirmed state -- any in-progress
            # flip attempt is stale, drop it.
            self._candidate_state = None
            self._candidate_count = 0
            return self._state

        if raw_state == self._candidate_state:
            self._candidate_count += 1
        else:
            self._candidate_state = raw_state
            self._candidate_count = 1

        if self._candidate_count >= self._debounce_frames:
            self._state = raw_state
            self._candidate_state = None
            self._candidate_count = 0

        return self._state
