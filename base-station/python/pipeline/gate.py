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

Subtracting the floor is still not enough on every mounting. A node whose
own noise floor is unusually jittery (measured: 1.91x frame-to-frame spread
against another node's 1.37x on the same rig, in the same minute, after its
firmware-side FIFO-overrun bug was fixed) fails both directions of this at
once -- pipeline/stopped_baseline.py refuses to fit a baseline it cannot put
a threshold above, and if one is forced through, real running frames flicker
either side of a threshold scaled up from that inflated floor and get
silently dropped from whatever is collecting. The last layer is therefore to
average consecutive frames' spectra before measuring them at all
(SpectrumAverager / DEFAULT_SMOOTHING_FRAMES), which shrinks the noise that
does not repeat frame to frame while leaving the motor lines that do.

And a floor that is only constant on average is not constant over hours.
Averaging fixes the jitter this floor has frame to frame; it does nothing
about the floor moving as a whole, which it does -- measured an hour after
a capture, on a genuinely stopped machine, every bin of all three accel
channels sat at a flat 1.11-1.13x its own stopped_spectrum_ref. Because
stopped_energy_ref is only ~8% of the floor it was subtracted from, a
uniform drift of ~14% is enough to hold the gate at RUNNING forever, which
is what it did: 290 of 290 stopped frames read RUNNING, and the trip that
had actually stopped that machine was reported as failed. So the floor is
scaled to the frame before it is subtracted (floor_gain), which measures
the drift from the ~380 bins the motor never reaches and leaves the handful
it does. Full numbers in floor_gain's own docstring.
"""
import logging
import math
import statistics
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import (Callable, Deque, Dict, List, Mapping, Optional, Sequence,
                     Tuple)

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


# How many consecutive frames' spectra a node's gate averages together
# before measuring energy. Not a filter in the DSP sense -- band-limiting
# was measured and made things *worse* (1.09x, the module docstring's
# "the noise floor is tallest in exactly the low bins where the signal
# lives"). This averages across TIME, where the noise really is
# independent frame to frame and the motor's lines are not.
#
# Why it works, measured on node e36428 (docs: the satellite idle-vibration
# diagnosis): after the FIFO-overrun firmware fix its 30-frame-window spread
# still sat at 1.91x against base_station's 1.37x in the same room, i.e. the
# residual is that mounting's own jitter, not a bug left to find. Frame-to-
# frame lag-1 autocorrelation of the excess energy measured only +0.17, so
# the frames are near-independent and a K-frame mean cuts that jitter by
# roughly 1/sqrt(K) -- 1.91x -> ~1.46x at K=4, comfortably back under
# DEFAULT_STOPPED_MARGIN.
#
# It helps the RUNNING side twice over, which is the point that makes this
# the durable fix rather than a way to squeak a baseline past its check.
# Averaging the *spectra* (not the resulting energies) before
# excess_over_stopped() clamps them at zero means the rectified noise the
# floor leaves behind shrinks with K while a real motor line -- present in
# every frame, well above the floor -- does not. So stopped_energy_ref
# falls, the threshold scaled from it falls with it, and running energy
# stays put: the two states move further apart rather than the line moving
# up. Averaging energies after the clamp would do none of that.
#
# 6 rather than 4, chosen by measurement, not by the 1/sqrt(K) arithmetic.
# 251 live frames off e36428 (machine off, 50.9s, 4.93 fps), scored as the
# sliding 150-frame captures an operator would actually take:
#
#   K   accepted    median spread   worst    energy_ref   gate threshold
#   1    0 of 21        2.11x       2.21x       2705          4734
#   4   15 of 21        1.70x       1.79x       1333          2333
#   6   21 of 21        1.56x       1.64x       1089          1906
#
# K=4 works on a good capture and fails on an unlucky one; K=6 had margin on
# every window measured. Above 6 the returns flatten and the worst case
# starts drifting back up (the residual is not purely white -- there is a
# slower wander that a longer average cannot cancel), so this is a measured
# optimum rather than "more is better".
#
# The right-hand column is the other half of the point. The gate threshold
# is stopped_energy_ref * DEFAULT_STOPPED_MARGIN, so averaging lowers the
# line a running machine has to clear by 2.5x (4734 -> 1906) while leaving
# the running energy that clears it alone. That is what stops this from
# being a way to squeak a bad baseline past its check and into the next
# problem: the alternative fix, raising MAX_STOPPED_SPREAD or scaling the
# margin per node, would have accepted the same baseline and then starved
# commissioning's next step of frames, since both numbers are multiples of
# the same inflated floor.
#
# 6 frames is ~1.2s at the satellite's ~4.9 fps. That lands on protection's
# trip gate too, whose own DEFAULT_TRIP_DELAY_S is 10s, so it is noise
# against the delay already there by design.
DEFAULT_SMOOTHING_FRAMES = 6

# The most floor drift floor_gain() will scale away, in either direction.
# Above this the frame is measured against the unscaled floor instead, which
# is what every node did before the gain existed.
#
# This bound is what keeps the gain a DRIFT correction rather than a general
# normalization, and that distinction is the whole safety argument. An
# unbounded median-of-ratios cannot tell "the floor rose 12%" from "the
# machine came on and lifted every bin equally" -- it would divide both out
# and report silence for a running machine, which is the one error this
# module must never make. Bounding it means the most the gate can ever
# subtract is MAX_FLOOR_GAIN x the commissioned floor, so a machine whose
# signature clears that survives no matter what the estimator does.
#
# 1.5 against a measured drift of 1.12 (floor_gain's docstring) is ~4x the
# headroom actually needed, while a real running machine on this rig sits
# far outside it -- pooled over its bins, node 194584's running spectrum
# measured 6.7x its stopped floor. There is a wide gap between the two
# populations and this sits in it, so the exact value is not delicate.
MAX_FLOOR_GAIN = 1.5

# Fewest bins floor_gain() will estimate a gain from. Below this it returns
# 1.0 (the old, unscaled behaviour) rather than a number it cannot support:
# the estimator's premise is that most bins carry floor and only a few carry
# machine, and a handful of bins cannot establish "most". Real frames are
# nowhere near this line -- a mic-only node carries 128 bins and an
# accel-only one 384 -- so this only ever catches the degenerate case.
MIN_FLOOR_GAIN_BINS = 32


class BinsFrame:
    """The two attributes excess_over_stopped()/energy_channels() actually
    read. Lets this module and pipeline/stopped_baseline.py measure derived
    bins (a rolling mean, stored capture frames) through exactly the same
    functions a live SensorFrame goes through, instead of either duplicating
    the maths or keeping whole SensorFrames alive."""

    __slots__ = ("node_id", "bins")

    def __init__(self, node_id: str, bins: Mapping[str, Sequence[float]]):
        self.node_id = node_id
        self.bins = bins


class SpectrumAverager:
    """Rolling per-bin mean of the last k frames, per channel.

    k is passed to push() rather than fixed at construction because it is a
    property of the node's stored StoppedBaseline (see its smoothing_frames
    field), which the gate re-reads on every frame so that a freshly
    captured baseline takes effect immediately -- the same reason
    MotorStateGate holds providers rather than values.

    Resets on any change to the channel set or bin counts: averaging across
    a sensor_config change would mix two different measurements into one
    frame, and the shape check is (channel, bin_count) pairs for the same
    reason pipeline/stopped_baseline.py commits to that shape.
    """

    def __init__(self) -> None:
        self._window: Deque[Dict[str, Tuple[float, ...]]] = deque()
        self._shape: Optional[Tuple[Tuple[str, int], ...]] = None

    def push(self, frame, k: int):
        """Returns `frame` itself when k <= 1 or only one frame is buffered
        (the no-op path pays no copy), else a BinsFrame of the mean. A
        partial window is averaged as-is rather than waiting for k frames:
        a gate that returned nothing for its first frames would stall
        collection at exactly the moment an operator has just pressed
        Start."""
        shape = tuple((chan, len(frame.bins[chan])) for chan in sorted(frame.bins))
        if shape != self._shape:
            self._window.clear()
            self._shape = shape
        if k <= 1:
            # Keep no history: k can change under us (a re-captured
            # baseline), and stale frames from before that change must not
            # leak into the first averaged frame after it.
            self._window.clear()
            return frame
        self._window.append({chan: tuple(frame.bins[chan]) for chan, _ in shape})
        while len(self._window) > k:
            self._window.popleft()
        if len(self._window) == 1:
            return frame
        count = len(self._window)
        averaged = {
            chan: tuple(sum(values) / count
                        for values in zip(*(f[chan] for f in self._window)))
            for chan, _ in self._shape
        }
        return BinsFrame(frame.node_id, averaged)


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
    smoothing_frames: how many consecutive frames were averaged together
    (SpectrumAverager) before both of the above were measured, and
    therefore how many the gate must average before measuring a live frame
    against them. Persisted with the pair rather than read from a global
    setting, and defaulting to 1, because it is part of the scale those
    two numbers are on: the floor is a median over K-averaged spectra,
    which sits at the raw bins' *mean* rather than their median (FFT
    magnitudes are Rayleigh-ish, so mean/median ~ 1.065), and subtracting
    that from an unaveraged frame would leave a systematic ~6% positive
    excess in every bin that no amount of margin absorbs. A baseline
    captured before this field existed reads back as 1 and is gated
    exactly as it was, unsmoothed -- changing K means re-capturing, the
    same rule compute_energy()'s docstring already states for the
    subtracted/unsubtracted scales.
    """
    spectrum: Mapping[str, Sequence[float]]
    energy: float
    smoothing_frames: int = 1


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


