#!/usr/bin/env python3
"""Script a repeatable demo run for the 3-motor stepper vibration rig.

Sends serial commands to the motor-driver firmware (src/main.cpp) to produce
labeled phases:

    baseline -> speed sweep -> injected fault

Each phase prints a start marker (with a wall-clock timestamp) so you can align
the captured vibration stream with the commanded state.

The firmware applies whatever RPM it's told IMMEDIATELY, with no ramp of its
own (see src/main.cpp) — so this script ramps every speed change itself via
Rig.ramp_to(), the same way dashboard.html does, instead of jumping straight
to a target that could stall a motor.

Requires pyserial:  pip install pyserial

Examples:
    python3 run_demo.py --port /dev/ttyACM0
    python3 run_demo.py --port COM5 --baseline-rpm 90 --sweep-hi 180
"""

import argparse
import json
import math
import struct
import sys
import threading
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial not installed. Run: pip install pyserial")

MOTOR_IDS = (1, 2, 3)
RAMP_RPM_S = 150.0   # matches the firmware's old proven-safe ramp rate
RAMP_TICK_S = 0.1

# --- machinery-protection trip (docs/MOTOR_STOP_PLAN.md) ---------------
#
# The base station publishes [TYPE:1B][PAYLOAD] to epm/<node_id>/cmd. These
# two constants are a deliberate hand-rolled copy of
# base-station/python/common/wire_protocol.py's MqttMsgType.MOTOR_STOP and
# MOTOR_STOP_PAYLOAD_FMT: this script runs on the host laptop that owns the
# rig's USB port, a different machine entirely from the UNO Q the base-station
# package deploys to, so it can't import them. Same convention the ESP32
# satellite firmware already follows for the telemetry formats.
#
# wire_protocol.py is the source of truth. base-station/tests/
# wire_protocol_test.py asserts the exact bytes this copy expects, so the two
# can't drift silently.
MQTT_MSG_TYPE_MOTOR_STOP = 0x09
MOTOR_STOP_PAYLOAD_FMT = "<B"  # motor_idx, 1-based

# Every asset's command topic, not one node's. A trip is addressed by the
# motor_idx in its payload -- that is what identifies an output on this rig;
# the node_id in the topic is the *asset that faulted*, which is incidental
# here. Subscribing to a single node's topic (which this did, from a CLI arg)
# meant a second monitored asset's trip was published into the void, no matter
# which output it was mapped to (docs/UNIFIED_COMMISSIONING_PLAN.md S1.1/S3.2).
CMD_TOPIC_FILTER = "epm/+/cmd"

# This rig's own retained self-description, so the base station stops guessing
# how many outputs exist (S3.2). Published once on connect and retained, so a
# base station that starts later still receives it. JSON, unlike the binary
# command direction above: this is host-to-host, variable-length, and never on
# a hot path -- no MCU firmware parses it.
OUTPUTS_TOPIC_FMT = "epm/{node_id}/outputs"


def marker(label: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] === {label} ===", flush=True)


