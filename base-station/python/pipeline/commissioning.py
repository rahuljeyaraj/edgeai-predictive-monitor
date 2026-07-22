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
import os
import statistics
from typing import Callable, List, Optional, Tuple

from sensor_frame import SensorFrame
from registry import NodeStatus, Registry
from gate import MotorState, MotorStateGate
from features import build_feature_vector, standardize_scalars
from autoencoder import (build_autoencoder, reconstruction_error, save_model,
                          train_autoencoder)


class CommissioningError(Exception):
    pass


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
    stopped/transient data ("gated 'running, healthy' data", S3.5)."""

    def __init__(self, registry: Registry, models_dir: str, node_id: str,
                 gate: MotorStateGate, min_frames: int = 50, epochs: int = 300):
        if min_frames < 1:
            raise ValueError("min_frames must be >= 1")
        self._registry = registry
        self._models_dir = models_dir
        self._node_id = node_id
        self._gate = gate
        self._min_frames = min_frames
        self._epochs = epochs
        self._collected: List[Tuple[float, ...]] = []
        self._frozen: List[Tuple[float, ...]] = []
        # Where each collected vector's scalar tail starts -- constant for
        # the whole session (same node, same sensor_config throughout), set
        # from build_feature_vector()'s own return each frame rather than
        # recomputed independently, so it can never drift out of sync with
        # what actually built self._collected's vectors.
        self._spectral_dim: Optional[int] = None

    @property
    def collected_count(self) -> int:
        return len(self._collected)

    def start(self) -> None:
        # Registry.start_commissioning() is the sole guard here: it already
        # refuses a COMMISSIONING_COLLECTING->COMMISSIONING_COLLECTING
        # re-entry (that's not an allowed source state), so a double-start
        # raises InvalidTransitionError from there rather than a
        # session-local CommissioningError.
        self._registry.start_commissioning(self._node_id)
        self._collected = []
        self._spectral_dim = None

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

        if self._gate.update(frame) != MotorState.RUNNING:
            return

        vector, spectral_dim = build_feature_vector(frame, entry.sensor_config, entry.input_dim)
        self._spectral_dim = spectral_dim
        self._collected.append(vector)

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
        if len(self._collected) < self._min_frames:
            raise CommissioningError(
                f"only collected {len(self._collected)} running-healthy frames for "
                f"{self._node_id!r}, need at least {self._min_frames}")

        self._registry.stop_collecting(self._node_id)
        self._frozen = list(self._collected)

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
            columns = list(zip(*(v[self._spectral_dim:] for v in self._frozen)))
            scalar_mu = tuple(statistics.fmean(col) for col in columns)
            scalar_sigma = tuple(statistics.pstdev(col) for col in columns)
            standardized = [standardize_scalars(v, self._spectral_dim, scalar_mu, scalar_sigma)
                             for v in self._frozen]
        else:
            scalar_mu = scalar_sigma = None
            standardized = self._frozen

        model = build_autoencoder(entry.input_dim)
        train_autoencoder(model, standardized, epochs=self._epochs, on_epoch=on_epoch)

        # Calibrate this node's warning/fault thresholds from the trained
        # model's error on its own healthy batch, so inference (S3.6) uses
        # a baseline that fits this motor rather than a fixed global cutoff
        # that may sit entirely above or below its score range.
        healthy_scores = [reconstruction_error(model, vector) for vector in standardized]
        warning_threshold, fault_threshold = _thresholds_from_healthy(healthy_scores)

        os.makedirs(self._models_dir, exist_ok=True)
        model_path = os.path.join(self._models_dir, f"{self._node_id}.pt")
        save_model(model, model_path)
        self._registry.complete_commissioning(
            self._node_id, model_path,
            warning_threshold=warning_threshold, fault_threshold=fault_threshold,
            scalar_mu=scalar_mu, scalar_sigma=scalar_sigma)

        return model_path
