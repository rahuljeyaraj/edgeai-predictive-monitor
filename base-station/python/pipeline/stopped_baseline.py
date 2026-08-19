"""Stopped-baseline capture -- measures what a node's sensor reads with its
machine deliberately OFF, so pipeline/gate.py can subtract that noise floor
out of the running/stopped decision.

Why this exists at all is in gate.py's module docstring: the KX134's own
broadband noise dominates an RMS over all bins and is identical whether the
machine runs or not, so a raw energy separates running from stopped by only
~1.18x. Subtracting a measured floor takes the same rig to ~2.09x worst-case
and ~4.4x at the median.

Deliberately NOT part of commissioning (pipeline/commissioning.py), for two
reasons that both come down to the machine being in the opposite state:
commissioning collects a training batch with the motor running and gates
frames on MotorState.RUNNING, this needs it stopped and gates on nothing at
all (gating a stopped-machine capture on the very gate it's calibrating is
circular). Keeping them separate is also what lets a node gain a baseline
without retraining its autoencoder -- capturing one writes only
RegistryEntry.stopped_spectrum_ref/stopped_energy_ref and leaves the model,
its thresholds and running_energy_ref untouched.

The operator asserting "the machine is off" is the whole input here, and
nothing in software can check it -- a running machine captured as a baseline
would teach the gate that its own vibration is the floor, and it would then
read STOPPED forever. stop() rejects the three cases that are *detectably*
wrong (a dead sensor, a floor so noisy no threshold could sit above it, and
a capture where so many frames are transients that "still running" is the
likeliest explanation), but a genuinely running machine that is smooth
looks like a valid, quiet-ish baseline from here. The UI wording is what
has to carry that.

stop() also decides how many frames the gate will average before measuring
one against this baseline (StoppedBaseline.smoothing_frames), because the
floor it fits is only valid on frames averaged the same way -- see that
field's docstring, and gate.py's DEFAULT_SMOOTHING_FRAMES for why the
averaging is there at all.
"""
import logging
import math
import statistics
from typing import Dict, List, Optional, Tuple

from gate import (DEFAULT_SMOOTHING_FRAMES, DEFAULT_STOPPED_MARGIN, BinsFrame,
                   SpectrumAverager, StoppedBaseline, compute_energy,
                   energy_channels)
from registry import Registry
from sensor_frame import SensorFrame

logger = logging.getLogger(__name__)

# Frames to require before a baseline can be computed. ~30s at the
# satellite's ~4.9 fps, and the operator is standing next to a machine they
# have deliberately switched off, so it is time spent waiting -- but 30
# frames, what this used to be, is 6 seconds, and 6 seconds is shorter than
# the thing being measured.
#
# The fitted floor itself never needed more: refitting on 20 frames and
# measuring the other 20 moved the resulting energy by ~4%. The SPREAD does.
# Scored against 251 live frames off e36428 (machine off), as sliding
# captures of each length, with SMOOTHING_FRAMES=6:
#
#   capture   accepted     median spread   worst
#    30 fr    32 of 45         1.65x       2.10x
#   100 fr    30 of 31         1.60x       1.77x
#   150 fr    21 of 21         1.56x       1.64x
#
# A 6-second capture is a coin toss on that node not because the floor is
# unfittable but because 6 seconds is one draw from a jitter that wanders
# over several seconds -- an operator who reran the capture got a different
# answer each time, which is exactly what the user saw. Length fixes that in
# a way no threshold can. Note this only became true once the spread became
# a percentile (SPREAD_PERCENTILE): under the old max/median it got strictly
# WORSE with length, which is why the old default had to be short.
#
# 50 for demo pacing (~10s): NOT one of the three measured lengths above --
# interpolated between 30's 71% (32/45) accept rate and 100's 97% (30/31),
# so treat it as untested. If a capture fails the MAX_STOPPED_SPREAD check
# during the demo, that is this tradeoff showing up, not a bug -- the fix is
# either to retry (capture is fast now) or bump this back toward 100.
DEFAULT_MIN_FRAMES = 50