class Rig:
    def __init__(self, port: str, baud: int = 115200):
        # 2 s settle: opening the port resets the Uno.
        self.ser = serial.Serial(port, baud, timeout=1)
        time.sleep(2.0)
        # Re-entrant, and it guards BOTH the serial writes and self.rpm:
        # once a trip listener thread exists, two threads issue commands on one
        # port. Unsynchronized writes interleave mid-line and the firmware's
        # single-char parser (src/main.cpp) reads the result as garbage.
        # Re-entrant because disable()/stop_motor() call motor() while holding
        # it, and both need to be atomic as a whole.
        self._lock = threading.RLock()
        self._drain()
        self.rpm = {idx: 0.0 for idx in MOTOR_IDS}  # last RPM sent per motor
        # Motors stopped by a protection trip. Commands for these are refused
        # until clear_trip() -- see motor() below.
        self._tripped = set()

    def _drain(self) -> None:
        while self.ser.in_waiting:
            self.ser.readline()

    def send(self, cmd: str) -> None:
        with self._lock:
            self.ser.write((cmd.strip() + "\n").encode())
            self.ser.flush()

    def motor(self, idx: int, rpm: float) -> None:
        with self._lock:
            # A tripped motor stays stopped until a human clears it. Without
            # this, run_profile()'s scripted ramps (or dashboard.html's ramp
            # tick) would cheerfully command the tripped motor back up to
            # speed on the very next tick, undoing the trip a second after it
            # landed. "Only a human restarts a machine" has to be enforced
            # here, at the one place that writes speeds, not just promised.
            if idx in self._tripped and abs(rpm) > 0.05:
                return
            self.send(f"{idx} {rpm:.1f}")
            self.rpm[idx] = rpm

    def stop_motor(self, idx: int) -> None:
        """Machinery-protection trip: stop ONE motor, leaving the others
        running.

        Sends "<idx> 0" rather than "d", because the CNC shield has a single
        shared ~ENABLE -- "d" would cut all three. A stepper commanded to 0 RPM
        stops turning (and so stops producing vibration, which is what lets the
        base station confirm the trip) while still holding position. "d" is
        only safe once every motor is already at zero, which is the same rule
        dashboard.html's refreshEnergize() follows."""
        with self._lock:
            self._tripped.add(idx)
            self.rpm[idx] = 0.0
            self.send(f"{idx} 0.0")
            if all(abs(v) <= 0.05 for v in self.rpm.values()):
                self.send("d")

    def clear_trip(self, idx: int) -> None:
        """Re-arm a tripped motor. Deliberately never called from the trip
        listener -- only from a human path."""
        with self._lock:
            self._tripped.discard(idx)

    def ramp_to(self, targets: dict, rate: float = RAMP_RPM_S, tick: float = RAMP_TICK_S) -> None:
        """Ramp the given {motor_id: target_rpm} from their last-sent RPM,
        blocking until every motor in `targets` has arrived.

        A tripped motor is treated as already arrived. It must be: motor()
        refuses to move it, so waiting for it to reach a nonzero target would
        never finish -- a trip landing mid-profile would hang this loop (and
        the whole script) forever instead of letting the remaining motors
        carry on."""
        max_delta = rate * tick
        while True:
            done = True
            for idx, target in targets.items():
                if idx in self._tripped:
                    continue
                cur = self.rpm[idx]
                if abs(target - cur) > 0.05:
                    done = False
                    step = math.copysign(min(abs(target - cur), max_delta), target - cur)
                    self.motor(idx, cur + step)
            if done:
                break
            time.sleep(tick)
        for idx, target in targets.items():  # snap exactly to the target
            if idx in self._tripped:
                continue
            self.motor(idx, target)

    def enable(self) -> None:
        self.send("e")

    def disable(self) -> None:
        # Zero every motor before cutting power, never just "d". The firmware
        # keeps each motor's last commanded speed, and the shield's single
        # shared ~ENABLE means the next "e" re-applies all of them at once --
        # so a disable-without-zero leaves the whole rig armed to jump back to
        # speed the instant anything re-enables it.
        with self._lock:
            for idx in MOTOR_IDS:
                # Bypasses motor()'s tripped-motor refusal: zero is the one
                # command a tripped motor must always accept.
                self.send(f"{idx} 0.0")
                self.rpm[idx] = 0.0
            self.send("d")

    def close(self) -> None:
        try:
            self.disable()
        finally:
            self.ser.close()


class TripListener:
    """Subscribes to the base station's command topics, applies MOTOR_STOP to
    the rig, and announces which outputs this rig has. One-way by
    construction: nothing here can start a motor or set a speed, so the worst
    a compromised or confused broker can do is stop the machine -- which is
    the safe direction.

    Optional. With no --mqtt-host the rig stays exactly as standalone as it has
    always been (docs/motor-driver.md S1), which is the point: monitoring is
    something a site adds to a working rig, not a dependency of it."""

    def __init__(self, rig: Rig, host: str, port: int, node_id: str):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            sys.exit("paho-mqtt not installed (needed for --mqtt-host). "
                      "Run: pip install paho-mqtt")
        self._rig = rig
        self._topic = CMD_TOPIC_FILTER
        self._outputs_topic = OUTPUTS_TOPIC_FMT.format(node_id=node_id)
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        # connect_async + loop_start: a broker that isn't up yet must not stop
        # the rig from running. Same reasoning as the base station's own
        # publisher.
        self._client.connect_async(host, port)
        self._host = host
        self._port = port

    def start(self) -> None:
        self._client.loop_start()
        marker(f"TRIP LISTENER armed on {self._host}:{self._port} -> {self._topic}")

    def _on_message(self, client, userdata, message):
        data = message.payload
        if len(data) < 2 or data[0] != MQTT_MSG_TYPE_MOTOR_STOP:
            return  # not a trip -- e.g. a STATUS_LED meant for a real node
        motor_idx = struct.unpack(MOTOR_STOP_PAYLOAD_FMT, data[1:2])[0]
        if motor_idx not in MOTOR_IDS:
            # Now that this subscribes to every asset's cmd topic, this is
            # also the normal case for a trip belonging to another rig on the
            # same broker -- not only a misconfiguration.
            marker(f"TRIP IGNORED: motor {motor_idx} is not on this rig")
            return
        marker(f"*** TRIP RECEIVED: stopping motor {motor_idx} ***")
        try:
            self._rig.stop_motor(motor_idx)
        except Exception as e:  # a dead port must not kill the paho thread
            marker(f"TRIP FAILED to reach the rig: {e}")
            return
        marker(f"motor {motor_idx} stopped -- restart it by hand "
                f"(this script will not)")

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        # Subscribe from on_connect, not once at construction: a reconnect
        # after a broker restart otherwise leaves this silently unsubscribed
        # and the trip would never arrive.
        if reason_code == 0:
            client.subscribe(self._topic, qos=1)
            self._announce_outputs()
            marker("TRIP LISTENER connected to broker")
        else:
            marker(f"TRIP LISTENER connect failed (reason={reason_code})")

    def _announce_outputs(self) -> None:
        """Tell the base station what this rig actually has (S3.2). Retained,
        and republished on every reconnect, so the announce survives a base
        station restart without this script restarting too.

        MOTOR_IDS is the single source of truth for the fleet now -- the rig
        already refused unknown indices (_on_message above), it just never
        told anyone, which is how the dashboard ended up carrying a hardcoded
        copy that offered three motors to a one-motor factory."""
        payload = json.dumps({
            "outputs": [{"idx": idx, "name": f"Motor {idx}"} for idx in MOTOR_IDS],
        })
        self._client.publish(self._outputs_topic, payload, qos=1, retain=True)
        marker(f"ANNOUNCED outputs {list(MOTOR_IDS)} on {self._outputs_topic}")


