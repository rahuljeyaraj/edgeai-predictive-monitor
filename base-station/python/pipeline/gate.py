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

The threshold is RELATIVE to this node's own commissioned running energy
(`RegistryEntry.running_energy_ref`, calibrated in pipeline/
commissioning.py alongside the warning/fault thresholds), not an absolute
number -- for exactly the reason the anomaly thresholds are per-node too
(registry.py's warning_threshold comment): compute_energy() below is an
RMS over unnormalized FFT magnitudes, so its scale depends entirely on
that node's accelerometer range, gain, and physical mounting. A number
that means "stopped" for one node is orders of magnitude off for another.

This was a real bug, not a hypothetical: the absolute default was 0.05
(main.py's --gate-threshold) while real accel captures measure ~19,000 --
a 250,000x margin, i.e. ~-108 dB of attenuation needed before a frame
would ever read STOPPED. MotorState.STOPPED was unreachable on real
hardware, so the "suppress inference/training while stopped" behaviour
S3.2/S3.5 both specify has never actually engaged live. It went unnoticed
because gate_test.py's synthetic bins are single-digit values, a unit
scale hardware never produces.

Nodes commissioned before running_energy_ref existed have it as None; for
those, the gate falls back to the absolute `threshold` -- which reproduces
the old always-RUNNING behaviour exactly rather than changing what a
not-yet-re-commissioned node does underneath an operator.

compute_energy() only sums accelerometer channels, not mic -- see its own
docstring. Nodes commissioned before that change have a running_energy_ref
computed the old, mic-inclusive way; re-commissioning recalculates it
against the same accel-only definition the live gate now uses, the same
re-commissioning requirement the running_energy_ref bug above already
established.
"""
import logging
import math
from enum import Enum
from typing import Callable, Optional

from sensor_frame import SensorFrame

logger = logging.getLogger(__name__)

# Fraction of a node's commissioned running energy below which it reads
# STOPPED. Deliberately low: the trip feature needs to detect a motor that
# has stopped while its neighbours keep shaking the shared rig frame
# (docs/MOTOR_STOP_PLAN.md), so this has to sit under the cross-talk floor
# but above the sensor's own noise floor. Tunable per deployment via
# main.py's --gate-running-fraction, since how much a neighbouring motor
# leaks through a shared frame is a property of the rig, not of the code.
DEFAULT_RUNNING_FRACTION = 0.15


class MotorState(Enum):
    STOPPED = "stopped"
    RUNNING = "running"


def compute_energy(frame: SensorFrame) -> float:
    """RMS across this frame's accelerometer channels when it has any,
    mic excluded deliberately -- whether a motor is turning is a mechanical
    fact, and a microphone picks up ambient room/electrical noise that has
    nothing to do with it, which raises the STOPPED-reading floor for no
    benefit (a live rig with the motors fully powered down still read a
    "running"-range energy because ambient mic pickup alone cleared the
    threshold). Falls back to every channel present (i.e. mic) for a
    mic-only sensor_config (features.py's test_single_sensor_mic_only) --
    those nodes have no accelerometer to prefer, so mic is all there is."""
    accel_bins = [b for chan, chan_bins in frame.bins.items()
                  if chan.startswith("accel") for b in chan_bins]
    bins = accel_bins or [b for chan_bins in frame.bins.values() for b in chan_bins]
    if not bins:
        return 0.0
    return math.sqrt(sum(b * b for b in bins) / len(bins))


class MotorStateGate:
    def __init__(self, threshold: float, debounce_frames: int = 3,
                 initial_state: MotorState = MotorState.STOPPED,
                 energy_ref_provider: Optional[Callable[[], Optional[float]]] = None,
                 running_fraction: float = DEFAULT_RUNNING_FRACTION):
        """energy_ref_provider: zero-arg callable returning this node's
        current `RegistryEntry.running_energy_ref`, or None if it has never
        been commissioned since that field existed. Read fresh on every
        update() rather than captured once at construction, so a
        re-commissioning that recalibrates the reference takes effect
        immediately -- gates are long-lived (MotorPipeline builds its
        classification gate once and never rebuilds it, manager.py) and
        would otherwise keep comparing against a stale baseline.

        A plain callable rather than a Registry reference keeps this module
        free of a registry import, the same isolation registry.py's own
        _DIM_BY_CHANNEL comment protects in the other direction.

        threshold: the absolute fallback, used only when no reference is
        available (see the module docstring).
        """
        if debounce_frames < 1:
            raise ValueError("debounce_frames must be >= 1")
        if not 0.0 < running_fraction < 1.0:
            raise ValueError("running_fraction must be between 0 and 1 exclusive")
        self._threshold = threshold
        self._debounce_frames = debounce_frames
        self._state = initial_state
        self._candidate_state: Optional[MotorState] = None
        self._candidate_count = 0
        self._energy_ref_provider = energy_ref_provider
        self._running_fraction = running_fraction
        # Most recent measured energy and the threshold it was compared
        # against, purely for observability -- picking a --gate-running-
        # fraction for a given rig is an empirical exercise (how much a
        # neighbouring motor leaks through the frame), so these are what
        # make it tunable from real numbers instead of guesswork.
        self._last_energy: Optional[float] = None
        self._last_threshold: Optional[float] = None

    @property
    def state(self) -> MotorState:
        return self._state

    @property
    def last_energy(self) -> Optional[float]:
        return self._last_energy

    @property
    def last_threshold(self) -> Optional[float]:
        return self._last_threshold

    def _effective_threshold(self) -> float:
        """Relative to this node's commissioned running energy when we have
        one, else the absolute fallback."""
        if self._energy_ref_provider is None:
            return self._threshold
        ref = self._energy_ref_provider()
        if ref is None or ref <= 0:
            # Not commissioned since running_energy_ref existed (or a
            # degenerate all-zero baseline, e.g. a node whose sensor was
            # dead throughout commissioning) -- fall back rather than
            # dividing meaning out of a zero reference.
            return self._threshold
        return ref * self._running_fraction

    def update(self, frame: SensorFrame) -> MotorState:
        energy = compute_energy(frame)
        threshold = self._effective_threshold()
        self._last_energy = energy
        self._last_threshold = threshold
        raw_state = (MotorState.RUNNING if energy >= threshold
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
