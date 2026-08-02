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
motor_driver, so this runs anywhere.

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


# Stub pyserial and the 2s boot settle before importing motor_driver.
_fake_serial_module = types.ModuleType("serial")
_fake_serial_module.Serial = FakeSerial
sys.modules["serial"] = _fake_serial_module

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import time as _time
_real_sleep = _time.sleep
_time.sleep = lambda s: None
import motor_driver  # noqa: E402
_time.sleep = _real_sleep

MOTOR_IDS = motor_driver.MOTOR_IDS


def build_rig():
    rig = motor_driver.Rig("/dev/fake")
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
    message = bytes([motor_driver.MQTT_MSG_TYPE_MOTOR_STOP, 2])
    listener = motor_driver.TripListener.__new__(motor_driver.TripListener)
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

    listener = motor_driver.TripListener.__new__(motor_driver.TripListener)
    listener._rig = rig
    for payload in (b"",                       # empty
                     b"\x09",                   # truncated, no motor index
                     bytes([0x08, 2]),          # STATUS_LED, not a trip
                     bytes([0x09, 9])):         # motor not on this rig
        listener._on_message(None, None, types.SimpleNamespace(payload=payload))

    assert rig.ser.lines == [], rig.ser.lines
    assert all(v == 120.0 for v in rig.rpm.values()), rig.rpm
    print("malformed messages and other command types are ignored safely: PASS")


# --- Port autodetect: the one place a wrong guess is expensive -------------

def _port(device, vid=None, pid=None, product=None):
    return types.SimpleNamespace(device=device, vid=vid, pid=pid, product=product,
                                  description=product or "n/a")


def _with_ports(ports):
    """Point motor_driver.find_uno_port() at a fake set of attached devices."""
    tools = types.ModuleType("serial.tools")
    lp = types.ModuleType("serial.tools.list_ports")
    lp.comports = lambda: list(ports)
    tools.list_ports = lp
    sys.modules["serial.tools"] = tools
    sys.modules["serial.tools.list_ports"] = lp


UNO = _port("/dev/ttyACM1", 0x2341, 0x0043, "Arduino Uno")
UNO_Q = _port("/dev/ttyACM0", 0x2341, 0x0078, "UNO Q - epm-base")
LEGACY = _port("/dev/ttyS0")


def test_autodetect_finds_a_lone_uno():
    _with_ports([LEGACY, UNO])
    assert motor_driver.find_uno_port() == "/dev/ttyACM1"
    print("autodetect picks the one Uno, ignoring legacy ttyS ports: PASS")


def test_autodetect_never_grabs_the_uno_q_base_station():
    """The expensive mistake this guards. The UNO Q is also an Arduino-VID CDC
    device on /dev/ttyACM*, so "first Arduino wins" would open the monitoring
    board's port and write stepper commands into it."""
    _with_ports([UNO_Q])
    try:
        motor_driver.find_uno_port()
    except RuntimeError as e:
        assert "UNO Q" in str(e), e          # it names what it saw
        assert "--port" in str(e), e         # and what to do about it
    else:
        raise AssertionError("autodetect selected the UNO Q base station")

    # And with the real rig alongside it, the rig is still the answer.
    _with_ports([UNO_Q, UNO])
    assert motor_driver.find_uno_port() == "/dev/ttyACM1"
    print("autodetect refuses the UNO Q, and still finds the rig beside it: PASS")


def test_autodetect_refuses_to_guess_between_two_rigs():
    _with_ports([UNO, _port("/dev/ttyACM2", 0x1A86, 0x7523, "USB Serial")])
    try:
        motor_driver.find_uno_port()
    except RuntimeError as e:
        assert "--port" in str(e), e
    else:
        raise AssertionError("autodetect guessed between two Uno-like boards")
    print("autodetect asks rather than guesses when two boards match: PASS")


