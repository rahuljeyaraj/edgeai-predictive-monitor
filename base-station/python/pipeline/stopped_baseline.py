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
read STOPPED forever. stop() rejects the two cases that are *detectably*
wrong (a dead sensor, and a floor so noisy no threshold could sit above it),
but a genuinely running machine looks like a valid, quiet-ish baseline from
here. The UI wording is what has to carry that.
"""
import logging
import statistics
from typing import Dict, List, Optional, Tuple

from gate import (DEFAULT_STOPPED_MARGIN, StoppedBaseline, compute_energy,
                   energy_channels)
from registry import Registry
from sensor_frame import SensorFrame

logger = logging.getLogger(__name__)

# Frames to require before a baseline can be computed. Lower than
# commissioning's 50: this fits a per-bin median and a single scalar, not a
# neural net, and every extra frame is another second the operator stands
# there with the machine off. 30 was enough on the real rig for the fitted
# floor to generalize -- refitting on 20 frames and measuring the other 20
# moved the resulting energy by ~4%, small against the 1.75x margin gate.py
# applies to it (DEFAULT_STOPPED_MARGIN).
DEFAULT_MIN_FRAMES = 30

# Reject a baseline whose own frame-to-frame spread leaves no room for a
# threshold: if the loudest stopped frame is already past where
# DEFAULT_STOPPED_MARGIN would put the line, the gate would flap on the
# baseline data itself.
#
# A real 65-frame capture on the live rig, machine confirmed off, measured
# 1.39x (energy_ref 1533, threshold 2683). Don't tighten this toward that
# number without re-measuring: 1.39 is one capture, and the headroom to
# 1.75 is what absorbs a longer capture catching a bigger outlier. It is
# deliberately equal to DEFAULT_STOPPED_MARGIN -- that is exactly the
# statement being made, that the loudest stopped frame must still fall
# below where the gate will put its line.
MAX_STOPPED_SPREAD = 1.75


class StoppedBaselineError(Exception):
    pass


class StoppedBaselineSession:
    """One start -> collect -> stop cycle for one node, with its machine
    off. Holds raw per-channel bins rather than feature vectors: this
    calibrates gate.py, which reads frame.bins directly and never builds a
    feature vector (a stopped machine has nothing to infer on anyway)."""

    def __init__(self, registry: Registry, node_id: str,
                 min_frames: int = DEFAULT_MIN_FRAMES):
        if min_frames < 2:
            # A single frame has no spread to measure, so stop() could not
            # tell a valid floor from a dead sensor.
            raise ValueError("min_frames must be >= 2")
        self._registry = registry
        self._node_id = node_id
        self._min_frames = min_frames
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

        # Median per bin, not mean: a knock against the bench while the
        # operator stands there is exactly the kind of one-frame outlier
        # that shouldn't raise the floor everywhere. Same reasoning as
        # commissioning.py's running_energy_ref.
        spectrum: Dict[str, Tuple[float, ...]] = {}
        for chan, _ in self._shape:
            per_bin = zip(*(f[chan] for f in self._frames))
            spectrum[chan] = tuple(statistics.median(values) for values in per_bin)

        # What the collected frames still measure once that floor is taken
        # out -- the floor's own frame-to-frame jitter, which is what the
        # gate's threshold has to clear. Fitted and measured on the same
        # frames: checked against a proper held-out split on real rig data
        # and the difference was ~4% (1475 vs 1414), far inside the margin
        # gate.py applies, so the extra complexity of folding bought
        # nothing.
        probe = StoppedBaseline(spectrum=spectrum, energy=0.0)
        # compute_energy() subtracts `probe` here rather than falling back
        # to a raw RMS, because `probe` was built from these exact channels
        # and bin counts -- the one case its fallback can't trigger.
        energies = [compute_energy(_BinsOnly(self._node_id, collected), probe)
                     for collected in self._frames]
        energy = statistics.median(energies)
        spread = max(energies) / energy if energy > 0 else float("inf")

        if energy <= 0:
            raise StoppedBaselineError(
                f"stopped baseline for {self._node_id!r} measures exactly zero spread -- "
                "every frame was identical, which means the sensor is not producing live "
                "data rather than that the machine is quiet")
        if spread > MAX_STOPPED_SPREAD:
            raise StoppedBaselineError(
                f"stopped baseline for {self._node_id!r} is too unsteady to gate on "
                f"(loudest frame is {spread:.2f}x the median, limit {MAX_STOPPED_SPREAD}) -- "
                "something was still moving. Confirm the machine is off and capture again")

        self._registry.set_stopped_baseline(self._node_id, spectrum, energy)
        self._active = False
        # The threshold shown is the one DEFAULT_STOPPED_MARGIN implies. A
        # deployment that overrode --gate-stopped-margin gates on a
        # different number -- this module never sees that value (the gate
        # owns it), and logging it as though it were authoritative would be
        # worse than logging the default and saying so.
        logger.info("stopped baseline for %s: %d frames, %d channels, energy_ref=%.1f "
                     "(spread %.2fx) -> gate threshold %.1f at the default %.2fx margin",
                     self._node_id, len(self._frames), len(spectrum), energy, spread,
                     energy * DEFAULT_STOPPED_MARGIN, DEFAULT_STOPPED_MARGIN)
        return StoppedBaseline(spectrum=spectrum, energy=energy)

    def cancel(self) -> None:
        """Drops the collected frames without touching the registry -- the
        node keeps whatever baseline it already had."""
        self._active = False
        self._frames = []
        self._shape = None


class _BinsOnly:
    """The two attributes gate.py's excess_over_stopped()/energy_channels()
    actually read, so stop() can reuse them on stored bins instead of
    keeping whole SensorFrames alive for the length of a capture."""

    __slots__ = ("node_id", "bins")

    def __init__(self, node_id: str, bins: Dict[str, Tuple[float, ...]]):
        self.node_id = node_id
        self.bins = bins
