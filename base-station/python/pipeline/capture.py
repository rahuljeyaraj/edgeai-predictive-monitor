"""Capture + label workflow -- explicit per-node start/stop trigger that
buffers gated running feature vectors, then persists the labeled batch to
disk for later Edge Impulse upload, per
docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S2.

Deliberately independent of commissioning/NodeStatus (2026-07-24 decision,
see that doc's S2): a fault worth capturing can happen on a node in any
status, so this module never touches Registry.status at all -- it only
reads a node's sensor_config/input_dim to build the same feature vector
commissioning trains on. Still gated on MotorState.RUNNING (own
MotorStateGate instance, mirroring pipeline/commissioning.py) since a
captured sample while the motor is stopped carries no signature worth
labeling.

One CaptureSession is long-lived per node (unlike CommissioningSession,
which is one-shot per commission cycle) -- a node may be captured many
times over its life, each time a fault is worth keeping, so it cycles
idle -> capturing -> stopped -> idle repeatedly rather than being
discarded after one save.
"""
import json
import os
import re
import time
from typing import List, Optional, Tuple

from sensor_frame import SensorFrame
from registry import Registry
from gate import MotorState, MotorStateGate
from features import build_feature_vector, muted_channel_names


class CaptureError(Exception):
    pass


# Canonicalizes a free-typed label into a safe, deduplicated form -- this
# same string is used as BOTH the on-disk directory name and the stored/
# displayed label, so "Bearing Fault", "bearing fault", and "bearing  Fault!"
# all collapse to "bearing_fault" instead of fragmenting into near-duplicate
# labels from typos/casing. It also doubles as the path-traversal guard:
# `label` is user-controlled REST input that becomes a filesystem path
# component (captures_dir/<label>/...), so anything other than [a-z0-9]
# must never survive into that path.
_LABEL_UNSAFE_RE = re.compile(r"[^a-z0-9]+")


def normalize_label(label: str) -> str:
    normalized = _LABEL_UNSAFE_RE.sub("_", label.strip().lower()).strip("_")
    if not normalized:
        raise CaptureError("label must contain at least one letter or digit")
    return normalized


def list_labels(captures_dir: str) -> List[str]:
    """Distinct labels already saved, for the dashboard's label dropdown
    (docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S2) -- just the capture
    directory's subfolder names, since save() below uses the label as the
    directory itself. Empty (not an error) if nothing's been captured yet."""
    if not os.path.isdir(captures_dir):
        return []
    return sorted(name for name in os.listdir(captures_dir)
                  if os.path.isdir(os.path.join(captures_dir, name)))


def list_captures(captures_dir: str) -> List[dict]:
    """Every saved batch across every label, newest first, for the
    dashboard's Classifier tab (docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md
    S3). `id` is the `<label>/<filename>` pair save() produces -- the same
    string rename_capture()/delete_capture() below take back in, so the
    frontend never needs to construct or parse a path itself. Mirrors
    save()'s payload shape minus the vectors themselves (`frame_count`
    stands in -- the table doesn't need hundreds of raw floats per row).
    Skips any file that fails to parse rather than raising, since one
    corrupt/partial save shouldn't block the whole list from rendering."""
    if not os.path.isdir(captures_dir):
        return []
    entries: List[dict] = []
    for label in os.listdir(captures_dir):
        label_dir = os.path.join(captures_dir, label)
        if not os.path.isdir(label_dir):
            continue
        for filename in os.listdir(label_dir):
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(label_dir, filename)) as f:
                    payload = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            entries.append({
                "id": f"{label}/{filename}",
                "node_id": payload.get("node_id"),
                "device_type": payload.get("device_type"),
                "label": payload.get("label", label),
                # Which operating condition this batch was recorded under
                # (docs/UNIFIED_COMMISSIONING_PLAN.md S2.3) -- absent on
                # every capture saved before setup existed, and on every
                # manual fault recording, which is why it stays a plain
                # optional key rather than becoming part of the label.
                "condition": payload.get("condition"),
                "timestamp": payload.get("timestamp"),
                "frame_count": len(payload.get("vectors", [])),
            })
    entries.sort(key=lambda e: e["timestamp"] or 0, reverse=True)
    return entries


