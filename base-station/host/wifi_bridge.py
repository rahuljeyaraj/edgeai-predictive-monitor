#!/usr/bin/env python3
"""wifi_bridge.py - root-owned host-side bridge for base station WiFi
onboarding (docs/WIFI_ONBOARDING_PLAN.md S1).

Why this exists: same reason as spi_bridge.py/gpu_bridge.py -- the app's
Python code runs inside a non-privileged Docker container that can't drive
wlan0 directly (no CAP_NET_ADMIN / host network namespace). This daemon
runs as root on the host (via systemd -- see wifi-bridge.service), drives
NetworkManager via nmcli (confirmed already active on this board --
`systemctl is-active NetworkManager`, `nmcli` present -- not hostapd/
dnsmasq as the plan doc's own "open item" had speculated might be needed),
and re-exposes control over a Unix socket at /dev/wifi-link.sock. /dev is
already bind-mounted into the container (same trust-boundary reasoning as
spi_bridge.py/gpu_bridge.py), so this needs no new compose/bind-mount
plumbing.

Two responsibilities:

1. A monitor loop (runs for the daemon's whole life, not just at boot):
   whenever wlan0 isn't genuinely joined to a real network, bring up the
   open "Hotspot" NM connection profile that provision-wifi.sh creates
   once (this daemon only ever activates/deactivates it, never creates
   it) -- so a technician always has a way in, no manual factory reset.
   The same one check covers "no saved credentials yet" (fresh board),
   "join attempt failed", and "was connected, dropped later."

2. A one-request-per-connection JSON socket API (same wire-protocol shape
   as spi_bridge.py/gpu_bridge.py -- one line of JSON in, one line of
   JSON out) for the app container to read current status and submit new
   factory-WiFi credentials:
     {"cmd": "status"}
       -> {"mode": "sta"|"ap"|"disconnected", "ssid": str|null, "ip": str|null}
     {"cmd": "connect", "ssid": str, "password": str}
       -> {"success": bool, "error": str|null}
   A connect attempt blocks this connection for up to CONNECT_TIMEOUT_S
   while nmcli tries the join -- deliberately synchronous (same "blocking
   call, caller's worker thread absorbs it" shape the app side already
   uses for e.g. POST /classifier/ei/link's blocking EI login) since this
   is a rare, one-shot, technician-driven action, not a polled path.

Concurrent AP+STA on this board's WCBN3536A radio is NOT assumed:
activating the factory-network connection while the Hotspot is up is
expected to drop the Hotspot outright (NM switching the same physical
wlan0 between modes) -- an accepted, anticipated fallback per the plan
doc's own "open item," not a bug. NM itself owns credential persistence
(/etc/NetworkManager/system-connections/*.nmconnection) and autoconnect
on subsequent boots -- no app-side credential store needed here.

All state-mutating nmcli calls (ensure_hotspot_up/down, handle_connect)
go through _nm_lock so the monitor loop and an in-flight app-triggered
connect request never race each other.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time

SOCKET_PATH = "/dev/wifi-link.sock"
IFACE = "wlan0"
HOTSPOT_CON_NAME = "Hotspot"
# The Hotspot profile's actual broadcast SSID (provision-wifi.sh creates it
# with this ssid, con-name "Hotspot" -- distinct names on purpose, see
# status_payload()'s comment on why the two must not be conflated).
HOTSPOT_SSID = "EPM-BaseStation"
MONITOR_INTERVAL_S = 10
CONNECT_TIMEOUT_S = 45
# Brief pause after tearing down the Hotspot before attempting the new
# join, letting the radio actually finish switching modes.
RADIO_SETTLE_S = 2
NMCLI_TIMEOUT_S = 10
MAX_REQUEST_BYTES = 4096

_nm_lock = threading.Lock()


def _nmcli(args, timeout=NMCLI_TIMEOUT_S):
    return subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)


def _device_state():
    """Parses `nmcli -t -f GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS
    device show wlan0` terse output (one "FIELD:value" line per field --
    split on the FIRST colon only, since IP4.ADDRESS's value itself
    contains none but this keeps the parse robust either way).

    GENERAL.STATE's value looks like "100 (connected)" -- the leading
    numeric NM device-state code is what's checked (100 == connected),
    never a substring match on the word "connected": "disconnected"
    literally contains "connected" as a substring, which would otherwise
    misreport a disconnected device as connected."""
    result = _nmcli(["-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS", "device", "show", IFACE])
    state_code, connection, ip = None, None, None
    for line in result.stdout.splitlines():
        field, _, value = line.partition(":")
        if field == "GENERAL.STATE":
            try:
                state_code = int(value.split()[0])
            except (IndexError, ValueError):
                state_code = None
        elif field == "GENERAL.CONNECTION":
            connection = None if value in ("", "--") else value
        elif field.startswith("IP4.ADDRESS"):
            ip = value.split("/")[0] or None
    return state_code, connection, ip


def _is_connected_to_real_network(state_code, connection):
    return state_code == 100 and connection is not None and connection != HOTSPOT_CON_NAME


def _hotspot_active(state_code, connection):
    return state_code == 100 and connection == HOTSPOT_CON_NAME


def ensure_hotspot_up():
    with _nm_lock:
        state_code, connection, _ = _device_state()
        # Confirmed on hardware (2026-07-29): checking ONLY "is the Hotspot
        # already active" here let a stale monitor_loop tick force the
        # Hotspot back up right after a successful join -- the tick reads
        # "not connected" (true for an instant mid-transition), then blocks
        # on _nm_lock behind an in-flight handle_connect(); by the time it
        # finally acquires the lock the join has already succeeded, but
        # without this second check it had no way to know that and forced
        # the Hotspot up anyway, undoing the just-completed join. Also
        # bailing out when we're already on a real network closes that race.
        if _hotspot_active(state_code, connection) or _is_connected_to_real_network(state_code, connection):
            return
        _nmcli(["connection", "up", HOTSPOT_CON_NAME, "ifname", IFACE])


def ensure_hotspot_down():
    with _nm_lock:
        state_code, connection, _ = _device_state()
        if not _hotspot_active(state_code, connection):
            return
        _nmcli(["connection", "down", HOTSPOT_CON_NAME])


def status_payload():
    state_code, connection, ip = _device_state()
    if _hotspot_active(state_code, connection):
        # connection is the NM connection PROFILE name ("Hotspot"), not the
        # network a technician's phone actually sees in its WiFi picker --
        # report the real broadcast SSID instead, or a technician reading
        # this in the dashboard would look for a network called "Hotspot"
        # and never find it.
        return {"mode": "ap", "ssid": HOTSPOT_SSID, "ip": ip}
    if _is_connected_to_real_network(state_code, connection):
        return {"mode": "sta", "ssid": connection, "ip": ip}
    return {"mode": "disconnected", "ssid": None, "ip": ip}


def _connect_to_network(ssid, password):
    """Explicit delete/add/modify/up sequence, NOT the `nmcli device wifi
    connect <ssid> password <pw>` shorthand -- confirmed on hardware that
    the shorthand fails with a spurious "802-11-wireless-security.key-
    mgmt: property is missing" on this nmcli version even against a
    completely fresh connection profile with the target network correctly
    visible in-scan (nmcli's own automatic security-type inference from
    scan results, not a scan-timing issue -- setting key-mgmt explicitly
    here sidesteps it entirely). Mirrors provision-wifi.sh's own
    delete-then-add pattern for the Hotspot profile. Deleting first makes
    this idempotent against a stale profile of the same name (e.g.
    reconnecting after a password change)."""
    _nmcli(["connection", "delete", ssid])  # best-effort, fine if absent
    add_result = _nmcli(["connection", "add", "type", "wifi", "con-name", ssid,
                          "ifname", IFACE, "ssid", ssid])
    if add_result.returncode != 0:
        return add_result
    if password:
        _nmcli(["connection", "modify", ssid,
                "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password])
    return _nmcli(["connection", "up", ssid], timeout=CONNECT_TIMEOUT_S)


def handle_connect(ssid, password):
    if not ssid:
        return {"success": False, "error": "SSID is required"}
    with _nm_lock:
        state_code, connection, _ = _device_state()
        if _hotspot_active(state_code, connection):
            # The Hotspot must come down BEFORE attempting the join, not
            # after a failed one (an earlier version of this got this
            # backwards and every join attempt failed outright while in
            # AP fallback -- this radio can't reach another network while
            # busy hosting its own). RADIO_SETTLE_S gives the mode switch
            # itself a moment to finish. If the join below fails,
            # ensure_hotspot_up() brings the Hotspot back.
            _nmcli(["connection", "down", HOTSPOT_CON_NAME])
            time.sleep(RADIO_SETTLE_S)
        try:
            result = _connect_to_network(ssid, password)
        except subprocess.TimeoutExpired:
            result = None
        if result is not None and result.returncode == 0:
            success, error = True, None
        else:
            success = False
            error = (result.stderr.strip() if result and result.stderr.strip()
                      else result.stdout.strip() if result and result.stdout.strip()
                      else "Connection attempt timed out")
    # Outside _nm_lock (ensure_hotspot_up takes it itself) but still
    # logically sequential -- nothing else calls into nmcli between them.
    if not success:
        ensure_hotspot_up()
    return {"success": success, "error": error}


def monitor_loop():
    while True:
        try:
            state_code, connection, _ = _device_state()
            if not _is_connected_to_real_network(state_code, connection):
                ensure_hotspot_up()
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see spi_bridge.py
            print(f"wifi_bridge: monitor tick failed: {exc}", file=sys.stderr, flush=True)
        time.sleep(MONITOR_INTERVAL_S)


def handle_client(conn):
    with conn:
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return
            data += chunk
            if len(data) > MAX_REQUEST_BYTES:
                return
        line = data.split(b"\n", 1)[0]
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            return
        cmd = request.get("cmd")
        if cmd == "status":
            response = status_payload()
        elif cmd == "connect":
            response = handle_connect(request.get("ssid", ""), request.get("password", ""))
        else:
            response = {"error": f"unknown cmd {cmd!r}"}
        conn.sendall((json.dumps(response) + "\n").encode("utf-8"))


def main():
    threading.Thread(target=monitor_loop, daemon=True, name="wifi-bridge-monitor").start()

    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o666)  # container connects as uid 1000, host trust boundary only
    server.listen(1)

    print(f"wifi_bridge: listening on {SOCKET_PATH}, driving {IFACE} via nmcli", flush=True)

    try:
        while True:
            conn, _ = server.accept()
            # A single malformed request or a wedged nmcli call must never
            # take the daemon down (that would delete the socket and wedge
            # every app-side status read).
            try:
                handle_client(conn)
            except Exception as exc:  # noqa: BLE001 - deliberately broad
                print(f"wifi_bridge: client error: {exc}", file=sys.stderr, flush=True)
    finally:
        server.close()
        os.remove(SOCKET_PATH)


if __name__ == "__main__":
    main()
