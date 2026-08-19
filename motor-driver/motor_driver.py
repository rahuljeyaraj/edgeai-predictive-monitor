#!/usr/bin/env python3
"""Host-side controller for the 3-motor stepper vibration rig.

This is the process that owns the rig on its host machine. It does three
things, each of which works without the other two:

  1. **Serves the control page.** `dashboard.html` is served from here rather
     than from a bare `python3 -m http.server`, so the page has somewhere to
     ask which motors are installed and whether one has been tripped.
  2. **Listens for machinery-protection trips** over MQTT and stops the named
     motor (`--mqtt-host`). One-way: nothing here can start a motor from the
     network.
  3. **Runs a scripted capture profile** (`--profile`) — baseline -> speed
     sweep -> injected fault, each phase printing a timestamped marker so a
     captured vibration stream can be aligned with the commanded state.

The firmware applies whatever RPM it's told IMMEDIATELY, with no ramp of its
own (see src/main.cpp) — so this script ramps every speed change itself via
Rig.ramp_to(), the same way dashboard.html does, instead of jumping straight
to a target that could stall a motor.

Requires pyserial (and paho-mqtt for --mqtt-host):
    pip install pyserial paho-mqtt

Examples:
    ./start_motor_driver.sh                          # everything defaulted (broker at epm-base.local)
    ./start_motor_driver.sh --mqtt-host uno-q.local  # broker at a different host
    ./start_motor_driver.sh --mqtt-host ''           # no broker at all
    python3 motor_driver.py --port COM5 --profile --baseline-rpm 90
"""

import argparse
import json
import math
import os
import struct
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial not installed. Run: pip install pyserial")

# Every motor this rig is physically wired for -- the hardware's capability,
# fixed by src/main.cpp and the CNC shield's three driver slots. Which of them
# are actually installed and offered to the base station is a separate,
# runtime-editable question: see OutputSet.
MOTOR_IDS = (1, 2, 3)

# USB (vendor, product) IDs of the boards this rig's firmware runs on.
#
# Deliberately a whitelist of Uno-shaped boards rather than "anything with an
# Arduino vendor ID". On this bench the UNO Q base station is ALSO an
# Arduino-VID CDC device on /dev/ttyACM* (2341:0078, "UNO Q - epm-base"), and
# an autodetect that grabbed the first Arduino would open the monitoring
# board's port and start writing stepper commands into it.
UNO_USB_IDS = {
    (0x2341, 0x0043), (0x2341, 0x0001), (0x2341, 0x0243),  # Arduino Uno R3
    (0x2A03, 0x0043), (0x2A03, 0x0001),                    # Arduino SRL Uno
    (0x1A86, 0x7523),                                      # CH340 clone
    (0x0403, 0x6001),                                      # FTDI-based clone
}


def find_uno_port() -> str:
    """The port an Uno is on, when there is exactly one and no doubt about it.

    Raises RuntimeError otherwise -- with the list of what it actually saw.
    Guessing is worse than asking here: the wrong port is either a board that
    ignores the commands, or the base station being written to."""
    from serial.tools import list_ports

    ports = list(list_ports.comports())
    matches = [p for p in ports if (p.vid, p.pid) in UNO_USB_IDS]
    if len(matches) == 1:
        return matches[0].device

    # Only list USB serial devices in the error: a Linux host has eight
    # /dev/ttyS* legacy ports that are never the answer and only add noise.
    seen = [f"  {p.device}  {p.product or p.description}"
             + (f"  [{p.vid:04x}:{p.pid:04x}]" if p.vid else "")
             for p in ports if p.vid]
    detail = ("\n" + "\n".join(seen)) if seen else " (none)"
    if not matches:
        raise RuntimeError(
            f"couldn't find an Arduino Uno. USB serial devices seen:{detail}\n"
            f"Pass --port explicitly, e.g. --port /dev/ttyACM0.")
    raise RuntimeError(
        f"found {len(matches)} Uno-like boards, so which one is the rig is a "
        f"guess. USB serial devices seen:{detail}\nPass --port explicitly.")


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

