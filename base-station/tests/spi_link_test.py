#!/usr/bin/env python3
"""
MCU<->MPU dedicated SPI link verification - the Risk-1 spike from
docs/progress2.md's "THE NEXT CHANGE". Unlike the other tests/ scripts, this
one does NOT go through Bridge for the bulk data (that's the whole point of
moving off the UART) - it talks to base-station/host/spi_bridge.py's Unix
socket at /dev/spi-link.sock, which is the root daemon that owns
/dev/spidev0.0 on the MCU's behalf (see docs/progress2.md 4.2 for why a plain
spidev.open() from inside the app container doesn't work).

Protocol (the RPC-triggered handshake, docs/progress2.md decision 3/task 3):
each round first calls the "spi_arm" Bridge provider - the MCU stages one
64-byte frame (4-byte little-endian counter + 0..59 ramp, see
sketch/spi_link.cpp) and replies with that frame's counter - then clocks the
frame out over the socket and checks BOTH that the ramp survived the wire
intact AND that the counter is exactly the one spi_arm promised. A "busy"
reply (previous arm still in flight) is retried briefly.

Earlier free-running variant for reference: without the arm handshake the
MCU re-armed on its own 1s timeout cycle and blind reads only landed on a
fresh frame ~1-2 times in 10 (2026-07-14 hardware run) - which is why the
handshake exists.

Run on the board while the app is running (needs spi-bridge.service - see
base-station/provision-spi.sh):
    adb shell "docker exec edgeai-predictive-monitor-base-station-main-1 python3 /app/tests/spi_link_test.py"
"""
import socket
import struct
import time

from arduino.app_utils import Bridge

SOCKET_PATH = "/dev/spi-link.sock"
FRAME_LEN = 64
ROUND_COUNT = 20
ARM_RETRIES = 5


def read_frame():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5.0)
    try:
        s.connect(SOCKET_PATH)
        s.sendall(struct.pack("<I", FRAME_LEN))
        data = b""
        while len(data) < FRAME_LEN:
            chunk = s.recv(FRAME_LEN - len(data))
            if not chunk:
                break
            data += chunk
        return data
    finally:
        s.close()


def arm():
    """Ask the MCU to stage a frame; returns its counter, or None."""
    for _ in range(ARM_RETRIES):
        reply = Bridge.call("spi_arm")
        if reply != "busy":
            return int(reply)
        time.sleep(0.1)
    return None


def check_pattern(data, expected_counter):
    if len(data) != FRAME_LEN:
        return f"short read: {len(data)}/{FRAME_LEN} bytes"
    (counter,) = struct.unpack("<I", data[:4])
    expected_ramp = bytes((i - 4) & 0xFF for i in range(4, FRAME_LEN))
    if data[4:] != expected_ramp:
        return "ramp mismatch - bytes did not survive the wire intact"
    if counter != expected_counter:
        return f"counter mismatch: armed {expected_counter}, read {counter}"
    return None


def main():
    try:
        stats = Bridge.call("get_spi_link_stats")
        print(f"get_spi_link_stats: checkpoint,transfers,completed,timeouts,errors,last_error_flags = {stats}")
    except Exception as exc:
        print(f"get_spi_link_stats unavailable ({exc}) - continuing anyway")
    print()

    ok_count = 0
    for i in range(ROUND_COUNT):
        counter = arm()
        if counter is None:
            print(f"[{i + 1:2d}/{ROUND_COUNT}] FAIL: spi_arm stayed busy after {ARM_RETRIES} retries")
            continue
        data = read_frame()
        error = check_pattern(data, counter)
        if error:
            print(f"[{i + 1:2d}/{ROUND_COUNT}] FAIL: {error} (raw={data!r})")
        else:
            print(f"[{i + 1:2d}/{ROUND_COUNT}] OK  counter={counter}")
            ok_count += 1

    print()
    print(f"{ok_count}/{ROUND_COUNT} armed frames read back correct and in sync.")
    try:
        stats = Bridge.call("get_spi_link_stats")
        print(f"final stats: {stats}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
