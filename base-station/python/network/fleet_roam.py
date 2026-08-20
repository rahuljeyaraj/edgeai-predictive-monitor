"""Fleet WiFi roaming (docs/WIFI_ONBOARDING_PLAN.md S6) -- hand every
satellite the network the base station is about to join, *before* it joins
it, so one form in the Network tab onboards the whole fleet instead of one
captive portal per device.

Ordering is the whole design, and it is forced:

  push credentials to the fleet  ->  wait for acks  ->  base station switches

The base station has a single radio (S1's "Implementation notes"): joining
the factory network drops its own EPM-BaseStation hotspot outright. Every
satellite reachable through that hotspot therefore loses its only path to us
at the instant we switch -- so the *last* moment we can tell them anything is
before our own join, not after. There is no "switch first and coordinate
afterwards" variant of this feature.

The same constraint shapes the ack. A node acks when it has taken the
credentials, not when it has joined: its join tears down the very link the
ack would travel on. So an ack here means "will switch", and the real
confirmation is the node reappearing on the dashboard once both ends are on
the new network. `push()`'s result is named accordingly (`acked`, not
`joined`) -- see api/app.py's route and frontend/network.js's copy, which
both say "told to move," never "moved."

Consequences worth knowing:

- A wrong password costs the fleet one bounded join attempt each (they roll
  back to the network they were on, satellite-side). The base station's own
  join then fails too and it stays on the hotspot, which is where the
  rolled-back nodes still are -- so the common typo case self-heals.
- Nodes are pushed the base station's mDNS name as their new broker address,
  never an IP: the address we are about to get from the factory network's
  DHCP is unknown at push time, and would change on renewal anyway. A node
  that ends up on a network where mDNS is blocked (S4's caveat) falls back to
  its own AP after a bounded rendezvous window (satellite firmware's
  ROAM_RENDEZVOUS_TIMEOUT_MS), where a technician can type a raw IP.

This owns a short-lived MQTT client of its own rather than borrowing
ingestion/mqtt_publisher.py's: a roam is a rare, technician-driven action
that needs a *subscription* (the ack topic) the publish-only client doesn't
have, and keeping it separate means nothing in this path can perturb the
live telemetry client's connection.
"""
import logging
import threading
import time
from typing import Dict, List, Optional

import paho.mqtt.client as mqtt

from wire_protocol import (
    MqttMsgType,
    WifiProvisionAckStatus,
    decode_mqtt_message,
    decode_wifi_provision_ack_payload,
    encode_mqtt_message,
    encode_wifi_provision_payload,
)

logger = logging.getLogger(__name__)

CMD_TOPIC_FMT = "epm/{node_id}/cmd"
EVT_TOPIC_FILTER = "epm/+/evt"

# What nodes are told to use as their broker address after the roam. Matches
# satellite/include/app_config.h's MQTT_BROKER_HOST default and the hostname
# host/wifi_bridge.py's board actually publishes over avahi (S1's
# "Implementation notes": the board was renamed epm -> epm-base for exactly
# this name). Deliberately not derived from socket.gethostname(): inside the
# App Lab container that returns the *container's* name, not the board's.
BASE_STATION_MDNS_HOST = "epm-base.local"

# How long push() waits for the fleet to answer. Sized for "a node's MQTT
# client is mid-backoff when the push lands" (satellite firmware's
# MQTT_RECONNECT_BACKOFF_MS), not for a network round trip -- an ack itself
# comes back in milliseconds on a healthy link. Kept well under the
# frontend's own fetch patience so a slow fleet reads as "no answer from
# node X" rather than as a dead request.
DEFAULT_ACK_TIMEOUT_S = 8.0

CONNECT_TIMEOUT_S = 5.0