def _resolve_capture_path(captures_dir: str, capture_id: str) -> str:
    """Turns a dashboard-supplied `<label>/<filename>` id back into a real
    on-disk path, same path-traversal concern normalize_label() guards for
    save() -- but here the whole id, not just a label, is REST input, so
    the check is a containment check against captures_dir instead."""
    captures_root = os.path.realpath(captures_dir)
    path = os.path.realpath(os.path.join(captures_dir, capture_id))
    if not (path == captures_root or path.startswith(captures_root + os.sep)) \
            or not os.path.isfile(path):
        raise CaptureError(f"unknown capture id {capture_id!r}")
    return path


def delete_capture(captures_dir: str, capture_id: str) -> None:
    os.remove(_resolve_capture_path(captures_dir, capture_id))


def load_capture(captures_dir: str, capture_id: str) -> dict:
    """Full save()-shaped payload (node_id/device_type/label/timestamp/
    sensor_config/input_dim/vectors) for one capture -- unlike
    list_captures(), which strips `vectors` for the table view, this is
    for callers that need the actual data (api/ei_controller.py's upload()).
    Reuses _resolve_capture_path()'s path-traversal guard rather than
    duplicating it."""
    with open(_resolve_capture_path(captures_dir, capture_id)) as f:
        return json.load(f)


def save_vectors(captures_dir: str, node_id: str, label: str,
                  vectors: List[Tuple[float, ...]], sensor_config, input_dim: int,
                  device_type: Optional[str] = None,
                  condition: Optional[str] = None) -> str:
    """Writes one labeled batch to disk and returns its path -- the single
    place this file format is produced. CaptureSession.save() below is one
    caller; pipeline/commissioning.py is the other, saving each operating
    condition it collected as its own `healthy` recording
    (docs/UNIFIED_COMMISSIONING_PLAN.md S2.3). Splitting it out is what
    keeps those two from drifting into two nearly-identical payload shapes
    that api/ei_controller.py would then have to tell apart.

    condition rides alongside `label`, deliberately not folded into it: the
    labels ARE the classifier's class list, so `healthy_no_load` and
    `healthy_full_load` would hand Edge Impulse two classes that both mean
    "fine" (S2.3)."""
    safe_label = normalize_label(label)
    label_dir = os.path.join(captures_dir, safe_label)
    os.makedirs(label_dir, exist_ok=True)
    timestamp = time.time()
    # Nanosecond precision, not millisecond -- two saves for the same
    # node can otherwise land in the same millisecond (a fast
    # start/stop/save cycle, or two conditions saved back to back at the
    # end of setup) and silently overwrite each other.
    path = os.path.join(label_dir, f"{time.time_ns()}_{node_id}.json")
    payload = {
        "node_id": node_id,
        "device_type": device_type,
        "label": safe_label,
        "condition": condition,
        "timestamp": timestamp,
        "sensor_config": sorted(c.value for c in sensor_config),
        # Which of those channels were zeroed out of these vectors
        # (pipeline/features.py's MUTED_CHANNELS). Recorded per file
        # because it is NOT recoverable from the data: a muted channel's
        # columns are present and correctly sized, just constant. Without
        # it, a dataset recorded muted and one recorded unmuted look
        # identical on disk and would be pooled into one Edge Impulse
        # training run with two incompatible column meanings.
        "muted_channels": muted_channel_names(),
        "input_dim": input_dim,
        "vectors": [list(v) for v in vectors],
    }
    with open(path, "w") as f:
        json.dump(payload, f)
    return path


def rename_capture(captures_dir: str, capture_id: str, new_label: str) -> str:
    """Moves a saved batch into a different label bucket and returns its
    new id -- same directory-per-label convention save() uses, so a
    renamed capture is indistinguishable from one originally saved under
    the new label."""
    path = _resolve_capture_path(captures_dir, capture_id)
    safe_label = normalize_label(new_label)
    with open(path) as f:
        payload = json.load(f)
    payload["label"] = safe_label

    new_dir = os.path.join(captures_dir, safe_label)
    os.makedirs(new_dir, exist_ok=True)
    filename = os.path.basename(path)
    new_path = os.path.join(new_dir, filename)
    with open(new_path, "w") as f:
        json.dump(payload, f)
    if os.path.realpath(new_path) != os.path.realpath(path):
        os.remove(path)
    return f"{safe_label}/{filename}"


