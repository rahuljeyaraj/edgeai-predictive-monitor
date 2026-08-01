"""What trip outputs actually exist on the rig -- announced by the rig
itself, never guessed here (docs/UNIFIED_COMMISSIONING_PLAN.md S3.2).

The dashboard used to render a fixed three-motor dropdown, a hand-copy of
motor-driver/run_demo.py's MOTOR_IDS carried in frontend/app.js with a
comment conceding the duplication. A factory with one motor saw three
options, two of which were nonsense, and the copy got *worse* as the real
rig changed. The rig already knew the answer -- it rejects unknown indices
with "TRIP IGNORED: motor N is not on this rig" -- it simply never told
anyone. Now it does, and this holds what it said.

In memory only, deliberately, unlike everything in registry.py: the announce
is published RETAINED, so the broker replays it to us on every connect. A
value persisted here could outlive the rig it describes and offer an
operator an output that no longer exists; re-reading it from the broker
cannot. A rig that is switched off simply has no outputs to offer, which is
the truth.
"""
import logging
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# epm/<rig_host_node_id>/outputs. Same epm/<id>/... convention as the
# telemetry (`/data`) and command (`/cmd`) topics -- the rig host is not a
# registry node (it reports no telemetry), it's the identity that owns the
# rig's serial port, matching main.py's --trip-host-node-id.
OUTPUTS_TOPIC_FILTER = "epm/+/outputs"
OUTPUTS_TOPIC_FMT = "epm/{node_id}/outputs"


class TripOutputStore:
    """Every rig host that has announced itself, and the outputs it offers.

    Keyed by host so two rigs on one broker don't overwrite each other, even
    though today's deployment has exactly one. `idx` is what actually
    identifies an output on the wire (encode_motor_stop_payload's one byte);
    `name` is display only and may be absent."""

    def __init__(self):
        self._lock = threading.Lock()
        self._hosts: Dict[str, dict] = {}

    def announce(self, host_node_id: str, outputs: List[dict]) -> None:
        """Records (or replaces) one rig host's announced outputs. Replaces
        rather than merges: the announce is the rig's complete current
        picture of itself, so an output dropped from a later announce is
        genuinely gone, and merging would resurrect it."""
        cleaned = []
        for output in outputs:
            try:
                idx = int(output["idx"])
            except (KeyError, TypeError, ValueError):
                logger.warning("trip outputs from %r: skipping malformed entry %r",
                                host_node_id, output)
                continue
            if idx < 1:
                # Same 1-based rule Registry.set_trip_motor() enforces --
                # rejected here too so a bad announce can never put an
                # unusable option in front of an operator.
                logger.warning("trip outputs from %r: skipping non-positive idx %r",
                                host_node_id, idx)
                continue
            name = output.get("name")
            cleaned.append({"idx": idx, "name": str(name) if name else f"Motor {idx}"})
        cleaned.sort(key=lambda o: o["idx"])
        with self._lock:
            self._hosts[host_node_id] = {"outputs": cleaned, "announced_at": time.time()}
        logger.info("trip outputs announced by %s: %s",
                     host_node_id, [o["idx"] for o in cleaned])

    def snapshot(self) -> List[dict]:
        """One flat list of every announced output across every host, for
        GET /trip_outputs. Empty when nothing has announced -- which is what
        setup's manual fallback (S3.5) exists for, and is also exactly what
        an older run_demo.py with no announce looks like."""
        with self._lock:
            hosts = {host: dict(state) for host, state in self._hosts.items()}
        return [dict(output, host_node_id=host, announced_at=state["announced_at"])
                for host, state in sorted(hosts.items())
                for output in state["outputs"]]

    def host_for(self, motor_idx: int) -> Optional[str]:
        """Which rig host announced this output, or None if nobody did.
        Present for a caller that needs to publish to the right rig once
        more than one is on the broker; today's single-rig deployment gets
        the same answer from main.py's --trip-host-node-id."""
        for output in self.snapshot():
            if output["idx"] == motor_idx:
                return output["host_node_id"]
        return None
