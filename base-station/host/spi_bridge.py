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
bind-mounted into the container, so a socket file created here under /dev
needs no new compose/bind-mount plumbing and survives every app redeploy.

Transfer path: the fuser frame is ~4.1 KB, larger than py-spidev's xfer2()
hardcoded 4096-byte argument-list cap (OverflowError) - and its xfer3()
splits a big buffer into multiple SPI_IOC_MESSAGE calls, each toggling CS,
which would break the single-CS-per-frame contract the MCU relies on (its
SPI3 slave stages one TSIZE-sized transfer per arm; a mid-frame NSS deassert
would abort it). So we do the transfer as ONE raw SPI_IOC_MESSAGE ioctl with
a single spi_ioc_transfer (one CS assertion for the whole frame). The kernel
enforces its own limit on that message too, so spidev's `bufsiz` module param
must be >= the frame size - provision-spi.sh installs
/etc/modprobe.d/spidev.conf (bufsiz=65536) for that. py-spidev is still used
for open()/mode/speed config; only the bulk transfer is raw.

Wire protocol (one request per connection):
  request:  4-byte little-endian uint32 N (bytes to transfer)
  response: N raw bytes read back from a full-duplex SPI transfer (tx
            all-zero - only the slave->master direction is under test)
"""

import ctypes
import fcntl
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
SPI_BITS_PER_WORD = 8
MAX_TRANSFER_LEN = 1 << 20  # sanity cap, well above the ~4.1 KB fuser frame

# struct spi_ioc_transfer is 32 bytes on 64-bit:
#   __u64 tx_buf, __u64 rx_buf, __u32 len, __u32 speed_hz,
#   __u16 delay_usecs, __u8 bits_per_word, __u8 cs_change,
#   __u8 tx_nbits, __u8 rx_nbits, __u8 word_delay_usecs, __u8 pad
_SPI_IOC_TRANSFER_FMT = "QQIIHBBBBBB"
# _IOW(SPI_IOC_MAGIC 'k', 0, struct spi_ioc_transfer[1]) with sizeof == 32:
#   (dir=write<<30) | (size 32<<16) | (type 'k'==0x6b <<8) | (nr 0)
_SPI_IOC_MESSAGE_1 = 0x40206B00


def spi_read(fd, length):
    """One full-duplex SPI transfer of `length` bytes as a single CS-delimited
    SPI_IOC_MESSAGE. tx is all-zero; returns the `length` rx bytes."""
    tx = ctypes.create_string_buffer(length)  # zero-filled
    rx = ctypes.create_string_buffer(length)
    xfer = struct.pack(
        _SPI_IOC_TRANSFER_FMT,
        ctypes.addressof(tx),   # tx_buf
        ctypes.addressof(rx),   # rx_buf
        length,                 # len
        SPI_MAX_HZ,             # speed_hz
        0,                      # delay_usecs
        SPI_BITS_PER_WORD,      # bits_per_word
        0,                      # cs_change (0 = deassert CS after this transfer)
        0,                      # tx_nbits
        0,                      # rx_nbits
        0,                      # word_delay_usecs
        0,                      # pad
    )
    fcntl.ioctl(fd, _SPI_IOC_MESSAGE_1, bytearray(xfer))
    return rx.raw[:length]


def handle_client(conn, fd):
    with conn:
        header = conn.recv(4)
        if len(header) < 4:
            return
        (length,) = struct.unpack("<I", header)
        if length == 0 or length > MAX_TRANSFER_LEN:
            return
        rx = spi_read(fd, length)
        conn.sendall(rx)


def main():
    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_MAX_HZ
    spi.mode = SPI_MODE
    fd = spi.fileno()

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
            # A single malformed/oversized request must never take the daemon
            # down (that would delete the socket and wedge every app-side read).
            try:
                handle_client(conn, fd)
            except Exception as exc:  # noqa: BLE001 - deliberately broad
                print(f"spi_bridge: client error: {exc}", file=sys.stderr, flush=True)
    finally:
        server.close()
        os.remove(SOCKET_PATH)
        spi.close()


if __name__ == "__main__":
    main()
