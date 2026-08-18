#!/usr/bin/env python3
"""
Covers host/wifi_bridge.py's monitor_loop AP-fallback policy -- the logic
that decides whether wlan0 is "down enough" to justify tearing the radio
away from NetworkManager and hosting the onboarding Hotspot instead.

The regression this exists for (base station appeared to lose its WiFi
credentials on every reboot, 2026-08-18) is test_boot_join_in_flight_is_
not_interrupted below. It is reproduced here as the exact NM device-state
sequence the board's own journal recorded: NM auto-activates the saved
profile ~11s into boot and is sitting in ip-config (state 70) running its
DHCP transaction when the first monitor tick lands.

Runs with no hardware and no NetworkManager -- every _nmcli() call is
stubbed out.

Run with PYTHONPATH covering base-station/host:
    PYTHONPATH=base-station/host python3 base-station/tests/wifi_bridge_monitor_test.py
"""
import sys

import wifi_bridge


class _StopLoop(Exception):
    """Breaks monitor_loop's `while True` once the scripted ticks run out."""


class Harness:
    """Drives monitor_loop through a scripted list of device states.

    Each entry is (state_code, connection) exactly as _device_state()
    would report it. time.sleep is replaced with the tick advance, so the
    loop runs at full speed and stops deterministically.
    """

    def __init__(self, states, saved_profiles=("FTTH-F05C",), monkeypatch_time=True):
        self._states = list(states)
        self._saved = list(saved_profiles)
        self.hotspot_ups = 0
        self.rejoin_attempts = 0
        self._tick = 0
        self._clock = 0.0
        self._monkeypatch_time = monkeypatch_time

    def install(self):
        self._orig = {name: getattr(wifi_bridge, name)
                      for name in ("_device_state", "_saved_sta_profiles", "ensure_hotspot_up",
                                    "try_saved_network", "_portal_client_connected", "time")}
        wifi_bridge._device_state = self._device_state
        wifi_bridge._saved_sta_profiles = lambda: list(self._saved)
        wifi_bridge.ensure_hotspot_up = self._ensure_hotspot_up
        wifi_bridge.try_saved_network = self._try_saved_network
        wifi_bridge._portal_client_connected = lambda: False
        if self._monkeypatch_time:
            wifi_bridge.time = self

    def restore(self):
        for name, value in self._orig.items():
            setattr(wifi_bridge, name, value)

    # --- stand-in for the `time` module inside wifi_bridge -------------
    def time(self):
        return self._clock

    def sleep(self, seconds):
        self._clock += seconds
        if self._tick >= len(self._states):
            raise _StopLoop

    def _device_state(self):
        state = self._states[min(self._tick, len(self._states) - 1)]
        self._tick += 1
        return state[0], state[1], None

    def _ensure_hotspot_up(self):
        self.hotspot_ups += 1

    def _try_saved_network(self):
        self.rejoin_attempts += 1
        return False

    def run(self):
        self.install()
        try:
            wifi_bridge.monitor_loop()
        except _StopLoop:
            pass
        finally:
            self.restore()
        return self


# NM device states used below: 20 unavailable, 30 disconnected, 70 ip-config,
# 100 activated. See wifi_bridge._NM_ACTIVATING_STATES.
def test_boot_join_in_flight_is_not_interrupted():
    """THE regression. NM is mid-activation on the saved network when the
    monitor ticks; forcing the Hotspot up here is what killed the join
    mid-DHCP and stranded the board in AP mode looking like it had
    forgotten its password."""
    harness = Harness([
        (20, None),    # wlan0 not up yet, moments after boot
        (70, None),    # NM auto-activating the saved profile, DHCP in flight
        (70, None),
        (100, "FTTH-F05C"),  # joined
        (100, "FTTH-F05C"),
    ]).run()
    assert harness.hotspot_ups == 0, f"hotspot forced up {harness.hotspot_ups}x during a live join"
    print("an in-flight join at boot is never interrupted: PASS")


def test_sustained_down_still_falls_back_to_ap():
    """The fallback must still happen -- a technician's only way in when
    the saved network really is gone. BOOT_GRACE_S has to elapse first,
    hence the long run of down ticks."""
    ticks = int(wifi_bridge.BOOT_GRACE_S / wifi_bridge.MONITOR_INTERVAL_S) + \
        wifi_bridge.DOWN_TICKS_BEFORE_AP + 2
    harness = Harness([(30, None)] * ticks).run()
    assert harness.hotspot_ups >= 1, "hotspot never came up despite a sustained outage"
    print("a sustained outage still falls back to AP mode: PASS")


def test_brief_dropout_does_not_fall_back():
    """A single unlucky sample (a roam, a DHCP renew) must not cost the
    network -- DOWN_TICKS_BEFORE_AP consecutive down ticks are required."""
    harness = Harness([
        (100, "FTTH-F05C"),
        (30, None),           # one blip
        (100, "FTTH-F05C"),
        (100, "FTTH-F05C"),
    ]).run()
    assert harness.hotspot_ups == 0, "a single down sample triggered AP fallback"
    print("a momentary dropout does not trigger fallback: PASS")


def test_fresh_board_gets_its_portal_immediately():
    """No saved credentials means nothing to protect: no grace period, no
    debounce -- a technician should see EPM-BaseStation on the first tick."""
    harness = Harness([(30, None), (30, None)], saved_profiles=()).run()
    assert harness.hotspot_ups >= 1, "a fresh board did not raise its portal promptly"
    print("a board with no credentials raises its portal at once: PASS")


def test_ap_fallback_retries_the_saved_network():
    """AP fallback must not be a one-way door: with credentials saved, the
    loop periodically drops the Hotspot and retries the real network (e.g.
    after a power cut where the board booted before the router did)."""
    ticks = int(wifi_bridge.AP_RETRY_INTERVAL_S / wifi_bridge.MONITOR_INTERVAL_S) + 3
    harness = Harness([(100, "Hotspot")] * ticks).run()
    assert harness.rejoin_attempts >= 1, "parked in AP mode with saved credentials, never retried"
    print("AP fallback periodically retries the saved network: PASS")


def test_ap_fallback_holds_off_while_a_technician_is_on_the_portal():
    ticks = int(wifi_bridge.AP_RETRY_INTERVAL_S / wifi_bridge.MONITOR_INTERVAL_S) + 3
    harness = Harness([(100, "Hotspot")] * ticks)
    harness.install()
    wifi_bridge._portal_client_connected = lambda: True
    try:
        wifi_bridge.monitor_loop()
    except _StopLoop:
        pass
    finally:
        harness.restore()
    assert harness.rejoin_attempts == 0, "yanked the AP out from under a connected client"
    print("the retry holds off while a portal client is connected: PASS")


def main():
    test_boot_join_in_flight_is_not_interrupted()
    test_sustained_down_still_falls_back_to_ap()
    test_brief_dropout_does_not_fall_back()
    test_fresh_board_gets_its_portal_immediately()
    test_ap_fallback_retries_the_saved_network()
    test_ap_fallback_holds_off_while_a_technician_is_on_the_portal()
    print("\nwifi_bridge monitor policy: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