class FleetRoamer:
    """One instance per app, cheap to hold: the MQTT connection is opened
    and closed inside each push(), so an idle roamer holds no socket."""

    def __init__(self, host: str, port: int,
                  broker_host_for_nodes: str = BASE_STATION_MDNS_HOST):
        self._host = host
        self._port = port
        self._broker_host_for_nodes = broker_host_for_nodes
        # Monotonic-ish and unique per roam without persisting anything: a
        # reboot mid-roam can only ever *increase* this (wall clock), so a
        # stale ack from before the reboot can't match a later push.
        self._next_roam_id = int(time.time()) & 0xFFFFFFFF
        self._lock = threading.Lock()

    def _claim_roam_id(self) -> int:
        with self._lock:
            self._next_roam_id = (self._next_roam_id + 1) & 0xFFFFFFFF
            return self._next_roam_id

    def push(self, ssid: str, password: str, node_ids: List[str],
              ack_timeout_s: float = DEFAULT_ACK_TIMEOUT_S,
              broker_port: Optional[int] = None) -> dict:
        """Tells `node_ids` to move to `ssid` and waits (bounded) for their
        acks. Returns

            {"roam_id": int, "broker_host": str, "nodes": [
                {"node_id": str, "status": "accepted"|"simulated"
                                           |"rejected"|"no_answer"}, ...]}

        Never raises for a node that stays silent -- that is a normal,
        reportable outcome ("this one will need its own setup"), not an
        error. Raises RuntimeError only if the broker itself is unreachable,
        which the route turns into a 503 the same way python/network/wifi.py's
        connect() does for a missing wifi-bridge.
        """
        if not node_ids:
            return {"roam_id": 0, "broker_host": self._broker_host_for_nodes, "nodes": []}

        roam_id = self._claim_roam_id()
        payload = encode_wifi_provision_payload(
            roam_id, ssid, password, self._broker_host_for_nodes,
            broker_port if broker_port is not None else self._port)
        message = encode_mqtt_message(MqttMsgType.WIFI_PROVISION, payload)

        acks: Dict[str, int] = {}
        done = threading.Event()
        pending = set(node_ids)

        def on_message(_client, _userdata, msg):
            node_id = _node_id_from_evt_topic(msg.topic)
            if node_id is None:
                return
            try:
                msg_type, body = decode_mqtt_message(msg.payload)
                if msg_type != MqttMsgType.WIFI_PROVISION_ACK:
                    return
                acked_roam_id, status = decode_wifi_provision_ack_payload(body)
            except Exception:
                logger.warning("malformed provision ack on %s", msg.topic)
                return
            # A node that acks an *earlier* roam (a retry after a timeout,
            # or a duplicate delivery) must not be counted here -- see the
            # module docstring on why roam_id exists.
            if acked_roam_id != roam_id:
                return
            acks[node_id] = status
            pending.discard(node_id)
            if not pending:
                done.set()

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.on_message = on_message
        try:
            client.connect(self._host, self._port, keepalive=30)
        except OSError as exc:
            raise RuntimeError(f"MQTT broker unreachable at "
                                f"{self._host}:{self._port}: {exc}") from exc
        client.loop_start()
        try:
            # Subscribe before publishing, not after: a node on a healthy
            # link acks within milliseconds, easily inside the window a
            # subscribe-afterwards would leave open.
            client.subscribe(EVT_TOPIC_FILTER, qos=1)
            for node_id in node_ids:
                # QoS 1, non-retained. Not retained deliberately: a retained
                # credential push would be replayed to any node connecting
                # later -- including one a technician has just deliberately
                # re-provisioned onto a different network by hand, which it
                # would silently undo.
                client.publish(CMD_TOPIC_FMT.format(node_id=node_id), message, qos=1)
            done.wait(ack_timeout_s)
        finally:
            client.loop_stop()
            client.disconnect()

        nodes = [{"node_id": node_id,
                   "status": _ack_status_name(acks.get(node_id))}
                  for node_id in node_ids]
        logger.info("fleet roam %d to %r: %d/%d acked", roam_id, ssid,
                    sum(1 for n in nodes if n["status"] != "no_answer"), len(nodes))
        return {"roam_id": roam_id, "broker_host": self._broker_host_for_nodes,
                 "nodes": nodes}


def _node_id_from_evt_topic(topic: str) -> Optional[str]:
    """epm/<node_id>/evt -> <node_id>, or None if it isn't that shape.
    Mirrors ingestion/mqtt_subscriber.py's _node_id_from_topic(), but
    returning None instead of raising: an odd topic here is something to
    ignore inside a paho callback, not an ingestion-path parse failure to
    count."""
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "epm" or parts[2] != "evt" or not parts[1]:
        return None
    return parts[1]


def _ack_status_name(status: Optional[int]) -> str:
    if status is None:
        return "no_answer"
    if status == WifiProvisionAckStatus.ACCEPTED:
        return "accepted"
    if status == WifiProvisionAckStatus.SIMULATED:
        return "simulated"
    return "rejected"