def test_tripped_is_visible_to_the_control_page():
    """The control page can only show a trip if it can see one. Before
    Rig.tripped() existed, a trip was a line in this script's terminal and
    nothing else."""
    rig = build_rig()
    for idx in MOTOR_IDS:
        rig.motor(idx, 120.0)
    assert rig.tripped() == []

    rig.stop_motor(3)
    rig.stop_motor(1)
    assert rig.tripped() == [1, 3], rig.tripped()

    rig.clear_trip(1)
    assert rig.tripped() == [3], rig.tripped()
    print("Rig.tripped() reports exactly the motors held stopped: PASS")


# --- Broker port default: the adb-forward case ------------------------------

def test_broker_port_prefers_the_adb_forward_when_it_is_live():
    """11883 vs 1883 is the difference between announcing and silently not,
    and nobody should have to remember which. Picking it by what's actually
    listening keeps the no-argument form correct in both deployments."""
    import socket
    real = socket.create_connection

    def fake(addr, timeout=None):
        host, port = addr
        if port == motor_driver.ADB_FORWARDED_MQTT_PORT and listening:
            return real(("127.0.0.1", server.getsockname()[1]))
        raise OSError("refused")

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    socket.create_connection = fake
    try:
        listening = True
        assert motor_driver.default_mqtt_port("localhost") == 11883
        listening = False
        assert motor_driver.default_mqtt_port("localhost") == 1883
        # A real remote broker is never behind an adb forward, so don't probe.
        listening = True
        assert motor_driver.default_mqtt_port("uno-q.local") == 1883
    finally:
        socket.create_connection = real
        server.close()
    print("broker port picks 11883 only when the adb forward is live: PASS")


# --- No serial port here: the control page holds it ------------------------

def test_a_portless_rig_still_tracks_trips_and_writes_nothing():
    """The default mode. The browser and the USB device are on Windows while
    this runs in WSL, so this process cannot open the port at all -- but the
    trip listener, the announce and the control server must all still work,
    and the page applies the stop."""
    rig = motor_driver.Rig()
    assert rig.has_serial is False
    assert rig.ser is None

    rig.motor(1, 120.0)
    rig.stop_motor(1)
    assert rig.tripped() == [1], rig.tripped()
    assert rig.rpm[1] == 0.0, rig.rpm
    # Still sticky: the page must not be able to ramp it back up either.
    rig.motor(1, 200.0)
    assert rig.rpm[1] == 0.0, rig.rpm
    rig.close()                      # must not blow up with no port
    print("a rig with no serial port tracks trips and never writes: PASS")


def test_state_tells_the_page_whether_it_must_apply_the_stop():
    """The one bit the page can't infer. Told it wrongly, the page either
    double-sends a stop or -- much worse -- assumes one already happened."""
    with_port = _fake_control_server(build_rig(), motor_driver.OutputSet([1]))
    without = _fake_control_server(motor_driver.Rig(), motor_driver.OutputSet([1]))
    assert with_port.state()["rig_serial"] is True, with_port.state()
    assert without.state()["rig_serial"] is False, without.state()
    print("/api/state says who holds the serial port: PASS")


# --- OutputSet: what the rig OFFERS, editable at runtime -------------------

def test_output_set_rejects_motors_the_rig_does_not_have():
    outputs = motor_driver.OutputSet([1])
    try:
        outputs.set([1, 9])
    except ValueError as e:
        assert "9" in str(e), e
    else:
        raise AssertionError("a motor not on the rig was accepted as an output")
    # And the set is unchanged -- a rejected edit must not half-apply.
    assert outputs.get() == (1,), outputs.get()
    print("OutputSet refuses an output the rig would then refuse to stop: PASS")


def test_output_set_normalises_and_notifies_on_change():
    outputs = motor_driver.OutputSet([1])
    seen = []
    outputs.subscribe(seen.append)

    outputs.set([3, 1, 1])            # unsorted, duplicated
    assert outputs.get() == (1, 3), outputs.get()
    assert seen == [(1, 3)], seen

    # An empty set is a legitimate announce ("this rig offers nothing"), not a
    # no-op: it is what clears a stale retained announce off the broker.
    outputs.set([])
    assert outputs.get() == (), outputs.get()
    assert seen[-1] == (), seen
    print("OutputSet sorts, dedupes, and notifies the announce on change: PASS")