# The broker lives on the UNO Q, and there are exactly two ways this host
# reaches it: straight to :1883 over the LAN, or through `adb forward
# tcp:11883 tcp:1883` when the board is in AP mode and has no LAN address --
# which is most of the time on this bench. Rather than make that a flag
# everyone has to remember, prefer the forwarded port when something is
# actually listening on it. Getting this wrong is silent: the rig connects to
# nothing, publishes no announce, and the base station goes on showing a stale
# retained motor list, which looks like a bug in the dashboard.
ADB_FORWARDED_MQTT_PORT = 11883
DEFAULT_MQTT_PORT = 1883


def default_mqtt_port(host: str) -> int:
    """The adb-forwarded port if it's live, else the standard one."""
    import socket

    if host in ("localhost", "127.0.0.1", "::1"):
        try:
            with socket.create_connection((host, ADB_FORWARDED_MQTT_PORT), timeout=0.4):
                return ADB_FORWARDED_MQTT_PORT
        except OSError:
            pass
    return DEFAULT_MQTT_PORT


def marker(label: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] === {label} ===", flush=True)


class Rig:
    """The rig's motors, and whatever this process can do to them.

    `port=None` is the DEFAULT and the normal case, because the control page
    has always driven the motors over Web Serial from the browser -- and on
    the usual split setup (rig host in WSL, Chrome and the USB device on
    Windows) the browser is the only thing that can reach the Uno at all.
    Forwarding the device into WSL with usbipd doesn't help: it takes the port
    away from Chrome, and then the control page has nothing to connect to.

    So without a port this object is the rig's *bookkeeping* -- which motors
    are tripped, what speed each was last told -- and the control page applies
    the actual stop. With a port it also writes the bytes itself, which is
    what `--profile` and browser-less protection need.
    """

    def __init__(self, port: str = None, baud: int = 115200):
        self.port = port
        self.ser = None
        if port is not None:
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
        if self.ser is not None:
            self._drain()
        self.rpm = {idx: 0.0 for idx in MOTOR_IDS}  # last RPM sent per motor
        # Motors stopped by a protection trip. Commands for these are refused
        # until clear_trip() -- see motor() below.
        self._tripped = set()

    @property
    def has_serial(self) -> bool:
        return self.ser is not None

    def _drain(self) -> None:
        while self.ser.in_waiting:
            self.ser.readline()

    def send(self, cmd: str) -> None:
        with self._lock:
            if self.ser is None:
                # No port here: the control page holds it. Every caller still
                # updates self.rpm / self._tripped around this, so the state
                # the page reads from /api/state stays correct and the page
                # applies the command. Silently dropping the write is the
                # whole point -- callers must not have to know which mode
                # they're in.
                return
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

    def tripped(self) -> list:
        """Which motors are currently held stopped by a trip.

        Read by the control server so dashboard.html can show it. Until this
        existed, a trip was visible only as a line in this script's terminal:
        the control page went on showing the motor as RUNNING at its old
        speed, because nothing ever told it otherwise. Worse, its ramp tick
        was still free to command that motor back up the moment anyone
        touched a slider."""
        with self._lock:
            return sorted(self._tripped)

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
        if self.ser is None:
            return
        try:
            self.disable()
        finally:
            self.ser.close()