class CaptureSession:
    """One capture session per node, reused across repeated
    start/stop/save cycles over that node's lifetime."""

    def __init__(self, registry: Registry, captures_dir: str, node_id: str,
                 gate: MotorStateGate):
        self._registry = registry
        self._captures_dir = captures_dir
        self._node_id = node_id
        self._gate = gate
        self._state = "idle"  # idle -> capturing -> stopped -> idle
        self._collected: List[Tuple[float, ...]] = []
        self._frozen: List[Tuple[float, ...]] = []
        self._target_frames: Optional[int] = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def collected_count(self) -> int:
        return len(self._collected) if self._state == "capturing" else len(self._frozen)

    @property
    def target_frames(self) -> Optional[int]:
        return self._target_frames

    def start(self, target_frames: Optional[int] = None) -> None:
        """target_frames: auto-stop (same as an explicit stop()) the
        moment this many running-gated frames have been collected --
        "we know how many frames a good batch needs, let the count drive
        it" (2026-07-24 decision). None means manual-stop-only, no cap."""
        if self._state != "idle":
            raise CaptureError(
                f"capture already in progress for {self._node_id!r} (state={self._state!r})")
        if target_frames is not None and target_frames < 1:
            raise CaptureError("target_frames must be >= 1")
        self._collected = []
        self._target_frames = target_frames
        self._state = "capturing"

    def feed_frame(self, frame: SensorFrame) -> None:
        """Call for every frame while a session exists; a no-op for frames
        outside an active capturing window (mirrors
        CommissioningSession.feed_frame's "silently drop, don't raise for
        a live stream" contract for the wrong-node case, but raises for
        the not-capturing case so CaptureController can tell a genuinely
        idle/stopped session apart from a mis-routed frame -- same split
        commissioning.py uses)."""
        if frame.node_id != self._node_id:
            return
        if self._state != "capturing":
            raise CaptureError(f"capture not active for {self._node_id!r}")

        if self._gate.update(frame) != MotorState.RUNNING:
            return

        entry = self._registry.get(self._node_id)
        vector, _ = build_feature_vector(frame, entry.sensor_config, entry.input_dim)
        self._collected.append(vector)

        if self._target_frames is not None and len(self._collected) >= self._target_frames:
            # Counted here, in the same thread that's actually receiving
            # frames -- not left to a REST poll to notice -- so it stops
            # at exactly target_frames rather than overshooting by
            # however many frames arrived during the next poll interval.
            self._freeze()

    def stop(self) -> int:
        """Explicit manual stop -- always available regardless of
        target_frames, so a capture can be cut short early (2026-07-24:
        "need provision to stop manually as well")."""
        if self._state != "capturing":
            raise CaptureError(f"capture not active for {self._node_id!r}")
        if not self._collected:
            raise CaptureError(
                f"no running frames captured for {self._node_id!r} -- "
                "keep the motor running and try again")
        return self._freeze()

    def _freeze(self) -> int:
        """Shared by the manual stop() above and the automatic
        target_frames path in feed_frame() -- same "collected -> frozen,
        capturing -> stopped" transition either way."""
        self._frozen = list(self._collected)
        self._collected = []
        self._state = "stopped"
        return len(self._frozen)

    def save(self, label: str) -> str:
        if self._state != "stopped":
            raise CaptureError(
                f"nothing to save for {self._node_id!r}: call stop() first")
        entry = self._registry.get(self._node_id)
        # No condition= here: a manual recording is a fault sample taken
        # whenever the machine happens to show one, not one of setup's
        # deliberately-staged operating conditions (save_vectors' docstring).
        path = save_vectors(self._captures_dir, self._node_id, label, self._frozen,
                             entry.sensor_config, entry.input_dim,
                             device_type=entry.device_type)

        self._frozen = []
        self._target_frames = None
        self._state = "idle"
        return path

    def cancel(self) -> None:
        self._collected = []
        self._frozen = []
        self._target_frames = None
        self._state = "idle"
