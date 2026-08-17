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
   JSON out) for the app container to read current status, list nearby
   networks, and submit new factory-WiFi credentials:
     {"cmd": "status"}
       -> {"mode": "sta"|"ap"|"disconnected", "ssid": str|null, "ip": str|null}
     {"cmd": "scan"}
       -> {"networks": [{"ssid": str, "signal": int}, ...], "error": str|null}
       (networks strongest-signal first; error is set on a real scan
       failure/timeout, distinct from a genuinely empty result)
     {"cmd": "connect", "ssid": str, "password": str}
       -> {"success": bool, "error": str|null}
     {"cmd": "forget"}
       -> {"success": bool, "error": str|null}
   A connect attempt blocks this connection for up to CONNECT_TIMEOUT_S
   while nmcli tries the join -- deliberately synchronous (same "blocking
   call, caller's worker thread absorbs it" shape the app side already
   uses for e.g. POST /classifier/ei/link's blocking EI login) since this
   is a rare, one-shot, technician-driven action, not a polled path.
   "forget" is the only user-triggered way back to AP mode: it deletes the
   currently-joined network's NM connection profile (so autoconnect can't
   silently rejoin it) and brings the Hotspot back up. Without it the only
   path back to AP mode was the monitor loop noticing a dropped connection
   on its own -- there was no way to leave a working network on purpose.

3. A captive-portal redirect: while the Hotspot is up, provision-wifi.sh's
   dnsmasq-shared.d drop-in resolves EVERY hostname a joined phone/laptop
   looks up to the Hotspot's own IP (same trick real captive portals use --
   an OS's own connectivity-check probe, e.g. Apple's
   captive.apple.com/hotspot-detect.html or Google's generate_204, ends up
   pointed at us instead of the real internet). That alone isn't enough --
   the probe is plain HTTP on port 80, and the dashboard listens on
   DASHBOARD_PORT, not 80 -- so this daemon also runs a trivial port-80
   listener (_run_captive_portal_redirect) that 302s any request straight
   to the dashboard's Network tab. Seeing anything other than the exact
   response it expects is what makes the OS pop its own browser open on
   this redirect's Location automatically, the same "join WiFi -> a login
   page just appears" behavior as airport/hotel WiFi.

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
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time

SOCKET_PATH = "/dev/wifi-link.sock"
IFACE = "wlan0"
# Must match main.py's --port default (also app.yaml's exposed [8080, 8081]
# list) -- this is where the app container's dashboard actually listens;
# the port-80 redirect below only exists to bounce probes/browsers there.
DASHBOARD_PORT = 8080
CAPTIVE_PORTAL_PORT = 80
HOTSPOT_CON_NAME = "Hotspot"
# The Hotspot profile's actual broadcast SSID (provision-wifi.sh creates it
# with this ssid, con-name "Hotspot" -- distinct names on purpose, see
# status_payload()'s comment on why the two must not be conflated).
HOTSPOT_SSID = "EPM-BaseStation"
MONITOR_INTERVAL_S = 10
CONNECT_TIMEOUT_S = 45
# A real rescan (not just reading nmcli's cached scan cache) takes a few
# seconds on this radio -- generous headroom over that.
SCAN_TIMEOUT_S = 15
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


def scan_payload():
    """Returns nearby networks, strongest signal first, deduped by SSID (a
    network is usually seen once per radio/band it's on). Uses nmcli's own
    "auto" rescan judgment, NOT a forced "yes" rescan on every request --
    nmcli already tracks how stale its own scan cache is and only pays the
    real ~5-10s scan cost when it decides the cache is actually old, so a
    repeat call (e.g. the "Scan for networks" button after the automatic
    one on page load) is fast whenever a recent scan already ran. Doesn't
    hold _nm_lock: read-only from NM's point of view (doesn't touch our
    own connection profiles), so it's fine to run concurrently with the
    monitor loop or an in-flight connect.

    Returns {"networks": [...], "error": str|null} -- error distinguishes
    "nmcli itself failed or timed out" from a genuinely clean empty scan;
    without this, both would look identical over the wire and the
    dashboard couldn't tell an infra failure apart from "no networks
    nearby, this is real" -- see python/network/wifi.py's scan()."""
    try:
        result = _nmcli(["-t", "-f", "SSID,SIGNAL", "device", "wifi", "list", "--rescan", "auto"],
                         timeout=SCAN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"networks": [], "error": "Scan timed out"}
    if result.returncode != 0:
        return {"networks": [], "error": result.stderr.strip() or "Scan failed"}
    best_signal = {}
    for line in result.stdout.splitlines():
        # SIGNAL is always numeric (no colons), so the last colon is always
        # the field separator even if the SSID itself contains one -- same
        # reasoning as _device_state()'s split.
        ssid, _, signal_str = line.rpartition(":")
        ssid = ssid.strip()
        if not ssid or ssid == HOTSPOT_SSID:  # blank == hidden network; skip our own AP
            continue
        try:
            signal = int(signal_str)
        except ValueError:
            signal = 0
        if ssid not in best_signal or signal > best_signal[ssid]:
            best_signal[ssid] = signal
    networks = [{"ssid": ssid, "signal": signal} for ssid, signal in best_signal.items()]
    networks.sort(key=lambda n: n["signal"], reverse=True)
    return {"networks": networks, "error": None}


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


