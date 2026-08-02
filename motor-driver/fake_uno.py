#!/usr/bin/env python3
"""Run the rig host against a fake Uno, so the control page can be clicked
through with no hardware at all.

Opens a pseudo-terminal, swallows everything written to it (the firmware
answers speed commands with silence anyway -- see src/main.cpp), and starts
motor_driver.py pointed at it. Everything except the motors themselves is
real: the same ControlServer, the same page, the same MQTT trip listener if
you pass --mqtt-host.

    python3 fake_uno.py                       # then open http://localhost:8000/
    python3 fake_uno.py --trip-after 20       # ...and watch a trip land at 20s
    python3 fake_uno.py --mqtt-host <uno-q>   # against a real base station

Every other argument is passed through to motor_driver.py.
"""
import argparse
import os
import pty
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def drain(fd):
    """Read and discard whatever the rig host writes. Without this the pty's
    buffer fills and the rig host blocks on write -- which would look exactly
    like a hung serial port."""
    while True:
        try:
            if not os.read(fd, 4096):
                return
        except OSError:
            return


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--trip-after", type=float, default=None, metavar="SECONDS",
                    help="Publish a fake trip for --trip-motor this many seconds "
                         "from now, to exercise the tripped card and Reset without "
                         "a base station. Needs --mqtt-host.")
    p.add_argument("--trip-motor", type=int, default=1,
                    help="Which motor the fake trip stops (default: 1).")
    args, passthrough = p.parse_known_args()

    if args.trip_after is not None and "--mqtt-host" not in passthrough:
        p.error("--trip-after needs --mqtt-host (the trip travels over MQTT)")

    controller, follower = pty.openpty()
    port = os.ttyname(follower)
    threading.Thread(target=drain, args=(controller,), daemon=True).start()

    python = os.path.join(HERE, ".venv", "bin", "python")
    if not os.path.exists(python):
        python = sys.executable
    cmd = [python, os.path.join(HERE, "motor_driver.py"), "--port", port] + passthrough
    print(f"fake Uno on {port}")
    child = subprocess.Popen(cmd)

    if args.trip_after is not None:
        threading.Thread(target=_fake_trip, args=(args, passthrough), daemon=True).start()

    try:
        child.wait()
    except KeyboardInterrupt:
        child.send_signal(signal.SIGINT)
        child.wait()


def _fake_trip(args, passthrough):
    """Publish the base station's exact MOTOR_STOP bytes, so the trip arrives
    through the real listener rather than by poking Rig directly."""
    import struct
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("--trip-after needs paho-mqtt: pip install paho-mqtt", file=sys.stderr)
        return
    host = passthrough[passthrough.index("--mqtt-host") + 1]
    port = 1883
    if "--mqtt-port" in passthrough:
        port = int(passthrough[passthrough.index("--mqtt-port") + 1])

    time.sleep(args.trip_after)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(host, port)
    payload = bytes([0x09]) + struct.pack("<B", args.trip_motor)
    client.publish("epm/fake_asset/cmd", payload, qos=1)
    client.disconnect()
    print(f"published a fake trip for motor {args.trip_motor}")


if __name__ == "__main__":
    main()
