#!/usr/bin/env python3
"""Script a repeatable demo run for the stepper vibration rig.

Sends serial commands to stepper_rig.ino to produce labeled phases:

    baseline -> speed sweep -> injected fault

Each phase prints a start marker (with a wall-clock timestamp) so you can align
the captured vibration stream with the commanded state.

Requires pyserial:  pip install pyserial

Examples:
    python3 run_demo.py --port /dev/ttyACM0
    python3 run_demo.py --port COM5 --baseline-rpm 90 --sweep-hi 180
"""

import argparse
import sys
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial not installed. Run: pip install pyserial")


def marker(label: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] === {label} ===", flush=True)


class Rig:
    def __init__(self, port: str, baud: int = 115200):
        # 2 s settle: opening the port resets the Uno.
        self.ser = serial.Serial(port, baud, timeout=1)
        time.sleep(2.0)
        self._drain()

    def _drain(self) -> None:
        while self.ser.in_waiting:
            self.ser.readline()

    def send(self, cmd: str) -> None:
        self.ser.write((cmd.strip() + "\n").encode())
        self.ser.flush()

    def both(self, rpm: float) -> None:
        self.send(f"b {rpm:.1f}")

    def motor(self, idx: int, rpm: float) -> None:
        self.send(f"{idx} {rpm:.1f}")

    def enable(self) -> None:
        self.send("e")

    def disable(self) -> None:
        self.send("d")

    def close(self) -> None:
        try:
            self.disable()
        finally:
            self.ser.close()


def run_profile(rig: Rig, args) -> None:
    marker(f"BASELINE {args.baseline_rpm} RPM ({args.baseline_s}s)")
    rig.enable()
    rig.both(args.baseline_rpm)
    time.sleep(args.baseline_s)

    marker(f"SWEEP {args.sweep_lo}->{args.sweep_hi} RPM ({args.sweep_s}s)")
    steps = max(1, int(args.sweep_s / args.sweep_step_s))
    for i in range(steps + 1):
        rpm = args.sweep_lo + (args.sweep_hi - args.sweep_lo) * i / steps
        rig.both(rpm)
        time.sleep(args.sweep_step_s)

    marker(f"FAULT: motor1 -> {args.fault_rpm} RPM, motor2 held ({args.fault_s}s)")
    # Sudden RPM step on one motor = obvious anomaly / imbalance-like signature.
    rig.motor(1, args.fault_rpm)
    rig.motor(2, args.baseline_rpm)
    time.sleep(args.fault_s)

    marker("DONE (coasting to stop)")
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
    args = p.parse_args()

    rig = Rig(args.port, args.baud)
    try:
        run_profile(rig, args)
    except KeyboardInterrupt:
        marker("INTERRUPTED (disabling drivers)")
    finally:
        rig.close()


if __name__ == "__main__":
    main()