class OutputSet:
    """Which motors are installed on this rig *right now*.

    Not the same question as MOTOR_IDS. MOTOR_IDS is what the shield could
    drive; this is what a human has actually bolted down and wants offered to
    the base station as a trip output. A rig wired for three motors where only
    one drives a monitored asset has exactly one meaningful trip output, and
    putting the other two in front of an operator invites mapping a sensor to
    a motor that has nothing to do with it.

    Mutable at runtime, and that is the point: adding a motor on the control
    page re-announces, so a new trip output appears in the base station's
    setup without restarting anything. Subscribers are notified on change --
    TripListener registers its announce here.
    """

    def __init__(self, outputs=MOTOR_IDS):
        self._lock = threading.Lock()
        self._outputs = self._clean(outputs)
        self._listeners = []

    @staticmethod
    def _clean(outputs) -> tuple:
        return tuple(sorted({int(idx) for idx in outputs}))

    def get(self) -> tuple:
        with self._lock:
            return self._outputs

    def set(self, outputs) -> tuple:
        """Replace the installed set. Raises ValueError for anything the rig
        isn't wired for -- an offered output the rig would then refuse is the
        one mapping that looks armed and does nothing."""
        cleaned = self._clean(outputs)
        unknown = [idx for idx in cleaned if idx not in MOTOR_IDS]
        if unknown:
            raise ValueError(f"motors {unknown} are not on this rig {list(MOTOR_IDS)}")
        with self._lock:
            self._outputs = cleaned
            listeners = list(self._listeners)
        # Outside the lock: an announce publishes over the network, and a
        # listener that blocks must not freeze every other reader of this set.
        for callback in listeners:
            callback(cleaned)
        return cleaned

    def subscribe(self, callback) -> None:
        with self._lock:
            self._listeners.append(callback)


class ControlServer:
    """Serves dashboard.html plus the small JSON API it needs.

    The control page is a Web Serial page: it drives the Uno over USB and has
    no MQTT client of its own, and this repo's broker is TCP-only (no
    websockets listener), so the page cannot publish the rig's announce
    itself. It doesn't have to. This process already holds the MQTT client, so
    the page just asks *it* -- browser -> localhost HTTP -> this script ->
    retained announce. No broker change, no websockets listener, no root on
    the base station.

    Serving the page from here rather than from `python3 -m http.server` is
    what makes that hop same-origin, and means there is one command to start
    the rig instead of two that have to agree with each other.

    Read-mostly and deliberately tiny: the only two mutations it accepts are
    "which motors are installed" and "clear this trip", both of which are
    things a human standing at the rig is entitled to do.
    """

    def __init__(self, rig: "Rig", outputs: OutputSet, port: int,
                  directory: str, base_station_url=None):
        self._rig = rig
        self._outputs = outputs
        self._base_station_url = base_station_url
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), self._make_handler(directory))
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                         name="control-server", daemon=True)

    # -- state exposed to the page ------------------------------------------
    def state(self) -> dict:
        return {
            "motor_ids": list(MOTOR_IDS),
            "installed": list(self._outputs.get()),
            "tripped": self._rig.tripped() if self._rig else [],
            "base_station_url": self._base_station_url,
            # Whether THIS process holds the serial port. When it doesn't, the
            # page is the only thing that can act on a trip, and has to send
            # the stop itself rather than assume it already happened.
            "rig_serial": bool(self._rig and self._rig.has_serial),
        }

    def _set_installed(self, body: dict) -> dict:
        self._outputs.set(body.get("installed", []))
        return self.state()

    def _clear_trip(self, body: dict) -> dict:
        idx = int(body["idx"])
        if idx not in MOTOR_IDS:
            raise ValueError(f"motor {idx} is not on this rig")
        # Only ever from here: a trip that cleared itself, or that the trip
        # listener could clear, would not be a trip.
        self._rig.clear_trip(idx)
        marker(f"TRIP CLEARED by hand: motor {idx} is commandable again")
        return self.state()

    def _make_handler(self, directory: str):
        server = self

        class Handler(SimpleHTTPRequestHandler):
            server_version = "MotorDriver/1.0"

            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=directory, **kwargs)

            def do_GET(self):
                if self.path.split("?")[0] == "/api/state":
                    return self._respond(200, server.state())
                if self.path == "/":
                    self.path = "/dashboard.html"
                return super().do_GET()

            def do_POST(self):
                routes = {
                    "/api/installed": server._set_installed,
                    "/api/trip/clear": server._clear_trip,
                }
                handler = routes.get(self.path.split("?")[0])
                if handler is None:
                    return self._respond(404, {"error": "no such endpoint"})
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length) or b"{}")
                    return self._respond(200, handler(body))
                except (ValueError, KeyError, TypeError) as e:
                    # A bad request must not take the server down: the page
                    # polls this, so one 500 would become a 1 Hz stream of
                    # them.
                    return self._respond(400, {"error": str(e)})

            def _respond(self, code: int, payload: dict):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                # Silence the per-request log. The page polls /api/state once
                # a second, and this terminal's job is to show phase and trip
                # markers -- which a request log would bury.
                pass

        return Handler

    def start(self) -> None:
        self._thread.start()
        marker(f"CONTROL PAGE at http://localhost:{self.port}/  "
                f"(Chrome or Edge -- it needs Web Serial)")

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