# Reject a baseline whose own frame-to-frame spread leaves no room for a
# threshold: if a stopped frame is already past where
# DEFAULT_STOPPED_MARGIN would put the line, the gate would flap on the
# baseline data itself.
#
# A real 65-frame capture on the live rig, machine confirmed off, measured
# 1.39x (energy_ref 1533, threshold 2683). Don't tighten this toward that
# number without re-measuring: 1.39 is one capture. It is deliberately
# equal to DEFAULT_STOPPED_MARGIN -- that is exactly the statement being
# made, that a stopped frame must still fall below where the gate will put
# its line.
#
# What that statement is measured on has changed twice, and both changes
# are what let a jittery mounting pass without loosening the limit itself:
# the energies are now averaged SMOOTHING_FRAMES-wide (gate.py's
# DEFAULT_SMOOTHING_FRAMES) exactly as the live gate will average them, and
# the spread is the SPREAD_PERCENTILE quantile rather than the max. Node
# e36428 measured 1.91x raw and unsmoothed against another node's 1.37x in
# the same room; loosening this constant to fit it would have raised that
# node's RUNNING threshold in the same breath and starved the next step of
# commissioning of frames, which is the failure this file's whole point is
# to avoid.
MAX_STOPPED_SPREAD = 1.75

# Frames whose raw excess energy exceeds this multiple of the capture's own
# median are dropped before anything is fitted. This is the knock against
# the bench, the operator leaning on the machine, the trolley going past --
# a transient with a start and an end, as opposed to the steady jitter
# MAX_STOPPED_SPREAD is about. Measured on node e36428: a 13s burst at 5-25x
# the median, narrowband, uncorrelated (r = -0.04) with the other node on
# the same frame, i.e. genuinely local and genuinely transient.
#
# Rejecting them here rather than leaning on MAX_STOPPED_SPREAD is what
# makes a long capture safe: at 30 frames one outlier is 3% of the capture,
# at 800 frames one outlier is 0.1% and yet max/median failed the whole
# thing just the same. The smoothing below makes that worse, not better --
# a single bad frame contaminates SMOOTHING_FRAMES consecutive averages --
# so the drop has to happen before the averaging, not after it.
OUTLIER_ENERGY_FACTOR = 3.0

# ...but if a quarter of the capture looks like that, it is not knocks. It
# is a machine that is still running, or a mounting so bad the capture is
# meaningless, and silently fitting a floor from the quiet three-quarters
# would be the one failure this module's docstring says nothing in software
# can catch. Reject instead, and say so.
MAX_OUTLIER_FRACTION = 0.25

# Which quantile of the (post-outlier-rejection, post-smoothing) energies
# has to sit under MAX_STOPPED_SPREAD. Not max(): with outliers already
# gone, what is left is the floor's own distribution, and the largest
# sample of a long capture is by definition further into that
# distribution's tail than the largest sample of a short one -- a check on
# max() therefore gets strictly harder the longer an operator stands there
# collecting, which is exactly backwards. p95 measures the same jitter
# independently of how many frames were taken to measure it. The gate's
# debounce_frames absorbs the remaining 5%.
SPREAD_PERCENTILE = 0.95


class StoppedBaselineError(Exception):
    pass