def floor_gain(frame: SensorFrame,
                stopped: StoppedBaseline) -> Optional[float]:
    """How much this frame's noise floor has drifted since `stopped` was
    captured, as a single multiplier: the median of bin/ref over every bin
    of every energy channel. 1.0 means the floor sits exactly where it was
    commissioned. None when the baseline doesn't fit this frame (same rule
    as excess_over_stopped below, which is its only caller).

    Why a gain and not a fresh baseline
    -----------------------------------
    Subtracting the floor (excess_over_stopped) assumes the floor stays put.
    It does not. Measured live on node 194584 an hour after its baseline was
    captured, machine genuinely stopped: every bin of all three accel
    channels read a FLAT 1.11-1.13x its stopped_spectrum_ref -- low bins and
    high bins alike, no peak anywhere in 0-6.4kHz. That is the KX134's own
    broadband floor drifting, not the machine moving.

    A uniform drift is exactly the thing a fixed subtraction cannot absorb,
    and the tolerance is far thinner than it looks. On that node
    stopped_energy_ref was 397 against a floor whose RMS is 5102, i.e. the
    residual the threshold scales from is 7.8% of the floor itself; at
    DEFAULT_STOPPED_MARGIN the gate trips RUNNING at 13.6% of the floor. So
    a floor gain above ~1.14 pins the gate to RUNNING permanently, and 1.12
    already put the median stopped frame at 929 against a 695 threshold --
    290 of 290 stopped frames read RUNNING.

    That is not a slow gate, it is a blind one, and it fails in the worst
    direction: protection/protection.py confirms a trip by watching this
    gate go quiet, so a machine this system had genuinely stopped kept
    reading RUNNING and the trip was reported as failed. Confirmation
    arrived only when three consecutive frames happened to jitter under the
    line -- measured live at one such run per 30-180s, which is what
    produced the ~75s "confirm latency" that no confirm-window value could
    have fixed (see protection.py's DEFAULT_CONFIRM_WINDOW_S).

    Why the median over every bin is safe
    -------------------------------------
    It assumes the machine's signature is NARROW -- that most bins carry
    floor and only a few carry motor. That holds here and is the same fact
    the module docstring's bin table already establishes: the motor is "a
    handful of narrow lines below ~600Hz", under 10 of the 384 bins an
    accel-only frame carries, so a median over all 384 lands on the floor
    even at full speed. Measured on the same node with motor 3 running, the
    low bins lifted to 1.64x/1.23x/1.35x while the bins above ~2.5kHz stayed
    at 1.15x -- the median tracked the floor, not the motor.

    Taking the median over only the high bins (which by that same table
    carry no motor signature at all) measured within 1% of this on both
    states, so the extra tuning knob of "which bins are floor" is not worth
    it -- but a machine with genuinely BROADBAND vibration would defeat
    this, because there would be no bins left for the median to find the
    floor in. Such a machine already defeats the running_fraction path for
    the same reason (its running and stopped spectra differ by a scale
    factor, which is all this measures), so it is not a regression -- but it
    is the assumption to check first if a new device type gates badly.
    """
    ratios: List[float] = []
    for chan in energy_channels(frame):
        ref = stopped.spectrum.get(chan)
        chan_bins = frame.bins[chan]
        if ref is None or len(ref) != len(chan_bins):
            return None
        ratios.extend(b / r for b, r in zip(chan_bins, ref) if r > 0)
    if len(ratios) < MIN_FLOOR_GAIN_BINS:
        # Too few bins to support the "most bins are floor" premise, or a
        # reference that is entirely zero -- leave the subtraction exactly as
        # it was rather than inventing a scale from nothing.
        return 1.0
    return min(max(statistics.median(ratios), 1.0 / MAX_FLOOR_GAIN), MAX_FLOOR_GAIN)