def handle_forget():
    """Leaves the currently-joined network on purpose and falls back to AP
    mode -- the counterpart to handle_connect(). Deletes the joined
    network's NM connection profile (not just `connection down`) so
    autoconnect can't immediately rejoin it out from under the fresh
    Hotspot, mirroring _connect_to_network's own delete-before-add idiom.
    ensure_hotspot_up() is called outside _nm_lock (same reasoning as
    handle_connect's own call to it) since it takes the lock itself."""
    with _nm_lock:
        state_code, connection, _ = _device_state()
        if not _is_connected_to_real_network(state_code, connection):
            return {"success": False, "error": "Not connected to a network"}
        _nmcli(["connection", "down", connection])
        _nmcli(["connection", "delete", connection])
    ensure_hotspot_up()
    return {"success": True, "error": None}


class _CaptivePortalRedirectHandler(http.server.BaseHTTPRequestHandler):
    """Answers every request on CAPTIVE_PORTAL_PORT with a 302 to the
    dashboard's Network tab. Deliberately ignores path/method specifics --
    an OS's captive-portal probe hits a fixed well-known URL (varies by
    OS/browser: Apple, Google, Microsoft, Firefox all use different ones)
    and this only needs to NOT return that probe's expected "you're on the
    real internet" response for the OS to assume a portal and open a
    browser on whatever this redirects to."""

    def _redirect(self):
        # self.connection.getsockname() is THIS accepted socket's local
        # address -- i.e. whatever IP the client actually dialed (the
        # Hotspot's address, reachable from where the client sits) --
        # rather than a hardcoded subnet, so this keeps working even if
        # NM's "shared" ipv4 method ever picks a different one.
        host_ip = self.connection.getsockname()[0]
        location = f"http://{host_ip}:{DASHBOARD_PORT}/?tab=network"
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._redirect()

    def do_HEAD(self):
        self._redirect()

    def log_message(self, format_str, *args):  # noqa: A002 - stdlib signature
        pass  # OS captive-portal probes poll every few seconds; silence per-request noise


def run_captive_portal_redirect():
    # Best-effort: if port 80 is somehow already taken, the rest of the
    # bridge (status/scan/connect, hotspot fallback) must keep working --
    # this is a nice-to-have UX layer on top, not load-bearing.
    try:
        server = http.server.ThreadingHTTPServer(("", CAPTIVE_PORTAL_PORT), _CaptivePortalRedirectHandler)
    except OSError as exc:
        print(f"wifi_bridge: captive-portal redirect disabled, port {CAPTIVE_PORTAL_PORT} unavailable: {exc}",
              file=sys.stderr, flush=True)
        return
    with server:
        server.serve_forever()


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
        elif cmd == "scan":
            response = scan_payload()
        elif cmd == "connect":
            response = handle_connect(request.get("ssid", ""), request.get("password", ""))
        elif cmd == "forget":
            response = handle_forget()
        else:
            response = {"error": f"unknown cmd {cmd!r}"}
        conn.sendall((json.dumps(response) + "\n").encode("utf-8"))


def main():
    threading.Thread(target=monitor_loop, daemon=True, name="wifi-bridge-monitor").start()
    threading.Thread(target=run_captive_portal_redirect, daemon=True, name="wifi-bridge-captive-portal").start()

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
