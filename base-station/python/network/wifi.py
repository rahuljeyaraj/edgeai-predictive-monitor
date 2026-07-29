"""App-side client for host/wifi_bridge.py's /dev/wifi-link.sock
(docs/WIFI_ONBOARDING_PLAN.md S1) -- the app container can't drive wlan0
directly (no CAP_NET_ADMIN), so this is a thin Unix-socket client, same
trust-boundary reasoning as ingestion/spi_reader.py's /dev/spi-link.sock
and monitoring/gpu_perf.py's /dev/gpu-perf.sock.

WifiStatusPoller polls `status` on a background thread and caches the
result -- same shape as monitoring/gpu_perf.py's GpuPerfPoller: a missing/
refused socket just means wifi-bridge.service hasn't been provisioned yet
(provision-wifi.sh is a one-time, separate-from-deploy.sh step), reported
as `available: False`, not an error.

connect() is different: a rare, technician-driven, one-shot action, called
directly (blocking) from a FastAPI route handler -- FastAPI runs plain
`def` handlers in a worker thread, the same shape already used for e.g.
POST /classifier/ei/link's blocking EI login call, so no background-job/
WS-progress machinery is needed here either.
"""
import json
import logging
import socket
import threading
import time

logger = logging.getLogger(__name__)

SOCKET_PATH = "/dev/wifi-link.sock"
POLL_INTERVAL_S = 3.0
STATUS_TIMEOUT_S = 2.0
# Generous: must outlast wifi_bridge.py's own CONNECT_TIMEOUT_S (45s) so a
# genuine nmcli timeout on the other end is what surfaces, not this socket
# read timing out first and masking it as "bridge unreachable."
CONNECT_TIMEOUT_S = 60.0
# Must outlast wifi_bridge.py's own SCAN_TIMEOUT_S (15s), same reasoning as
# CONNECT_TIMEOUT_S above.
SCAN_TIMEOUT_S = 20.0


def _request(payload: dict, timeout: float) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(SOCKET_PATH)
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    return json.loads(data.split(b"\n", 1)[0])


class WifiStatusPoller:
    """Background poll loop against wifi_bridge's `status` command.
    start() spawns one daemon thread, snapshot() reads the cached
    last-known reading under a lock."""

    def __init__(self, interval_s: float = POLL_INTERVAL_S):
        self._interval_s = interval_s
        self._lock = threading.Lock()
        self._available = False
        self._mode = None
        self._ssid = None
        self._ip = None

    def _poll_once(self) -> None:
        reply = _request({"cmd": "status"}, STATUS_TIMEOUT_S)
        with self._lock:
            self._available = True
            self._mode = reply.get("mode")
            self._ssid = reply.get("ssid")
            self._ip = reply.get("ip")

    def _run(self) -> None:
        while True:
            try:
                self._poll_once()
            except (OSError, ValueError):
                # Socket missing (bridge not provisioned), connection
                # refused (daemon not running), or a garbled reply -- all
                # equally mean "no real reading right now."
                with self._lock:
                    self._available = False
                    self._mode = self._ssid = self._ip = None
            except Exception:
                logger.exception("wifi status poll failed")
            time.sleep(self._interval_s)

    def start(self) -> None:
        threading.Thread(target=self._run, name="wifi-status-poller", daemon=True).start()

    def snapshot(self) -> dict:
        with self._lock:
            return {"available": self._available, "mode": self._mode,
                     "ssid": self._ssid, "ip": self._ip}


def scan() -> dict:
    """Blocking request to wifi_bridge for nearby networks. Returns
    {"networks": [...], "error": str|None} -- never raises, since a scan
    failure shouldn't take down the connect form. `error` distinguishes a
    real failure (bridge unreachable, or wifi_bridge's own nmcli call
    failed/timed out -- see its scan_payload()) from a genuinely empty
    scan, so the frontend can show "try again" instead of a misleading
    "no networks nearby" for what was really a transient failure."""
    try:
        return _request({"cmd": "scan"}, SCAN_TIMEOUT_S)
    except (OSError, ValueError) as exc:
        return {"networks": [], "error": f"wifi-bridge unreachable: {exc}"}


def connect(ssid: str, password: str) -> dict:
    """Blocking request to wifi_bridge to join `ssid` as the factory
    network. Returns {"success": bool, "error": str|None} -- a normal join
    failure (wrong password, out of range) comes back this way, not as an
    exception. Raises RuntimeError only when the bridge itself is
    unreachable (not provisioned / daemon down), a distinct condition the
    route handler reports differently (503, not 400)."""
    try:
        return _request({"cmd": "connect", "ssid": ssid, "password": password}, CONNECT_TIMEOUT_S)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"wifi-bridge unreachable: {exc}") from exc