class TripListener:
    """Subscribes to the base station's command topics, applies MOTOR_STOP to
    the rig, and announces which outputs this rig has. One-way by
    construction: nothing here can start a motor or set a speed, so the worst
    a compromised or confused broker can do is stop the machine -- which is
    the safe direction.

    Optional. With no --mqtt-host the rig stays exactly as standalone as it has
    always been (docs/motor-driver.md S1), which is the point: monitoring is
    something a site adds to a working rig, not a dependency of it."""

    def __init__(self, rig: Rig, host: str, port: int, node_id: str,
                  outputs: OutputSet = None):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            sys.exit("paho-mqtt not installed (needed for --mqtt-host). "
                      "Run: pip install paho-mqtt")
        self._rig = rig
        self._topic = CMD_TOPIC_FILTER
        # Which outputs to OFFER -- not the same question as which motors this
        # rig can drive (MOTOR_IDS, still the trip whitelist in _on_message).
        # Shared with the control server and subscribed to, so a motor added
        # or removed on the control page is re-announced immediately rather
        # than at the next restart of this script.
        self._outputs = outputs if outputs is not None else OutputSet()
        self._outputs.subscribe(self._announce_outputs)
        self._outputs_topic = OUTPUTS_TOPIC_FMT.format(node_id=node_id)
        self._connected = False
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
        # paho's connect_async retries forever without saying anything, so a
        # wrong host/port looks identical to a working one until you notice
        # the base station is showing a stale retained motor list. Say it out
        # loud instead -- this exact silence cost a debugging session.
        threading.Timer(6.0, self._warn_if_not_connected).start()

    def _warn_if_not_connected(self) -> None:
        if self._connected:
            return
        marker(f"!! NOT CONNECTED to {self._host}:{self._port} after 6s -- nothing "
                f"has been announced, so the base station is still showing whatever "
                f"a previous run left retained. Check the broker, or --mqtt-port.")

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
        if self._rig.has_serial:
            marker(f"motor {motor_idx} stopped -- restart it by hand "
                    f"(this script will not)")
        else:
            # Be precise about what just happened. Without a serial port here,
            # nothing has physically stopped yet: the control page picks this
            # up on its next poll and sends the stop. If no page is open, the
            # motor keeps running, and saying "stopped" would be a lie.
            marker(f"motor {motor_idx} trip recorded -- the control page will "
                    f"apply it (nothing stops if no page is open)")

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        # Subscribe from on_connect, not once at construction: a reconnect
        # after a broker restart otherwise leaves this silently unsubscribed
        # and the trip would never arrive.
        if reason_code == 0:
            self._connected = True
            client.subscribe(self._topic, qos=1)
            self._announce_outputs()
            marker("TRIP LISTENER connected to broker")
        else:
            marker(f"TRIP LISTENER connect failed (reason={reason_code})")

    def _announce_outputs(self, outputs=None) -> None:
        """Tell the base station what this rig actually has (S3.2). Retained,
        and republished on every reconnect, so the announce survives a base
        station restart without this script restarting too.

        Called on connect and again on every change to the installed set, so
        adding a motor on the control page makes a new trip output appear in
        the base station's setup live.

        The rig already refused unknown indices (_on_message above), it just
        never told anyone, which is how the dashboard ended up carrying a
        hardcoded copy that offered three motors to a one-motor factory.

        An empty set publishes an empty list, not nothing: that is the
        announce that says "this rig offers no trip outputs", and it also
        clears a previous retained announce off the broker. Staying silent
        would instead leave the last retained one standing forever."""
        if outputs is None:
            outputs = self._outputs.get()
        payload = json.dumps({
            "outputs": [{"idx": idx, "name": f"Motor {idx}"} for idx in outputs],
        })
        self._client.publish(self._outputs_topic, payload, qos=1, retain=True)
        marker(f"ANNOUNCED outputs {list(outputs)} on {self._outputs_topic}")


