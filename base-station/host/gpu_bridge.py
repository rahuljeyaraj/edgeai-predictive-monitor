#!/usr/bin/env python3
"""
gpu_bridge.py - root-owned host-side bridge exposing the Adreno GPU's live
busy-percentage counter to the app container over a Unix socket.

Why this exists: same reason as spi_bridge.py -- the app's Python code runs
inside a non-privileged Docker container, and /sys/kernel/debug is root-only
and not bind-mounted/reachable there. This daemon runs as root on the host
(via systemd -- see gpu-bridge.service), reads the GPU's live %BUSY debugfs
stream, and re-exposes the latest reading over a Unix socket at
/dev/gpu-perf.sock. /dev is already bind-mounted into the container (same
trust-boundary reasoning as spi_bridge.py), so this needs no new compose/
bind-mount plumbing.

GPU debugfs discovery: this board's msm/adreno driver exposes ONE combined
DRM device -- display and GPU components share a single
/sys/kernel/debug/dri/<N>/ directory (verified on hardware: that directory
carries both display files (crtc-0/encoder-0/DP-1/kms) AND GPU files
(gpu/perf/devfreq/gem) together, and /sys/class/drm/renderD<N>/device
resolves to the *display* platform device even though the render node is
the GPU's own submission interface -- a red herring for discovery, not a
reliable signal). Rather than hardcode a minor number that could shift
across kernel versions, this scans /sys/kernel/debug/dri/ for a numeric
directory that has both a "gpu" and a "perf" file and uses whichever one it
finds.

The "perf" file streams repeating "%BUSY\n  <value>%\n" x N blocks
continuously while open (confirmed on hardware -- currently 0.0% throughout,
correctly, since nothing uses the GPU yet). Best-effort interpretation:
this reports the average of the most recently completed block as "the
current busy%" -- the exact per-line semantics of that block (independent
counters vs a rolling window of samples) are unconfirmed since there's no
real GPU load yet to test against; worth re-checking once on-device
inference actually drives the GPU.

Wire protocol (one request per connection, no request payload needed):
  response: ascii text, either "<percent, 1 decimal>\n" or "unavailable\n"
            (the reader thread hasn't completed a block yet, or the
            debugfs read failed and is retrying)
"""
import glob
import os
import socket
import sys
import threading
import time

SOCKET_PATH = "/dev/gpu-perf.sock"
DEBUGFS_DRI = "/sys/kernel/debug/dri"
RETRY_DELAY_S = 5


def find_gpu_perf_file():
    """Returns the path to the GPU's debugfs "perf" file, discovered by
    scanning for a numeric dri/<N>/ dir that also has a "gpu" file (see
    module docstring) rather than trusting a hardcoded minor number or the
    renderD*/device symlink (which points at the display controller on
    this board's combined DPU+GPU driver)."""
    for entry in sorted(glob.glob(os.path.join(DEBUGFS_DRI, "*"))):
        if not os.path.basename(entry).isdigit():
            continue
        perf_path = os.path.join(entry, "perf")
        gpu_path = os.path.join(entry, "gpu")
        if os.path.exists(perf_path) and os.path.exists(gpu_path):
            return perf_path
    return None


class GpuBusyReader:
    """Owns the persistent read of the streaming debugfs "perf" file on a
    background thread, updating "latest busy%" as each %BUSY block
    completes. Never re-opens per-request -- the file streams forever once
    opened (confirmed on hardware: a plain `cat` never reaches EOF)."""

    def __init__(self, perf_path):
        self._perf_path = perf_path
        self._lock = threading.Lock()
        self._latest = None

    def latest(self):
        with self._lock:
            return self._latest

    def run(self):
        while True:
            try:
                self._read_forever()
            except Exception as exc:  # noqa: BLE001 - deliberately broad, see spi_bridge.py
                print(f"gpu_bridge: perf read error: {exc}, retrying in {RETRY_DELAY_S}s",
                      file=sys.stderr, flush=True)
                with self._lock:
                    self._latest = None
                time.sleep(RETRY_DELAY_S)

    def _read_forever(self):
        with open(self._perf_path, "r") as f:
            block = []
            for line in f:
                line = line.strip()
                if line == "%BUSY":
                    if block:
                        self._update(block)
                    block = []
                    continue
                if line.endswith("%"):
                    try:
                        block.append(float(line[:-1]))
                    except ValueError:
                        continue

    def _update(self, block):
        avg = sum(block) / len(block)
        with self._lock:
            self._latest = avg


def handle_client(conn, reader):
    with conn:
        value = reader.latest()
        reply = "unavailable" if value is None else f"{value:.1f}"
        conn.sendall((reply + "\n").encode("ascii"))


def main():
    perf_path = find_gpu_perf_file()
    if perf_path is None:
        print(f"gpu_bridge: no GPU debugfs perf file found under {DEBUGFS_DRI}, exiting",
              file=sys.stderr, flush=True)
        sys.exit(1)

    reader = GpuBusyReader(perf_path)
    threading.Thread(target=reader.run, daemon=True, name="gpu-perf-reader").start()

    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o666)  # container connects as uid 1000, host trust boundary only
    server.listen(1)

    print(f"gpu_bridge: listening on {SOCKET_PATH}, reading {perf_path}", flush=True)

    try:
        while True:
            conn, _ = server.accept()
            # A single client error must never take the daemon down (that
            # would delete the socket and wedge every app-side read).
            try:
                handle_client(conn, reader)
            except Exception as exc:  # noqa: BLE001 - deliberately broad
                print(f"gpu_bridge: client error: {exc}", file=sys.stderr, flush=True)
    finally:
        server.close()
        os.remove(SOCKET_PATH)


if __name__ == "__main__":
    main()