def excess_over_stopped(frame: SensorFrame,
                         stopped: Optional[StoppedBaseline]) -> Optional[List[float]]:
    """Per-bin magnitude above `stopped`'s noise floor, clamped at zero
    (a bin below the floor carries no evidence of motion, and letting it go
    negative would let a quiet bin cancel out a real line elsewhere).

    The floor subtracted is `stopped.spectrum` scaled by floor_gain() above,
    not `stopped.spectrum` itself -- see that function for why a fixed floor
    goes blind within an hour on real hardware.

    None when the baseline can't be applied to this frame -- no baseline at
    all, or one that doesn't cover exactly the channels/bin counts this
    frame carries. Refusing rather than subtracting what fits is deliberate:
    a partially-subtracted energy and a fully-subtracted threshold are on
    different scales, and silently mixing them is precisely the class of bug
    (see the module docstring) this function exists to end. A node whose
    sensor_config or bin count changed since its baseline was captured
    re-reads as "no baseline" and falls back, until it captures a new one.

    An existing stopped_energy_ref stays valid across this change and no
    node needs re-commissioning for it, which is why the gain is applied
    here rather than at capture time. The two ends agree by construction:
    pipeline/stopped_baseline.py measures stopped_energy_ref through
    compute_energy() -> this function, on the same frames the floor was just
    fitted from, so its gain is ~1.0 and the number it records is unchanged.
    Live, the gain is what keeps a drifted frame on that same scale -- on
    the node above, the stopped machine measured 490 against a
    commissioned 397, where the unscaled subtraction measured 929.
    """
    if stopped is None:
        return None
    gain = floor_gain(frame, stopped)
    if gain is None:
        return None
    out: List[float] = []
    for chan in energy_channels(frame):
        ref = stopped.spectrum.get(chan)
        chan_bins = frame.bins[chan]
        if ref is None or len(ref) != len(chan_bins):
            return None
        out.extend(max(b - gain * r, 0.0) for b, r in zip(chan_bins, ref))
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
        # Rolling frame buffer for the smoothing this node's baseline was
        # captured with (StoppedBaseline.smoothing_frames). Per-gate, and
        # gates are per-node, so no cross-node mixing is possible; a node
        # with no baseline never buffers anything.
        self._averager = SpectrumAverager()

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
        # Averaged over exactly the frames the baseline itself was measured
        # over -- see StoppedBaseline.smoothing_frames for why this cannot
        # be a separate setting, and DEFAULT_SMOOTHING_FRAMES for what it
        # buys. Read fresh every frame like the baseline itself, so a
        # re-capture that changes K takes effect on the next frame.
        smoothed = self._averager.push(frame, stopped.smoothing_frames if stopped else 1)
        excess = excess_over_stopped(smoothed, stopped)
        if excess is not None and stopped.energy > 0:
            return _rms(excess), stopped.energy * self._stopped_margin
        # No baseline, one that doesn't fit this frame's channels, or a
        # degenerate zero-energy one (a sensor dead throughout the stopped
        # capture would read a perfectly constant floor) -- measure and
        # threshold the pre-baseline way instead, both unsubtracted, and
        # on the RAW frame: running_energy_ref was commissioned from
        # unaveraged frames, so measuring an averaged one against it would
        # mix the two scales this function exists to keep apart.
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