class StoppedBaselineSession:
    """One start -> collect -> stop cycle for one node, with its machine
    off. Holds raw per-channel bins rather than feature vectors: this
    calibrates gate.py, which reads frame.bins directly and never builds a
    feature vector (a stopped machine has nothing to infer on anyway)."""

    def __init__(self, registry: Registry, node_id: str,
                 min_frames: int = DEFAULT_MIN_FRAMES,
                 smoothing_frames: int = DEFAULT_SMOOTHING_FRAMES):
        if min_frames < 2:
            # A single frame has no spread to measure, so stop() could not
            # tell a valid floor from a dead sensor.
            raise ValueError("min_frames must be >= 2")
        if smoothing_frames < 1:
            raise ValueError("smoothing_frames must be >= 1")
        if min_frames < smoothing_frames * 2:
            # Otherwise most of the capture is partial windows -- averages
            # of 1, 2, ... frames, which carry the jitter of a short average
            # while being measured as though they were full ones, and the
            # spread check reads them as a machine that never stopped. Two
            # full windows is the floor; the shipped defaults are 50 and 6.
            raise ValueError(
                f"min_frames ({min_frames}) must be at least twice smoothing_frames "
                f"({smoothing_frames}) -- a capture that short is mostly partial "
                "averages, and its measured spread would say more about the averaging "
                "than about the machine")
        self._registry = registry
        self._node_id = node_id
        self._min_frames = min_frames
        # Recorded onto the baseline so the gate averages live frames the
        # same way this fit did -- see StoppedBaseline.smoothing_frames for
        # why the two cannot disagree.
        self._smoothing_frames = smoothing_frames
        self._active = False
        # Per channel, one list of bin-tuples per collected frame. The
        # first frame commits the shape -- (channel, bin_count) pairs, not
        # just channel names (same rule as pipeline/manager.py's
        # _infer_sensor_config_and_dim). Both halves matter: a node whose
        # sensor_config changes mid-capture would produce a baseline
        # covering channels that no longer arrive together, and one whose
        # bin count changes would have stop()'s per-bin zip() silently
        # truncate the fit to the shortest frame seen, storing a baseline
        # too short to ever apply to a live frame again.
        self._frames: List[Dict[str, Tuple[float, ...]]] = []
        self._shape: Optional[Tuple[Tuple[str, int], ...]] = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def collected_count(self) -> int:
        return len(self._frames)

    @property
    def min_frames(self) -> int:
        return self._min_frames

    def start(self) -> None:
        if self._active:
            raise StoppedBaselineError(
                f"stopped-baseline capture already running for {self._node_id!r}")
        # Confirms the node exists before the operator goes and switches a
        # machine off for it (raises NodeNotFoundError otherwise).
        self._registry.get(self._node_id)
        self._frames = []
        self._shape = None
        self._active = True

    def feed_frame(self, frame: SensorFrame) -> None:
        """Every frame for this node counts -- no MotorState gating, by
        design (see the module docstring). Frames for other nodes, and
        frames whose channel set doesn't match the one this capture
        committed to, are dropped rather than raising: a live stream mixes
        nodes by nature, and one odd frame shouldn't abort a capture the
        operator is standing over."""
        if not self._active or frame.node_id != self._node_id:
            return
        shape = tuple((chan, len(frame.bins[chan])) for chan in energy_channels(frame))
        if not shape:
            return
        if self._shape is None:
            self._shape = shape
        elif shape != self._shape:
            logger.warning("stopped baseline for %s: dropping frame shaped %s, capture "
                            "committed to %s", self._node_id, shape, self._shape)
            return
        self._frames.append({chan: tuple(frame.bins[chan]) for chan, _ in shape})

    def stop(self) -> StoppedBaseline:
        """Fits the baseline from the collected frames, writes it to the
        registry and returns it. Raises (leaving the session active, so the
        operator can just keep collecting -- same retry shape as
        commissioning's stop_collecting()) if there isn't enough to fit."""
        if not self._active:
            raise StoppedBaselineError(
                f"no stopped-baseline capture running for {self._node_id!r}")
        if len(self._frames) < self._min_frames:
            raise StoppedBaselineError(
                f"only collected {len(self._frames)} stopped frames for "
                f"{self._node_id!r}, need at least {self._min_frames}")

        node = self._node_id
        collected = [BinsFrame(node, f) for f in self._frames]

        # --- pass 1: drop transients ------------------------------------
        # A provisional floor, used only to rank frames by how far above it
        # they sit. It is fitted from every frame including the bad ones,
        # which is fine for ranking (a per-bin median over 30+ frames barely
        # moves for a handful of outliers) and is why the real floor is
        # fitted again below rather than reused from here.
        provisional = StoppedBaseline(spectrum=self._fit_floor(collected), energy=0.0)
        raw_energies = [compute_energy(f, provisional) for f in collected]
        median_raw = statistics.median(raw_energies)
        if median_raw > 0:
            kept = [f for f, e in zip(collected, raw_energies)
                    if e <= median_raw * OUTLIER_ENERGY_FACTOR]
        else:
            # Every frame identical to the floor -- the dead-sensor case,
            # caught by the energy check below with a message that says so.
            kept = collected
        dropped = len(collected) - len(kept)
        if dropped > len(collected) * MAX_OUTLIER_FRACTION:
            raise StoppedBaselineError(
                f"stopped baseline for {node!r}: {dropped} of {len(collected)} frames "
                f"measure more than {OUTLIER_ENERGY_FACTOR:.0f}x the rest, which is too "
                "many to be knocks against the bench -- the machine is probably still "
                "running, or the sensor is loose on its mounting. Confirm it is off and "
                "capture again")
        if len(kept) < self._min_frames:
            # Same retry shape as the too-few-frames case above: the
            # operator just keeps collecting, and the transients are
            # already behind them.
            raise StoppedBaselineError(
                f"only {len(kept)} of {len(collected)} stopped frames for {node!r} were "
                f"steady enough to fit ({dropped} dropped as knocks), need at least "
                f"{self._min_frames} -- keep collecting")
        if dropped:
            logger.info("stopped baseline for %s: dropped %d of %d frames measuring over "
                         "%.0fx the median as transients", node, dropped, len(collected),
                         OUTLIER_ENERGY_FACTOR)

        # --- pass 2: average, then fit what the gate will use -----------
        # Averaged first and fitted second, in that order, because the two
        # have to be on one scale: a floor fitted from raw frames sits at
        # their per-bin median, an averaged frame converges on their per-bin
        # MEAN, and for Rayleigh-ish FFT magnitudes those differ by ~6.5% --
        # a systematic positive excess in every bin that would survive any
        # amount of averaging. See StoppedBaseline.smoothing_frames.
        smoothing = self._smoothing_frames
        averager = SpectrumAverager()
        smoothed = [averager.push(f, smoothing) for f in kept]
        # The first smoothing-1 outputs average fewer frames than the rest
        # (SpectrumAverager returns partial windows by design, so a live
        # gate never stalls), so they carry more jitter and would land in
        # the spread measurement as though they were representative. Drop
        # them here, where -- unlike live -- there is no cost to waiting.
        # Unconditional: __init__ guarantees min_frames >= 2 * smoothing, so
        # at least smoothing+1 full windows always survive this.
        smoothed = smoothed[smoothing - 1:]

        # Median per bin, not mean: a knock against the bench while the
        # operator stands there is exactly the kind of one-frame outlier
        # that shouldn't raise the floor everywhere (belt and braces with
        # the outlier rejection above -- that catches whole frames, this
        # catches a single bin doing something odd). Same reasoning as
        # commissioning.py's running_energy_ref.
        spectrum = self._fit_floor(smoothed)

        # What the collected frames still measure once that floor is taken
        # out -- the floor's own frame-to-frame jitter, which is what the
        # gate's threshold has to clear. Fitted and measured on the same
        # frames: checked against a proper held-out split on real rig data
        # and the difference was ~4% (1475 vs 1414), far inside the margin
        # gate.py applies to it, so the extra complexity of folding bought
        # nothing.
        #
        # compute_energy() subtracts `probe` here rather than falling back
        # to a raw RMS, because `probe` was built from these exact channels
        # and bin counts -- the one case its fallback can't trigger.
        probe = StoppedBaseline(spectrum=spectrum, energy=0.0,
                                 smoothing_frames=smoothing)
        energies = [compute_energy(f, probe) for f in smoothed]
        energy = statistics.median(energies)

        if energy <= 0:
            raise StoppedBaselineError(
                f"stopped baseline for {self._node_id!r} measures exactly zero spread -- "
                "every frame was identical, which means the sensor is not producing live "
                "data rather than that the machine is quiet")
        spread = _percentile(energies, SPREAD_PERCENTILE) / energy
        if spread > MAX_STOPPED_SPREAD:
            raise StoppedBaselineError(
                f"stopped baseline for {self._node_id!r} is too unsteady to gate on "
                f"(95th-percentile frame is {spread:.2f}x the median even after averaging "
                f"{smoothing} frames together, limit {MAX_STOPPED_SPREAD}) -- something "
                "was still moving. Confirm the machine is off and capture again")

        self._registry.set_stopped_baseline(self._node_id, spectrum, energy,
                                             smoothing_frames=smoothing)
        self._active = False
        # The threshold shown is the one DEFAULT_STOPPED_MARGIN implies. A
        # deployment that overrode --gate-stopped-margin gates on a
        # different number -- this module never sees that value (the gate
        # owns it), and logging it as though it were authoritative would be
        # worse than logging the default and saying so.
        logger.info("stopped baseline for %s: %d frames (%d kept, averaged %d-wide), "
                     "%d channels, energy_ref=%.1f (p%d spread %.2fx) -> gate threshold "
                     "%.1f at the default %.2fx margin",
                     self._node_id, len(self._frames), len(kept), smoothing,
                     len(spectrum), energy, int(SPREAD_PERCENTILE * 100), spread,
                     energy * DEFAULT_STOPPED_MARGIN, DEFAULT_STOPPED_MARGIN)
        return StoppedBaseline(spectrum=spectrum, energy=energy,
                                smoothing_frames=smoothing)

    def _fit_floor(self, frames: List[BinsFrame]) -> Dict[str, Tuple[float, ...]]:
        """Per-channel, per-bin median across `frames` -- the noise floor
        gate.py subtracts. Shared by both passes of stop() so a provisional
        fit and the final one can never drift apart."""
        floor: Dict[str, Tuple[float, ...]] = {}
        for chan, _ in self._shape:
            per_bin = zip(*(f.bins[chan] for f in frames))
            floor[chan] = tuple(statistics.median(values) for values in per_bin)
        return floor

    def cancel(self) -> None:
        """Drops the collected frames without touching the registry -- the
        node keeps whatever baseline it already had."""
        self._active = False
        self._frames = []
        self._shape = None


def _percentile(values: List[float], q: float) -> float:
    """Nearest-rank percentile -- no interpolation, no numpy. The value
    returned is always one an actual frame measured, which is what the
    error message quotes back at an operator."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]