# --- Control server: the browser -> rig host -> MQTT hop --------------------

def _fake_control_server(rig, outputs):
    """A ControlServer without its HTTP socket. Binding a real port in a unit
    test would make this depend on a free port and a working loopback; the
    request handlers are thin wrappers over these two methods anyway."""
    server = motor_driver.ControlServer.__new__(motor_driver.ControlServer)
    server._rig = rig
    server._outputs = outputs
    server._base_station_url = "http://base:8080"
    return server


def test_control_server_reports_state_the_page_renders_from():
    rig = build_rig()
    rig.motor(1, 100.0)
    rig.stop_motor(1)
    server = _fake_control_server(rig, motor_driver.OutputSet([1]))

    state = server.state()
    assert state["motor_ids"] == list(MOTOR_IDS), state
    assert state["installed"] == [1], state
    assert state["tripped"] == [1], state
    assert state["base_station_url"] == "http://base:8080", state
    print("/api/state carries installed motors, trips and the base station URL: PASS")


def test_installing_a_motor_reannounces_immediately():
    """The point of the whole hop: adding a motor on the control page has to
    reach the base station, not just draw a card."""
    rig = build_rig()
    outputs = motor_driver.OutputSet([1])
    announced = []
    outputs.subscribe(announced.append)
    server = _fake_control_server(rig, outputs)

    state = server._set_installed({"installed": [1, 2]})

    assert state["installed"] == [1, 2], state
    assert announced == [(1, 2)], announced
    print("adding a motor on the page re-announces the trip outputs: PASS")


def test_control_server_refuses_a_motor_the_rig_does_not_have():
    server = _fake_control_server(build_rig(), motor_driver.OutputSet([1]))
    for body in ({"installed": [1, 7]}, {"installed": ["x"]}):
        try:
            server._set_installed(body)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted a bad installed set: {body}")
    print("the control server rejects installed sets the rig can't honour: PASS")


def test_clearing_a_trip_is_the_only_thing_the_page_can_do_to_one():
    """The page can clear a trip (a human is standing at the rig) but there is
    no endpoint that can cause one, and none that can start a motor."""
    rig = build_rig()
    rig.motor(2, 120.0)
    rig.stop_motor(2)
    server = _fake_control_server(rig, motor_driver.OutputSet([1, 2]))

    state = server._clear_trip({"idx": 2})
    assert state["tripped"] == [], state
    rig.motor(2, 120.0)
    assert rig.rpm[2] == 120.0, rig.rpm

    try:
        server._clear_trip({"idx": 9})
    except ValueError:
        pass
    else:
        raise AssertionError("cleared a trip on a motor that isn't on the rig")
    print("the page can clear a trip and do nothing else to one: PASS")


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
        test_tripped_is_visible_to_the_control_page()
        test_autodetect_finds_a_lone_uno()
        test_autodetect_never_grabs_the_uno_q_base_station()
        test_autodetect_refuses_to_guess_between_two_rigs()
        test_broker_port_prefers_the_adb_forward_when_it_is_live()
        test_a_portless_rig_still_tracks_trips_and_writes_nothing()
        test_state_tells_the_page_whether_it_must_apply_the_stop()
        test_output_set_rejects_motors_the_rig_does_not_have()
        test_output_set_normalises_and_notifies_on_change()
        test_control_server_reports_state_the_page_renders_from()
        test_installing_a_motor_reannounces_immediately()
        test_control_server_refuses_a_motor_the_rig_does_not_have()
        test_clearing_a_trip_is_the_only_thing_the_page_can_do_to_one()
        print("RESULT: PASS - per-motor trip, sticky until cleared, thread-safe "
               "writes, runtime-editable outputs")
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