def run_profile(rig: Rig, motors, args) -> None:
    """The scripted capture run: baseline -> sweep -> injected fault, over
    whichever motors are installed."""
    motors = list(motors)
    if not motors:
        marker("NOTHING TO RUN: no motors installed (see --motors)")
        return

    marker(f"BASELINE {args.baseline_rpm} RPM, {len(motors)} motor(s) ({args.baseline_s}s)")
    rig.enable()
    rig.ramp_to({idx: args.baseline_rpm for idx in motors})
    time.sleep(args.baseline_s)

    marker(f"SWEEP {args.sweep_lo}->{args.sweep_hi} RPM ({args.sweep_s}s)")
    steps = max(1, int(args.sweep_s / args.sweep_step_s))
    for i in range(steps + 1):
        rpm = args.sweep_lo + (args.sweep_hi - args.sweep_lo) * i / steps
        rig.ramp_to({idx: rpm for idx in motors})
        time.sleep(args.sweep_step_s)

    # An RPM step on ONE motor = an obvious anomaly / imbalance-like
    # signature, with the rest held as a control. On a single-motor rig there
    # is nothing to hold, and the step alone is still the fault.
    faulted = motors[0]
    held = [idx for idx in motors if idx != faulted]
    marker(f"FAULT: motor {faulted} -> {args.fault_rpm} RPM"
            + (f", motors {held} held" if held else "")
            + f" ({args.fault_s}s)")
    targets = {idx: args.baseline_rpm for idx in held}
    targets[faulted] = args.fault_rpm
    rig.ramp_to(targets)
    time.sleep(args.fault_s)

    marker("DONE (ramping to a stop)")
    rig.ramp_to({idx: 0.0 for idx in motors})
    rig.disable()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default=None, metavar="PORT",
                    help="Serial port for THIS process to hold, e.g. /dev/ttyACM0, "
                         "or 'auto' to detect one. Omit it (the default) and the "
                         "control page owns the port over Web Serial instead, which "
                         "is the only thing that works when the browser and the USB "
                         "device are on Windows and this runs in WSL. Required by "
                         "--profile, which has no browser to drive the motors.")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--baseline-rpm", type=float, default=90.0)
    p.add_argument("--baseline-s", type=float, default=45.0)
    p.add_argument("--sweep-lo", type=float, default=60.0)
    p.add_argument("--sweep-hi", type=float, default=180.0)
    p.add_argument("--sweep-s", type=float, default=30.0)
    p.add_argument("--sweep-step-s", type=float, default=1.0)
    p.add_argument("--fault-rpm", type=float, default=220.0)
    p.add_argument("--fault-s", type=float, default=30.0)

    p.add_argument("--mqtt-host", default="epm-base.local",
                    help="Base station's broker address (default: epm-base.local, the "
                         "base station's LAN hostname). Listens for machinery-"
                         "protection trips and stops the named motor when one arrives "
                         "-- one-way: trips only, never speeds. Pass an empty string "
                         "(--mqtt-host '') to run the rig completely standalone, with "
                         "no broker connection at all.")
    p.add_argument("--mqtt-port", type=int, default=None,
                    help=f"Broker port. Default: {ADB_FORWARDED_MQTT_PORT} when "
                         f"something is listening there (the `adb forward "
                         f"tcp:{ADB_FORWARDED_MQTT_PORT} tcp:{DEFAULT_MQTT_PORT}` "
                         f"case), otherwise {DEFAULT_MQTT_PORT}.")
    p.add_argument("--trip-node-id", default="motor_rig",
                    help="This rig host's MQTT identity, used to announce which "
                         "outputs it has (epm/<id>/outputs); must match the base "
                         "station's --trip-host-node-id. Trips themselves are now "
                         "received on every asset's topic and routed by the "
                         "motor index in the payload, so this no longer decides "
                         "which trips arrive.")
    p.add_argument("--motors", default="1",
                    help="Comma-separated motor indices installed on this rig at "
                         "startup (default: 1). This is both what the control page "
                         "shows and what the base station is offered as trip "
                         "outputs. Defaults to one motor because that is where a "
                         "real floor starts; add the rest on the control page, "
                         "which re-announces live. Does not change which motors "
                         "the rig is wired for (see MOTOR_IDS).")
    p.add_argument("--http-port", type=int, default=8000,
                    help="Port for the control page and its JSON API "
                         "(default: 8000). Bound to localhost only.")
    p.add_argument("--no-control-page", action="store_true",
                    help="Don't serve the control page. Leaves a headless rig "
                         "host: the trip listener and --profile still work, but "
                         "the installed-motor set can then only be changed with "
                         "--motors and a restart.")
    p.add_argument("--base-station-url", default=None,
                    help="Where the base station's dashboard is, so the control "
                         "page can show which motors it has claimed as trip "
                         "outputs. Defaults to http://<mqtt-host>:8080.")
    p.add_argument("--profile", action="store_true",
                    help="Run the scripted capture profile (baseline -> sweep -> "
                         "injected fault) and exit, instead of idling. Without it "
                         "this process just holds the port open with the trip "
                         "listener armed and the control page served, and the "
                         "motors are driven from that page.")
    args = p.parse_args()

    try:
        # Validated before anything opens the serial port, and before any
        # announce: an offered output the rig would then refuse is the one
        # mapping that looks armed and does nothing.
        outputs = OutputSet([])
        outputs.set(int(part) for part in args.motors.split(",") if part.strip())
    except ValueError as e:
        p.error(f"--motors: {e}")

    mqtt_port = args.mqtt_port
    if mqtt_port is None and args.mqtt_host:
        mqtt_port = default_mqtt_port(args.mqtt_host)

    base_station_url = args.base_station_url
    if base_station_url is None and args.mqtt_host:
        base_station_url = f"http://{args.mqtt_host}:8080"

    # A scripted profile drives the motors from here, so it needs the port. In
    # every other mode the control page drives them, and taking the port here
    # would take it away from the browser rather than share it.
    port = args.port
    if port is None and args.profile:
        port = "auto"
    if port == "auto":
        try:
            port = find_uno_port()
        except RuntimeError as e:
            p.error(str(e))
        marker(f"USING {port} (autodetected)")

    rig = Rig(port, args.baud)
    if not rig.has_serial:
        marker("NO SERIAL PORT here -- the control page drives the motors "
                "(pass --port to hold it from this process instead)")
    trip = None
    control = None
    # The trip listener comes up before the control page: the page's first
    # poll should see a rig that is already protected, not one that arms a
    # moment later.
    if args.mqtt_host:
        trip = TripListener(rig, args.mqtt_host, mqtt_port, args.trip_node_id,
                             outputs=outputs)
        trip.start()
    else:
        marker(f"STANDALONE (--mqtt-host '') -- motors "
                f"{list(outputs.get())} installed, nothing announced to any "
                f"base station")
    if not args.no_control_page:
        control = ControlServer(rig, outputs, args.http_port,
                                 directory=os.path.dirname(os.path.abspath(__file__)),
                                 base_station_url=base_station_url)
        control.start()
    try:
        if args.profile:
            run_profile(rig, outputs.get(), args)
        else:
            marker("IDLE -- drive the motors from the control page. Ctrl-C to stop.")
            while True:
                time.sleep(1.0)
    except KeyboardInterrupt:
        marker("INTERRUPTED (disabling drivers)")
    finally:
        if control is not None:
            control.stop()
        if trip is not None:
            trip.stop()
        rig.close()


if __name__ == "__main__":
    main()
