#!/usr/bin/env python3
"""
spi_bridge.py - root-owned host-side bridge to the MCU<->MPU SPI link.

Why this exists: the app's Python code runs inside a Docker container
(edgeai-predictive-monitor-base-station-main-1) launched by arduino-app-cli.
/dev is bind-mounted wholesale into that container, so /dev/spidev0.0 is
*visible* there - but the container isn't privileged and its device-cgroup
allowlist (generated fresh by arduino-app-cli on every `app start`, no
app.yaml/CLI knob to extend it) doesn't include spidev's major number (153).
So a plain spidev.open() from inside the container gets EPERM at the cgroup
layer no matter what file permissions /dev/spidev0.0 has.

This daemon runs as root on the host (outside the container, via systemd -
see spi-bridge.service) where that restriction doesn't apply, owns the
spidev0.0 file descriptor, and re-exposes it to the container over a Unix
domain socket placed at /dev/spi-link.sock. /dev itself is already
bind-mounted into the container (confirmed: /dev/ttyHS1, /dev/spidev0.0 are
both visible there), so a socket file created here under /dev needs no new
compose/bind-mount plumbing and survives every app redeploy untouched.

Wire protocol (spike-minimal, one request per connection):
  request:  4-byte little-endian uint32 N (bytes to transfer)
  response: N raw bytes read back from a full-duplex SPI transfer (tx
            all-zero - only the slave->master direction is under test)
"""

import os
import socket
import struct
import sys

import spidev

SOCKET_PATH = "/dev/spi-link.sock"
SPI_BUS = 0
SPI_DEVICE = 0
SPI_MAX_HZ = 1_000_000
SPI_MODE = 0
MAX_TRANSFER_LEN = 1 << 20  # sanity cap, well above the 4112 B fuser frame


def handle_client(conn, spi):
    with conn:
        header = conn.recv(4)
        if len(header) < 4:
            return
        (length,) = struct.unpack("<I", header)
        if length == 0 or length > MAX_TRANSFER_LEN:
            return
        rx = bytes(spi.xfer2([0] * length))
        conn.sendall(rx)


def main():
    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_MAX_HZ
    spi.mode = SPI_MODE

    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o666)  # container connects as uid 1000, host trust boundary only
    server.listen(1)

    print(f"spi_bridge: listening on {SOCKET_PATH}, spidev{SPI_BUS}.{SPI_DEVICE} @ {SPI_MAX_HZ}Hz", flush=True)

    try:
        while True:
            conn, _ = server.accept()
            try:
                handle_client(conn, spi)
            except OSError as exc:
                print(f"spi_bridge: client error: {exc}", file=sys.stderr, flush=True)
    finally:
        server.close()
        os.remove(SOCKET_PATH)
        spi.close()


if __name__ == "__main__":
    main()