def run_profile(rig: Rig, args) -> None:
    marker(f"BASELINE {args.baseline_rpm} RPM, all 3 motors ({args.baseline_s}s)")
    rig.enable()
    rig.ramp_to({idx: args.baseline_rpm for idx in MOTOR_IDS})
    time.sleep(args.baseline_s)

    marker(f"SWEEP {args.sweep_lo}->{args.sweep_hi} RPM ({args.sweep_s}s)")
    steps = max(1, int(args.sweep_s / args.sweep_step_s))
    for i in range(steps + 1):
        rpm = args.sweep_lo + (args.sweep_hi - args.sweep_lo) * i / steps
        rig.ramp_to({idx: rpm for idx in MOTOR_IDS})
        time.sleep(args.sweep_step_s)

    marker(f"FAULT: motor1 -> {args.fault_rpm} RPM, motors 2+3 held ({args.fault_s}s)")
    # RPM step on one motor = obvious anomaly / imbalance-like signature.
    rig.ramp_to({1: args.fault_rpm, 2: args.baseline_rpm, 3: args.baseline_rpm})
    time.sleep(args.fault_s)

    marker("DONE (ramping to a stop)")
    rig.ramp_to({idx: 0.0 for idx in MOTOR_IDS})
    rig.disable()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", required=True, help="serial port, e.g. /dev/ttyACM0 or COM5")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--baseline-rpm", type=float, default=90.0)
    p.add_argument("--baseline-s", type=float, default=45.0)
    p.add_argument("--sweep-lo", type=float, default=60.0)
    p.add_argument("--sweep-hi", type=float, default=180.0)
    p.add_argument("--sweep-s", type=float, default=30.0)
    p.add_argument("--sweep-step-s", type=float, default=1.0)
    p.add_argument("--fault-rpm", type=float, default=220.0)
    p.add_argument("--fault-s", type=float, default=30.0)

    p.add_argument("--mqtt-host", default=None,
                    help="Base station's broker address. Optional -- without it the "
                         "rig runs completely standalone, exactly as before. With it, "
                         "listen for machinery-protection trips and stop the named "
                         "motor when one arrives (one-way: trips only, never speeds).")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--trip-node-id", default="motor_rig",
                    help="This rig host's MQTT identity, used to announce which "
                         "outputs it has (epm/<id>/outputs); must match the base "
                         "station's --trip-host-node-id. Trips themselves are now "
                         "received on every asset's topic and routed by the "
                         "motor index in the payload, so this no longer decides "
                         "which trips arrive.")
    p.add_argument("--hold-open", action="store_true",
                    help="Skip the scripted profile and just idle with the trip "
                         "listener armed, for driving the motors from elsewhere "
                         "(e.g. dashboard.html) while protection is live.")
    args = p.parse_args()

    rig = Rig(args.port, args.baud)
    trip = None
    if args.mqtt_host:
        trip = TripListener(rig, args.mqtt_host, args.mqtt_port, args.trip_node_id)
        trip.start()
    try:
        if args.hold_open:
            marker("IDLE (trip listener armed) -- Ctrl-C to stop")
            while True:
                time.sleep(1.0)
        else:
            run_profile(rig, args)
    except KeyboardInterrupt:
        marker("INTERRUPTED (disabling drivers)")
    finally:
        if trip is not None:
            trip.stop()
        rig.close()


if __name__ == "__main__":
    main()
