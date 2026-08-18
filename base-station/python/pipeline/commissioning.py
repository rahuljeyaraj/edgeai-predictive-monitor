"""Commissioning workflow -- explicit per-motor start/stop trigger that
collects gated healthy data, trains that motor's autoencoder, saves
weights, and updates the registry, per
docs/MPU_Software_Architecture.md S3.5/S8 M7.

Resolves open question #6 (S6): re-commissioning overwrites the existing
model rather than versioning it. `RegistryEntry.model_path` (S4.2) is a
single field, not a list, and nothing downstream reads more than the
current model (`history/store.py` at M9 is anomaly scores, not model
versions) -- versioning would be dead weight with no consumer.
`model_path` is deterministic from `node_id`, so re-commissioning
naturally lands on the same file.
"""
import logging
import os
import statistics
from typing import Callable, List, Optional, Tuple

from sensor_frame import SensorFrame
from registry import NodeStatus, Registry
from gate import MotorState, MotorStateGate, compute_energy
from features import build_feature_vector, standardize_scalars
from autoencoder import (build_autoencoder, reconstruction_error, save_model,
                          train_autoencoder)
from capture import CaptureError, normalize_label, save_vectors

logger = logging.getLogger(__name__)


class CommissioningError(Exception):
    pass


# The one condition every commission collects, and the only one a plain
# (non-setup) commission ever has -- a session that never calls
# start_condition() behaves exactly as it did before operating conditions
# existed (docs/UNIFIED_COMMISSIONING_PLAN.md S2.3: "day one, with one
# simple machine, they add none").
DEFAULT_CONDITION = "running"

# Every running condition setup collects is also kept as a labeled
# recording, and they all share this one label: the labels ARE the fault
# classifier's class list, so `healthy_no_load` / `healthy_full_load` would
# hand Edge Impulse two classes that both mean "fine" (S2.3). The condition
# name rides in the capture payload's own `condition` key instead.
CONDITION_CAPTURE_LABEL = "healthy"


# Threshold calibration (S3.6): warning/fault are placed above this motor's
# own healthy reconstruction-error spread, in std-devs, so a real fault
# (which the autoencoder can't reconstruct) crosses them while normal
# healthy variation on the same motor does not. Wide gap (3sigma -> 8sigma)
# because healthy scores cluster tightly right after training on that data,
# so a few sigma is a small absolute margin -- verified against the dataset
# (docs: warning=mu+3sigma flags 100% of fault frames, dense spectrum clears
# fault=mu+8sigma too).
_WARNING_SIGMA = 8.0
_FAULT_SIGMA = 15.0


def _condition_name(name: str) -> str:
    """Canonicalizes a free-typed condition ("Full Load", "full load") to one
    stable key. Reuses capture.py's label normalizer rather than a second
    near-identical one: the result is stored verbatim in the capture
    payload's `condition` field, so the two must agree on what "the same
    condition" means. Its "must contain at least one letter or digit"
    rejection is re-raised as a CommissioningError so start_condition()'s
    callers have exactly one exception type to handle."""
    try:
        return normalize_label(name)
    except CaptureError as e:
        raise CommissioningError(str(e)) from e


def _thresholds_from_healthy(scores) -> Tuple[float, float]:
    """warning/fault thresholds from the trained model's error on the
    healthy commissioning batch. Guards the degenerate near-zero-spread
    case (e.g. a node looping one file, so every collected window is nearly
    identical) so fault > warning > 0 always holds regardless."""
    mu = statistics.fmean(scores)
    sigma = statistics.pstdev(scores)  # population: the batch IS the baseline
    warning = max(mu + _WARNING_SIGMA * sigma, mu * 1.5, 1e-4)
    fault = max(mu + _FAULT_SIGMA * sigma, warning * 2.0)
    return warning, fault


