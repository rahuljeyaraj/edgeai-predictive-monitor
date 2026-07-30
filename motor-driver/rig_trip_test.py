#!/usr/bin/env python3
"""
Rig.stop_motor()/clear_trip() -- the rig-host half of the machinery-protection
trip (docs/MOTOR_STOP_PLAN.md).

Asserts the exact serial bytes, because the hardware makes the obvious
implementation wrong in two ways:

  - The CNC shield has ONE shared ~ENABLE, so "d" cuts all three motors. A
    per-motor trip must be "<idx> 0", and "d" only once everything is at zero.
  - The firmware remembers each motor's last commanded RPM, so anything that
    keeps commanding a tripped motor (run_profile's scripted ramps,
    dashboard.html's ramp tick) would spin it straight back up.

No hardware and no pyserial needed: `serial` is stubbed before importing
run_demo, so this runs anywhere.

Run:
    python3 motor-driver/rig_trip_test.py
"""
import os
import sys
import threading
import types


class FakeSerial:
    """Records every line written, and is deliberately slow so the
    concurrency test below can actually interleave two writers if the lock
    isn't doing its job."""

    def __init__(self, *args, **kwargs):
        self.lines = []
        self.in_waiting = 0
        self.closed = False
        self._partial = ""
        self.slow = False

    def write(self, data: bytes) -> int:
        text = data.decode()
        if self.slow:
            # Split the write in two with a yield in the middle: an unguarded
            # second writer will land between the halves and corrupt the line.
            mid = max(1, len(text) // 2)
            self._partial += text[:mid]
            os.sched_yield() if hasattr(os, "sched_yield") else None
            self._partial += text[mid:]
        else:
            self._partial += text
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            self.lines.append(line)
        return len(data)

    def flush(self) -> None:
        pass

    def readline(self) -> bytes:
        return b""

    def close(self) -> None:
        self.closed = True


# Stub pyserial and the 2s boot settle before importing run_demo.
_fake_serial_module = types.ModuleType("serial")
_fake_serial_module.Serial = FakeSerial
sys.modules["serial"] = _fake_serial_module

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import time as _time
_real_sleep = _time.sleep
_time.sleep = lambda s: None
import run_demo  # noqa: E402
_time.sleep = _real_sleep

MOTOR_IDS = run_demo.MOTOR_IDS


def build_rig():
    rig = run_demo.Rig("/dev/fake")
    rig.ser.lines.clear()
    return rig


def test_trip_stops_one_motor_without_cutting_the_others():
    rig = build_rig()
    for idx in MOTOR_IDS:
        rig.motor(idx, 120.0)
    rig.ser.lines.clear()

    rig.stop_motor(2)

    # Exactly one command, for motor 2 only, and NO "d" -- "d" would kill all
    # three via the shared ~ENABLE.
    assert rig.ser.lines == ["2 0.0"], rig.ser.lines
    assert rig.rpm[1] == 120.0 and rig.rpm[3] == 120.0, rig.rpm
    print("a trip stops only the named motor, never sends 'd' while others run: PASS")


def test_d_is_sent_once_every_motor_is_at_zero():
    rig = build_rig()
    rig.motor(1, 100.0)
    rig.ser.lines.clear()

    rig.stop_motor(1)

    # Now that nothing is turning, cutting driver power is safe and desirable.
    assert rig.ser.lines == ["1 0.0", "d"], rig.ser.lines
    print("'d' follows once the last running motor is stopped: PASS")


def test_tripped_motor_refuses_to_spin_back_up():
    """The bug this exists to prevent: the scripted profile (or the
    dashboard's ramp tick) commanding a tripped motor back to speed a moment
    after the trip landed."""
    rig = build_rig()
    for idx in MOTOR_IDS:
        rig.motor(idx, 120.0)
    rig.stop_motor(2)
    rig.ser.lines.clear()

    rig.motor(2, 150.0)
    rig.ramp_to({1: 120.0, 2: 200.0, 3: 120.0})

    assert all(not line.startswith("2 ") or line == "2 0.0" for line in rig.ser.lines), \
        rig.ser.lines
    assert rig.rpm[2] == 0.0, rig.rpm
    # The other two are still fully commandable -- a trip is per-motor.
    assert rig.rpm[1] == 120.0 and rig.rpm[3] == 120.0, rig.rpm
    print("a tripped motor ignores speed commands; its neighbours don't: PASS")


def test_only_an_explicit_clear_re_arms_a_tripped_motor():
    rig = build_rig()
    rig.motor(1, 90.0)
    rig.stop_motor(1)
    rig.motor(1, 90.0)
    assert rig.rpm[1] == 0.0, rig.rpm

    rig.clear_trip(1)
    rig.motor(1, 90.0)
    assert rig.rpm[1] == 90.0, rig.rpm
    print("clear_trip() is the only way back to a commandable motor: PASS")


def test_disable_always_zeroes_even_a_tripped_motor():
    """Zero is the one command a tripped motor must never refuse, or close()
    would leave the firmware holding a stale RPM -- the exact bug fixed in
    38d39a7."""
    rig = build_rig()
    for idx in MOTOR_IDS:
        rig.motor(idx, 120.0)
    rig.stop_motor(2)
    rig.ser.lines.clear()

    rig.disable()

    for idx in MOTOR_IDS:
        assert f"{idx} 0.0" in rig.ser.lines, (idx, rig.ser.lines)
    assert rig.ser.lines[-1] == "d", rig.ser.lines
    assert all(v == 0.0 for v in rig.rpm.values()), rig.rpm
    print("disable() zeroes every motor including a tripped one, then cuts power: PASS")


def test_concurrent_writers_never_interleave_a_line():
    """A trip arrives on the paho thread while the main thread is ramping.
    Without Rig's lock the two writes interleave mid-line and the firmware's
    single-char parser reads garbage."""
    rig = build_rig()
    rig.ser.slow = True

    def ramp():
        for _ in range(200):
            rig.motor(1, 111.0)

    def trip():
        for _ in range(200):
            rig.motor(3, 333.0)

    threads = [threading.Thread(target=ramp), threading.Thread(target=trip)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert rig.ser.lines, "no lines recorded"
    allowed = {"1 111.0", "3 333.0"}
    corrupt = [line for line in rig.ser.lines if line not in allowed]
    assert not corrupt, f"interleaved/corrupt lines: {corrupt[:5]}"
    print(f"{len(rig.ser.lines)} concurrent writes, zero corrupted lines: PASS")


def test_listener_decodes_the_base_stations_bytes():
    """End-to-end on the wire format, against the byte sequence
    base-station/tests/wire_protocol_test.py pins down."""
    rig = build_rig()
    for idx in MOTOR_IDS:
        rig.motor(idx, 120.0)
    rig.ser.lines.clear()

    # TripListener.__init__ needs paho, so exercise its decode path directly
    # with the same bytes the base station publishes for "stop motor 2".
    message = bytes([run_demo.MQTT_MSG_TYPE_MOTOR_STOP, 2])
    listener = run_demo.TripListener.__new__(run_demo.TripListener)
    listener._rig = rig
    fake_message = types.SimpleNamespace(payload=message)
    listener._on_message(None, None, fake_message)

    assert rig.ser.lines == ["2 0.0"], rig.ser.lines
    assert rig.rpm[2] == 0.0, rig.rpm
    print("the listener turns the base station's 2 bytes into a real stop: PASS")


def test_listener_ignores_other_command_types_and_bad_indexes():
    rig = build_rig()
    for idx in MOTOR_IDS:
        rig.motor(idx, 120.0)
    rig.ser.lines.clear()

    listener = run_demo.TripListener.__new__(run_demo.TripListener)
    listener._rig = rig
    for payload in (b"",                       # empty
                     b"\x09",                   # truncated, no motor index
                     bytes([0x08, 2]),          # STATUS_LED, not a trip
                     bytes([0x09, 9])):         # motor not on this rig
        listener._on_message(None, None, types.SimpleNamespace(payload=payload))

    assert rig.ser.lines == [], rig.ser.lines
    assert all(v == 120.0 for v in rig.rpm.values()), rig.rpm
    print("malformed messages and other command types are ignored safely: PASS")


if __name__ == "__main__":
    try:
        test_trip_stops_one_motor_without_cutting_the_others()
        test_d_is_sent_once_every_motor_is_at_zero()
        test_tripped_motor_refuses_to_spin_back_up()
        test_only_an_explicit_clear_re_arms_a_tripped_motor()
        test_disable_always_zeroes_even_a_tripped_motor()
        test_concurrent_writers_never_interleave_a_line()
        test_listener_decodes_the_base_stations_bytes()
        test_listener_ignores_other_command_types_and_bad_indexes()
        print("RESULT: PASS - per-motor trip, sticky until cleared, thread-safe writes")
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
