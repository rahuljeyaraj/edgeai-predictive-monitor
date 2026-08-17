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

Excluding mic was necessary but NOT sufficient, and the reason is the
third and last layer of this same bug (docs/progress4.md S4 left it open,
suspecting a DC/gravity term in the bins -- it isn't that: accel_sampler.
cpp's accel_fft_magnitude() already discards bin 0, so gravity never
reaches these bins at all). Measured live on the real rig, per pooled bin,
accelerometer only:

    bin (Hz)     stopped     running (90rpm)     delta
    2   (131)     13192           36134         +22942
    5   (281)     12680           44798         +32118
    12  (631)     13453           13545            +92
    24 (1231)     11217           11482           +265
    64 (3231)      5525            5483            -42

The motor's actual signature is a handful of narrow lines below ~600Hz.
Every other bin -- ~360 of the 384 an accel-only frame carries -- is the
KX134's own broadband noise floor at ACCEL_ODR_HZ=12800, and that floor is
IDENTICAL whether the machine runs or not. An RMS over all of them is
therefore mostly a measurement of the sensor's noise, which is why a
stopped rig read ~7500 against a commissioned running reference of ~11400:
a 1.18x worst-case margin, far too thin to gate on, and the same
"MotorState.STOPPED is unreachable in practice" failure the two layers
above already produced.

The fix is to subtract that floor rather than trying to find a fraction
between two numbers that are mostly the same number. A node can commission
a STOPPED baseline (RegistryEntry.stopped_spectrum_ref, captured with the
machine deliberately off -- pipeline/stopped_baseline.py), and
compute_energy() then measures only each bin's excess over it. On the same
live rig that turns a 1.18x margin into 2.09x, with the medians 4.38x
apart instead of 1.49x:

                    stopped      running     worst-case gap
    all bins RMS       7480        11137           1.18x
    excess over ref    1414         6194           2.09x

