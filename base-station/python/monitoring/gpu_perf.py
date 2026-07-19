"""GPU utilization for the Dev/perf page (docs/DEV_PERF_PAGE_PLAN.md S5b) --
polls the root-owned host/gpu_bridge.py daemon over its Unix socket at
/dev/gpu-perf.sock, since the app container can't reach the GPU's debugfs
busy% counter directly (root-only, not bind-mounted in) -- see
gpu_bridge.py's module docstring for the full "why".

Poll-not-push, same reasoning as ingestion/spi_reader.py's spi_arm round
trip: no continuous push stream on any link here, just a cheap ~1 Hz round
trip. This one is simpler than a Bridge/RPC call since the socket is
entirely local (host<->container over a Unix socket, not a flaky UART) --
a missing/refused socket just means gpu-bridge.service hasn't been
provisioned yet (provision-gpu.sh is a one-time, separate-from-deploy.sh
step), reported as `available: False`, not an error.
"""
import logging
import socket
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

SOCKET_PATH = "/dev/gpu-perf.sock"
POLL_INTERVAL_S = 1.0
SOCKET_TIMEOUT_S = 2.0


class GpuPerfPoller:
    """Background poll loop: start() spawns one daemon thread, snapshot()
    reads the cached last-known reading under a lock. Distinguishes
    "bridge not provisioned/unreachable" (available: False) from
    "provisioned and genuinely idle" (available: True, busy_percent: 0.0)
    -- the frontend must never show a fake 0% standing in for "no data"."""

    def __init__(self, interval_s: float = POLL_INTERVAL_S):
        self._interval_s = interval_s
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._available = False
        self._busy_percent: Optional[float] = None

    def _poll_once(self) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(SOCKET_TIMEOUT_S)
            sock.connect(SOCKET_PATH)
            reply = sock.recv(64).decode("ascii", errors="replace").strip()

        with self._lock:
            if reply == "unavailable" or not reply:
                self._available = False
                self._busy_percent = None
            else:
                self._available = True
                self._busy_percent = float(reply)

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
                    self._busy_percent = None
            except Exception:
                logger.exception("gpu perf poll failed")
            time.sleep(self._interval_s)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="gpu-perf-poller", daemon=True)
        self._thread.start()

    def snapshot(self) -> dict:
        with self._lock:
            return {"available": self._available, "busy_percent": self._busy_percent}