class CommissioningSession:
    """One session covers one explicit start -> collect -> stop cycle for
    one motor (S3.5: "per motor, not global" trigger). Only frames the
    gate confirms RUNNING are kept -- training must never see
    stopped/transient data ("gated 'running, healthy' data", S3.5).

    A session collects one or more named OPERATING CONDITIONS
    (docs/UNIFIED_COMMISSIONING_PLAN.md S2.3) rather than a single batch:
    the autoencoder learns "what healthy looks like" from whatever it was
    shown, so a model shown only no-load scores a full-load run as a fault.
    Collecting the machine's real duty range up front is the fix; the
    alternative was re-commissioning every time the duty changed.

    A caller that never calls start_condition() gets exactly the old
    behaviour under one condition named "running"."""

    def __init__(self, registry: Registry, models_dir: str, node_id: str,
                 gate: MotorStateGate, min_frames: int = 50, epochs: int = 300,
                 captures_dir: Optional[str] = None):
        """captures_dir: where each collected operating condition is also
        saved as a `healthy` recording (S2.3). None disables that half
        entirely -- the model is still trained on exactly the same frames,
        so a caller with nowhere to put recordings (or a node with no asset
        class yet) loses the classifier data and nothing else."""
        if min_frames < 1:
            raise ValueError("min_frames must be >= 1")
        self._registry = registry
        self._models_dir = models_dir
        self._captures_dir = captures_dir
        self._node_id = node_id
        self._gate = gate
        self._min_frames = min_frames
        self._epochs = epochs
        # One entry per completed operating condition: (name, vectors,
        # energies). The energies are gate.py compute_energy() per collected
        # frame, at the same index as its vector, so train() can calibrate
        # this node's running/stopped gate reference from the same
        # known-running frames it fits the model on -- the one moment we can
        # be sure what "this machine, turning" measures for this sensor at
        # this mounting point. Kept per condition rather than pooled because
        # running_energy_ref must come from the QUIETEST condition, not the
        # pool (S2.3).
        self._conditions: List[Tuple[str, List[Tuple[float, ...]], List[float]]] = []
        self._current_name: str = DEFAULT_CONDITION
        self._collected: List[Tuple[float, ...]] = []
        self._energies: List[float] = []
        # True between stop_condition() and the next start_condition(): the
        # session is still open and still watching the gate, but frames land
        # nowhere. That gap is the whole point -- an operator changing the
        # machine from no load to full load has to walk to it, change the
        # load and walk back, and every frame measured while they do belongs
        # to neither condition. Without it start_condition() closed the old
        # condition and opened the new one in the same instant, so the load
        # change itself was recorded as normal running.
        self._paused: bool = False
        # Frozen copy of self._conditions, taken by stop_collecting().
        self._frozen: List[Tuple[str, List[Tuple[float, ...]], List[float]]] = []
        # Where each collected vector's scalar tail starts -- constant for
        # the whole session (same node, same sensor_config throughout), set
        # from build_feature_vector()'s own return each frame rather than
        # recomputed independently, so it can never drift out of sync with
        # what actually built self._collected's vectors.
        self._spectral_dim: Optional[int] = None

    @property
    def collected_count(self) -> int:
        """Frames across every condition, completed and in progress -- what
        the "have we got enough to train" checks and the tile's own progress
        readout have always counted."""
        return sum(len(vectors) for _, vectors, _ in self._conditions) + len(self._collected)

    @property
    def condition_name(self) -> str:
        """The condition frames are landing in right now."""
        return self._current_name

    @property
    def condition_counts(self) -> List[Tuple[str, int]]:
        """(name, frames) per condition in collection order, current one
        last -- the per-condition live counters setup's step 3 shows. While
        paused there is no current one: self._current_name still holds the
        condition stop_condition() just closed, and appending it again would
        show the operator a second copy of it sitting at 0 frames."""
        counts = [(name, len(vectors)) for name, vectors, _ in self._conditions]
        if not self._paused:
            counts.append((self._current_name, len(self._collected)))
        return counts

    @property
    def paused(self) -> bool:
        """True while nothing is being collected because stop_condition()
        closed the last one -- what the step's controls read to know whether
        to offer "Stop collecting" or the next condition's name field."""
        return self._paused

    def start(self) -> None:
        # Registry.start_commissioning() is the sole guard here: it already
        # refuses a COMMISSIONING_COLLECTING->COMMISSIONING_COLLECTING
        # re-entry (that's not an allowed source state), so a double-start
        # raises InvalidTransitionError from there rather than a
        # session-local CommissioningError.
        self._registry.start_commissioning(self._node_id)
        self._conditions = []
        self._current_name = DEFAULT_CONDITION
        self._collected = []
        self._energies = []
        self._spectral_dim = None
        self._paused = False

    def start_condition(self, name: str) -> None:
        """Closes the condition currently collecting and opens a named new
        one (S2.3's "No load" / "Full load" / free-typed). The first call
        renames the default condition rather than closing it, so an operator
        who names their first condition doesn't end up with a stray empty
        "running" alongside it.

        Raises (leaving the current condition open, same retry shape as
        stop_collecting()) if the condition being closed has frames but not
        enough of them -- half a condition pooled into the training batch is
        worse than none, since it widens the healthy manifold without
        actually covering that duty point."""
        name = _condition_name(name)
        if any(existing == name for existing, _, _ in self._conditions) \
                or (name == self._current_name and self._collected):
            raise CommissioningError(
                f"operating condition {name!r} has already been collected for "
                f"{self._node_id!r}")
        if self._collected:
            self._close_condition()
        self._current_name = name
        self._paused = False

    def stop_condition(self) -> None:
        """Closes the condition currently collecting and stops collecting
        altogether until the next start_condition() -- the pause an operator
        needs to change the machine's load between conditions (S2.3).

        Idempotent while already paused. Unlike start_condition() and
        stop_collecting(), this never raises for too few frames: it's the
        operator's one guaranteed way to stop, including a condition stuck
        at zero frames because the gate never confirmed RUNNING (a bad load
        setting, a miswired sensor, ...). Below min_frames the attempt is
        discarded rather than banked -- the "no half conditions in the
        training batch" rule from _close_condition() still holds, it's just
        enforced by throwing the attempt away instead of refusing to stop."""
        if self._paused:
            return
        if len(self._collected) >= self._min_frames:
            self._close_condition()
        else:
            self._collected = []
            self._energies = []
        self._paused = True

    def _close_condition(self) -> None:
        if len(self._collected) < self._min_frames:
            raise CommissioningError(
                f"only collected {len(self._collected)} running-healthy frames for "
                f"condition {self._current_name!r} on {self._node_id!r}, need at "
                f"least {self._min_frames}")
        self._conditions.append((self._current_name, list(self._collected),
                                  list(self._energies)))
        self._collected = []
        self._energies = []

    def feed_frame(self, frame: SensorFrame) -> None:
        """Call for every frame while a session is active; frames for other
        node_ids or that the gate doesn't confirm as RUNNING are silently
        dropped rather than raising, since a live stream mixes motors and
        transient states by nature."""
        if frame.node_id != self._node_id:
            return

        entry = self._registry.get(self._node_id)
        if entry.status != NodeStatus.COMMISSIONING_COLLECTING:
            raise CommissioningError(f"commissioning not started for {self._node_id!r}")

        # The gate is updated even while paused, deliberately: it needs an
        # unbroken frame history to hold its confirmed running state, so
        # resuming after a load change doesn't have to re-earn debounce_frames
        # of agreement before the next condition collects anything.
        running = self._gate.update(frame) == MotorState.RUNNING
        if self._paused or not running:
            return

        vector, spectral_dim = build_feature_vector(frame, entry.sensor_config, entry.input_dim)
        self._spectral_dim = spectral_dim
        self._collected.append(vector)
        self._energies.append(compute_energy(frame))

    def stop_collecting(self) -> None:
        """Explicit stop trigger (S3.5, dashboard redesign S6: "Stop &
        Train"): freezes the collected batch and flips the node to
        COMMISSIONING_TRAINING. Raises (without resetting the session) if
        too few running-healthy frames were collected -- the technician can
        keep the motor running and feed more frames, then call
        stop_collecting() again. Training itself happens in train(),
        called separately so the caller (api/app.py) can run it off the
        request thread and stream progress in between."""
        entry = self._registry.get(self._node_id)
        if entry.status != NodeStatus.COMMISSIONING_COLLECTING:
            raise CommissioningError(f"commissioning not started for {self._node_id!r}")
        # An in-progress condition is closed here (with its own min_frames
        # check) so an operator never has to explicitly "finish" the last
        # one before training -- but a condition that was already closed is
        # enough on its own, which is what lets them stop mid-way through
        # adding an optional extra condition and still train on the ones
        # already banked. A paused session is exactly that case: nothing is
        # collecting, self._collected is empty, and the banked conditions
        # stand.
        if self._collected or not self._conditions:
            self._close_condition()

        self._registry.stop_collecting(self._node_id)
        self._frozen = [(name, list(vectors), list(energies))
                        for name, vectors, energies in self._conditions]

    def train(self, on_epoch: Optional[Callable[[int, int], None]] = None) -> str:
        """Fits the autoencoder on the batch frozen by stop_collecting(),
        saves weights, and completes the registry transition to HEALTHY,
        returning the saved model's path. Long-running (fixed-epoch,
        full-batch) -- callers that want to stream progress pass on_epoch,
        forwarded straight through to train_autoencoder()."""
        entry = self._registry.get(self._node_id)
        if entry.status != NodeStatus.COMMISSIONING_TRAINING:
            raise CommissioningError(f"not ready to train for {self._node_id!r}: "
                                      "call stop_collecting() first")

        # Every condition's frames, pooled into one batch and one model
        # (S2.3). The honest cost is stated in that section: pooling widens
        # the healthy reconstruction-error spread, so mu+8sigma sits higher
        # and sensitivity drops somewhat. The alternative -- per-condition
        # thresholds -- would need to know at runtime which condition the
        # machine is in, and nothing can detect that.
        pooled = [vector for _, vectors, _ in self._frozen for vector in vectors]

        # Fit the scalar tail's z-score standardization on THIS node's own
        # healthy batch (features.py's standardize_scalars() -- spectral
        # bins are already peak-normalized to [0,1] and skipped; a
        # sensor_config with no scalar tail at all makes this a no-op,
        # scalar_mu/scalar_sigma stay None). Population mean/stdev per
        # column, mirroring tools/offline_experiment.py's run_config() (the
        # config this exact standardization approach was validated with).
        # The model must train on standardized data, not raw, since that's
        # what inference.py will feed it too.
        scalar_dim = entry.input_dim - self._spectral_dim
        if scalar_dim > 0:
            columns = list(zip(*(v[self._spectral_dim:] for v in pooled)))
            scalar_mu = tuple(statistics.fmean(col) for col in columns)
            scalar_sigma = tuple(statistics.pstdev(col) for col in columns)
            standardized = [standardize_scalars(v, self._spectral_dim, scalar_mu, scalar_sigma)
                             for v in pooled]
        else:
            scalar_mu = scalar_sigma = None
            standardized = pooled

        model = build_autoencoder(entry.input_dim)
        train_autoencoder(model, standardized, epochs=self._epochs, on_epoch=on_epoch)

        # Calibrate this node's warning/fault thresholds from the trained
        # model's error on its own healthy batch, so inference (S3.6) uses
        # a baseline that fits this motor rather than a fixed global cutoff
        # that may sit entirely above or below its score range.
        healthy_scores = [reconstruction_error(model, vector) for vector in standardized]
        warning_threshold, fault_threshold = _thresholds_from_healthy(healthy_scores)

        # Median per condition, not mean: the batch is whatever the machine
        # did during commissioning, so a few frames caught during spin-up
        # (or a transient knock) shouldn't drag the reference the gate
        # scales from.
        #
        # Then the QUIETEST condition's median, not the pool's (S2.3). The
        # gate's running threshold is a fraction of this reference, and it
        # must still call the machine's quietest legitimate running state
        # "running" -- a pooled median biased upward by a loud full-load
        # condition would push that line above the machine's own no-load
        # level, so a no-load run would read as stopped.
        #
        # None when the batch somehow carries no energies at all, which
        # leaves the entry's existing reference untouched
        # (complete_commissioning ignores None) rather than writing a zero
        # that would make gate.py fall back for this node forever.
        medians = [statistics.median(energies) for _, _, energies in self._frozen if energies]
        running_energy_ref = min(medians) if medians else None

        os.makedirs(self._models_dir, exist_ok=True)
        model_path = os.path.join(self._models_dir, f"{self._node_id}.pt")
        save_model(model, model_path)
        self._registry.complete_commissioning(
            self._node_id, model_path,
            warning_threshold=warning_threshold, fault_threshold=fault_threshold,
            scalar_mu=scalar_mu, scalar_sigma=scalar_sigma,
            running_energy_ref=running_energy_ref,
            operating_conditions=[name for name, _, _ in self._frozen])

        # After complete_commissioning, not before: a failed recording save
        # must not be able to strand a node that has a perfectly good
        # trained model sitting on disk.
        self._save_condition_recordings(entry)

        return model_path

    def _save_condition_recordings(self, entry) -> None:
        """One `healthy` recording per collected condition (S2.3), so the
        same frames the autoencoder trained on are also available to the
        fault classifier as its "fine" class -- three consumers, one
        collection.

        Best-effort by design: recordings feed the classifier, which is
        independent of commissioning entirely (pipeline/capture.py's own
        docstring), so nothing here may fail a commission that has already
        produced a model."""
        if self._captures_dir is None:
            return
        if not entry.device_type:
            # Setup makes asset class mandatory (S2.2.1), but the plain
            # commission route doesn't -- and a recording with no asset
            # class belongs to no classifier training set, so there's
            # nowhere for it to go.
            logger.info("commissioning %s: no asset class, skipping condition recordings",
                         self._node_id)
            return
        for name, vectors, _ in self._frozen:
            try:
                save_vectors(self._captures_dir, self._node_id, CONDITION_CAPTURE_LABEL,
                              vectors, entry.sensor_config, entry.input_dim,
                              device_type=entry.device_type, condition=name)
            except (CaptureError, OSError):
                logger.exception("commissioning %s: saving the %r recording failed",
                                  self._node_id, name)