A node with a stopped baseline gates on `stopped_energy_ref *
DEFAULT_STOPPED_MARGIN` -- a multiple of what this machine's own silence
measures -- and needs no running reference at all, so capturing one does
not invalidate an existing commissioning or force a retrain. Nodes without
one keep the running_fraction path below, unchanged.
"""
import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Mapping, Optional, Sequence, Tuple

from sensor_frame import SensorFrame

logger = logging.getLogger(__name__)

# Fraction of a node's commissioned running energy below which it reads
# STOPPED. Deliberately low: the trip feature needs to detect a motor that
# has stopped while its neighbours keep shaking the shared rig frame
# (docs/MOTOR_STOP_PLAN.md), so this has to sit under the cross-talk floor
# but above the sensor's own noise floor. Tunable per deployment via
# main.py's --gate-running-fraction, since how much a neighbouring motor
# leaks through a shared frame is a property of the rig, not of the code.
#
# Only used by nodes with no stopped baseline -- see StoppedBaseline below,
# which supersedes this whole approach where it's available.
DEFAULT_RUNNING_FRACTION = 0.15

# How far above its own stopped-baseline energy a node has to measure
# before it reads RUNNING. Applies to the noise-floor-subtracted energy,
# not the raw RMS, so it is a multiple of what this machine's *silence*
# measures rather than a fraction of what its motion measures.
#
# 1.75 sits near the geometric middle of the live-measured window on the
# real rig: the loudest stopped frame came to 1.18x the stopped median and
# the quietest running frame to 4.38x it, so anything in roughly 1.2-4.4
# separates them and 1.75 keeps ~1.5x of headroom on both sides. Tunable
# via main.py's --gate-stopped-margin.
DEFAULT_STOPPED_MARGIN = 1.75

# Once RUNNING is confirmed, how far energy has to fall below the
# RUNNING threshold (as a fraction of it) before the gate reads STOPPED
# again. Real running-energy on a shared rig isn't flat -- a stopped
# baseline that picked up some cross-talk from a neighbouring motor
# (module docstring's 1.18x-worst-case-margin problem) leaves the plain
# threshold close enough to real running energy that debounce_frames of
# quiet-looking frames happen naturally while the motor keeps turning,
# which reads as STOPPED and silently drops those frames from whatever is
# collecting (commissioning.py/capture.py feed_frame() both only keep
# RUNNING-confirmed frames) -- live-measured on node e36428 at 2.8 raw
# frames/sec but only ~1/sec landing in the commissioning count, a ~65%
# loss with no error or visible symptom besides "recording is slow"
# while the spectrum view (which shows every frame, gated or not) looks
# fine.
#
# NOT used as MotorStateGate's own default (that stays 1.0, a no-op --
# see its __init__) because a gate isn't only used for collection: the
# same gate_factory also builds protection's trip-detection gate
# (pipeline/manager.py), where the whole point is noticing a real stop
# promptly. Loosening exit-from-RUNNING there would delay detecting a
# motor that has genuinely stopped -- exactly what
# gate_test.py's test_crosstalk_below_the_fraction_still_reads_stopped
# guards against. main.py instead builds a second gate_factory with this
# value, wired only to CommissioningController/CaptureController, so an
# operator's deliberate "Start recording" gets the leniency and
# protection's trip gate does not. Tunable via main.py's
# --gate-running-hysteresis.
DEFAULT_RUNNING_HYSTERESIS = 0.5


@dataclass(frozen=True)
class StoppedBaseline:
    """What a node measured with its machine deliberately stopped, captured
    by pipeline/stopped_baseline.py and persisted on its registry entry.

    spectrum: per-channel, per-bin median magnitude -- the sensor's own
    noise floor at this mounting point, subtracted from every live frame.
    energy: the median energy those same stopped frames produce *after*
    that subtraction (near zero in principle, but not actually zero -- the
    floor is only constant on average, and what's left is its frame-to-
    frame variance). This is what the threshold scales from, so the gate
    asks "is this frame further above the floor than the floor's own
    jitter", which is the question the noise makes hard, rather than
    re-deriving it from a running reference on a different scale.
    """
    spectrum: Mapping[str, Sequence[float]]
    energy: float


class MotorState(Enum):
    STOPPED = "stopped"
    RUNNING = "running"


def energy_channels(frame: SensorFrame) -> List[str]:
    """Which of this frame's channels the running/stopped decision reads:
    the accelerometer ones when it has any, mic excluded deliberately --
    whether a motor is turning is a mechanical fact, and a microphone picks
    up ambient room/electrical noise that has nothing to do with it, which
    raises the STOPPED-reading floor for no benefit (a live rig with the
    motors fully powered down still read a "running"-range energy because
    ambient mic pickup alone cleared the threshold). Falls back to every
    channel present (i.e. mic) for a mic-only sensor_config (features.py's
    test_single_sensor_mic_only) -- those nodes have no accelerometer to
    prefer, so mic is all there is."""
    accel = [chan for chan in frame.bins if chan.startswith("accel")]
    return accel or list(frame.bins)


def excess_over_stopped(frame: SensorFrame,
                         stopped: Optional[StoppedBaseline]) -> Optional[List[float]]:
    """Per-bin magnitude above `stopped`'s noise floor, clamped at zero
    (a bin below the floor carries no evidence of motion, and letting it go
    negative would let a quiet bin cancel out a real line elsewhere).

    None when the baseline can't be applied to this frame -- no baseline at
    all, or one that doesn't cover exactly the channels/bin counts this
    frame carries. Refusing rather than subtracting what fits is deliberate:
    a partially-subtracted energy and a fully-subtracted threshold are on
    different scales, and silently mixing them is precisely the class of bug
    (see the module docstring) this function exists to end. A node whose
    sensor_config or bin count changed since its baseline was captured
    re-reads as "no baseline" and falls back, until it captures a new one.
    """
    if stopped is None:
        return None
    out: List[float] = []
    for chan in energy_channels(frame):
        ref = stopped.spectrum.get(chan)
        chan_bins = frame.bins[chan]
        if ref is None or len(ref) != len(chan_bins):
            return None
        out.extend(max(b - r, 0.0) for b, r in zip(chan_bins, ref))
    return out


def _rms(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return math.sqrt(sum(v * v for v in values) / len(values))


def compute_energy(frame: SensorFrame,
                    stopped: Optional[StoppedBaseline] = None) -> float:
    """How much vibration this frame carries, over energy_channels(frame).

    With a `stopped` baseline this is an RMS of each bin's excess over that
    node's own measured noise floor; without one it's a plain RMS of the
    raw magnitudes, which is what every node did before baselines existed.
    The two are on wildly different scales (the module docstring's table:
    ~1400 vs ~7500 on the same stopped rig) -- callers that persist an
    energy must persist which of the two produced it, which is why
    stopped_energy_ref lives on StoppedBaseline itself rather than beside
    running_energy_ref."""
    excess = excess_over_stopped(frame, stopped)
    if excess is not None:
        return _rms(excess)
    return _rms([b for chan in energy_channels(frame) for b in frame.bins[chan]])


class MotorStateGate:
    def __init__(self, threshold: float, debounce_frames: int = 3,
                 initial_state: MotorState = MotorState.STOPPED,
                 energy_ref_provider: Optional[Callable[[], Optional[float]]] = None,
                 running_fraction: float = DEFAULT_RUNNING_FRACTION,
                 stopped_provider: Optional[Callable[[], Optional[StoppedBaseline]]] = None,
                 stopped_margin: float = DEFAULT_STOPPED_MARGIN,
                 running_hysteresis: float = 1.0):
        """energy_ref_provider: zero-arg callable returning this node's
        current `RegistryEntry.running_energy_ref`, or None if it has never
        been commissioned since that field existed. Read fresh on every
        update() rather than captured once at construction, so a
        re-commissioning that recalibrates the reference takes effect
        immediately -- gates are long-lived (MotorPipeline builds its
        classification gate once and never rebuilds it, manager.py) and
        would otherwise keep comparing against a stale baseline.

        stopped_provider: the same arrangement for this node's
        StoppedBaseline, and read fresh for the same reason (capturing a
        baseline has to take effect on the next frame, not the next
        restart). When it yields one that fits the frame, it wins outright
        over energy_ref_provider -- it measures the thing the running
        reference could only approximate, see the module docstring.

        Plain callables rather than a Registry reference keep this module
        free of a registry import, the same isolation registry.py's own
        _DIM_BY_CHANNEL comment protects in the other direction.

        threshold: the absolute fallback, used only when neither reference
        is available (see the module docstring).

        running_hysteresis: see DEFAULT_RUNNING_HYSTERESIS's docstring for
        what this trades off. Defaults to 1.0, a no-op (exit-from-RUNNING
        uses the same threshold as entering it, today's behaviour) --
        callers that want the collection-friendly leniency pass
        DEFAULT_RUNNING_HYSTERESIS explicitly. Applied only while
        confirmed RUNNING, never while STOPPED.
        """
        if debounce_frames < 1:
            raise ValueError("debounce_frames must be >= 1")
        if not 0.0 < running_fraction < 1.0:
            raise ValueError("running_fraction must be between 0 and 1 exclusive")
        if stopped_margin <= 1.0:
            raise ValueError("stopped_margin must be greater than 1")
        if not 0.0 < running_hysteresis <= 1.0:
            raise ValueError("running_hysteresis must be between 0 (exclusive) and 1 (inclusive)")
        self._threshold = threshold
        self._debounce_frames = debounce_frames
        self._running_hysteresis = running_hysteresis
        self._state = initial_state
        self._candidate_state: Optional[MotorState] = None
        self._candidate_count = 0
        self._energy_ref_provider = energy_ref_provider
        self._running_fraction = running_fraction
        self._stopped_provider = stopped_provider
        self._stopped_margin = stopped_margin
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

    def _running_threshold(self) -> float:
        """Relative to this node's commissioned running energy when we have
        one, else the absolute fallback. Only reached when no stopped
        baseline applies -- see _measure()."""
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

    def _measure(self, frame: SensorFrame) -> Tuple[float, float]:
        """(energy, threshold) for this frame, both from the same reference
        so they're always on the same scale -- computed together rather than
        by two independent lookups precisely because mixing a subtracted
        energy with an unsubtracted threshold is the failure this design
        guards against (see the module docstring)."""
        stopped = self._stopped_provider() if self._stopped_provider else None
        excess = excess_over_stopped(frame, stopped)
        if excess is not None and stopped.energy > 0:
            return _rms(excess), stopped.energy * self._stopped_margin
        # No baseline, one that doesn't fit this frame's channels, or a
        # degenerate zero-energy one (a sensor dead throughout the stopped
        # capture would read a perfectly constant floor) -- measure and
        # threshold the pre-baseline way instead, both unsubtracted.
        return compute_energy(frame), self._running_threshold()

    def update(self, frame: SensorFrame) -> MotorState:
        energy, threshold = self._measure(frame)
        self._last_energy = energy
        self._last_threshold = threshold
        # Hysteresis (DEFAULT_RUNNING_HYSTERESIS's docstring): while
        # confirmed RUNNING, only a drop below a fraction of `threshold`
        # counts as a STOPPED reading -- crossing `threshold` itself only
        # matters for the STOPPED -> RUNNING direction.
        exit_threshold = (threshold * self._running_hysteresis
                           if self._state == MotorState.RUNNING else threshold)
        raw_state = (MotorState.RUNNING if energy >= exit_threshold
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
