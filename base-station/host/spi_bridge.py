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
# Root-caused and raised to 100_000_000 on 2026-07-20 (was 1_000_000), the
# same session BRIDGE_BAUD went to 500000 (app_config.h) - now that the bulk
# stream lives on this link, push it as fast as the hardware genuinely goes.
#
# Unlike BRIDGE_BAUD there's no MCU-side divisor math to root-cause here:
# this MPU is the SPI *master* (GENI QUP, spidev0.0) and the MCU's SPI3 is a
# hardware-NSS *slave* with no baud-rate register of its own - it just clocks
# whatever SCK the master drives. So "maximum" is entirely a master-side
# question, answered empirically:
#   - Requested speed vs REAL achieved bus clock (measured directly - time a
#     raw 65536B spi-link.sock transfer, back out Hz from elapsed time,
#     bypassing spi_arm so it's pure ioctl/hardware timing, no RPC overhead):
#     requested 1-48 MHz scaled ~linearly with real achieved (e.g. 48M ->
#     ~36M real); from ~64-80M requested onward, real achieved FLATLINES at
#     ~37-41 MHz no matter how high you ask (tried up to 256M requested,
#     same plateau) - a hard driver/clock-plan ceiling on this GENI SE
#     instance, not a cable/slave limit. 100_000_000 sits deep in that
#     plateau (not right at its edge) so small driver/kernel variance can't
#     walk it back below the real ceiling.
#   - Correctness at that real ~40 MHz clock, chunked pull (CHUNK_SIZE=512,
#     ingestion/spi_reader.py) unchanged: 300/300 whole-frame CRC-OK across
#     two independent soak runs (150 frames each), zero MCU-side timeouts/
#     errors in get_spi_link_stats throughout. Matches the BRIDGE_BAUD
#     lesson that a single ad-hoc call is not a valid stability check - these
#     were sustained runs, not one-off probes.
#   - IMPORTANT caveat found in the same session: frames have grown to
#     ~10.3-14.5 KB (was ~4.1 KB when CHUNK_SIZE=512 was originally tuned,
#     docs/progress2.md 5.8) from the accel/mic time-series piggyback
#     channels (commit a7f62bb). At the OLD 1 MHz speed this larger frame
#     size alone already dropped clean-window reliability to ~80% (24/30) -
#     well below the historical "512B = 20/20" baseline - purely from more
#     chunks per frame compounding the per-chunk failure rate, before any
#     speed change. The speed raise here fixes that (300/300 at ~40 MHz).
#   - Bigger chunks were tried too (would cut RPC round-trips - each spi_arm
#     is a UART call and is now the real fps bottleneck, not SPI clock time:
#     512B chunks and 100_000_000 still give ~2 fps because a 14.5KB frame
#     needs 29 arm round-trips). CHUNK_SIZE=2048 worked (95/100 soak) but not
#     perfectly clean, and >=4096 hard-hangs (spi_arm times out completely,
#     unrelated to SPI clock - a separate bug, not investigated further this
#     session). Left CHUNK_SIZE at the proven-clean 512 for now; revisit as
#     its own investigation if more fps is needed.
SPI_MAX_HZ = 100_000_000
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
