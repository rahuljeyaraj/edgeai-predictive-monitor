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
import math
import sys
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial not installed. Run: pip install pyserial")

MOTOR_IDS = (1, 2, 3)
RAMP_RPM_S = 150.0   # matches the firmware's old proven-safe ramp rate
RAMP_TICK_S = 0.1


def marker(label: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] === {label} ===", flush=True)


class Rig:
    def __init__(self, port: str, baud: int = 115200):
        # 2 s settle: opening the port resets the Uno.
        self.ser = serial.Serial(port, baud, timeout=1)
        time.sleep(2.0)
        self._drain()
        self.rpm = {idx: 0.0 for idx in MOTOR_IDS}  # last RPM sent per motor

    def _drain(self) -> None:
        while self.ser.in_waiting:
            self.ser.readline()

    def send(self, cmd: str) -> None:
        self.ser.write((cmd.strip() + "\n").encode())
        self.ser.flush()

    def motor(self, idx: int, rpm: float) -> None:
        self.send(f"{idx} {rpm:.1f}")
        self.rpm[idx] = rpm

    def ramp_to(self, targets: dict, rate: float = RAMP_RPM_S, tick: float = RAMP_TICK_S) -> None:
        """Ramp the given {motor_id: target_rpm} from their last-sent RPM,
        blocking until every motor in `targets` has arrived."""
        max_delta = rate * tick
        while True:
            done = True
            for idx, target in targets.items():
                cur = self.rpm[idx]
                if abs(target - cur) > 0.05:
                    done = False
                    step = math.copysign(min(abs(target - cur), max_delta), target - cur)
                    self.motor(idx, cur + step)
            if done:
                break
            time.sleep(tick)
        for idx, target in targets.items():  # snap exactly to the target
            self.motor(idx, target)

    def enable(self) -> None:
        self.send("e")

    def disable(self) -> None:
        # Zero every motor before cutting power, never just "d". The firmware
        # keeps each motor's last commanded speed, and the shield's single
        # shared ~ENABLE means the next "e" re-applies all of them at once --
        # so a disable-without-zero leaves the whole rig armed to jump back to
        # speed the instant anything re-enables it.
        for idx in MOTOR_IDS:
            self.motor(idx, 0)
        self.send("d")

    def close(self) -> None:
        try:
            self.disable()
        finally:
            self.ser.close()


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
