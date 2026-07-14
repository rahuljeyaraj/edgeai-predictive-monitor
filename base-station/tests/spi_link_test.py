#!/usr/bin/env python3
"""
MCU<->MPU dedicated SPI link verification - the Risk-1 spike from
docs/progress2.md's "THE NEXT CHANGE". Unlike the other tests/ scripts, this
one does NOT go through Bridge for the bulk data (that's the whole point of
moving off the UART) - it talks to base-station/host/spi_bridge.py's Unix
socket at /dev/spi-link.sock, which is the root daemon that owns
/dev/spidev0.0 on the MCU's behalf (see docs/progress2.md 4.2 for why a plain
spidev.open() from inside the app container doesn't work).

Reads N=64-byte frames back and checks them against sketch/spi_link.cpp's
known pattern: first 4 bytes a little-endian counter, remaining 60 bytes a
0..59 ramp. Checks BOTH that the ramp is correct (proves the bytes moved
across the wire intact) and that the counter changes between reads (proves
it's a live, re-armed frame, not stale/frozen data). get_spi_link_stats is
also polled over Bridge for corroborating transfer/completed/timeout counts
(see spi_link.cpp - no serial monitor was usable this session, this Bridge
provider was added specifically to make bring-up observable without one).

No handshake yet (docs/progress2.md decision 3, task 3 not built) - the MCU
re-arms continuously and free-runs, so a read here may or may not line up
with a specific frame; this only checks frame *shape*, not exact frame
identity across reads.

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
READ_COUNT = 10
READ_INTERVAL_S = 0.3


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


def check_pattern(data):
    if len(data) != FRAME_LEN:
        return None, f"short read: {len(data)}/{FRAME_LEN} bytes"
    (counter,) = struct.unpack("<I", data[:4])
    expected_ramp = bytes((i - 4) & 0xFF for i in range(4, FRAME_LEN))
    if data[4:] != expected_ramp:
        return counter, "ramp mismatch - bytes did not survive the wire intact"
    return counter, None


def main():
    try:
        stats = Bridge.call("get_spi_link_stats")
        print(f"get_spi_link_stats: checkpoint,transfers,completed,timeouts = {stats}")
    except Exception as exc:
        print(f"get_spi_link_stats unavailable ({exc}) - continuing with the raw socket read anyway")
    print()

    counters = []
    ok_count = 0
    for i in range(READ_COUNT):
        data = read_frame()
        counter, error = check_pattern(data)
        if error:
            print(f"[{i + 1:2d}/{READ_COUNT}] FAIL: {error} (raw={data!r})")
        else:
            print(f"[{i + 1:2d}/{READ_COUNT}] OK  counter={counter}")
            counters.append(counter)
            ok_count += 1
        time.sleep(READ_INTERVAL_S)

    print()
    print(f"{ok_count}/{READ_COUNT} frames had a correct ramp.")
    if len(counters) >= 2 and len(set(counters)) == 1:
        print("WARNING: counter never changed across reads - frame may be stale/frozen.")
    elif len(counters) >= 2:
        print(f"Counter varied across reads ({counters[0]} -> {counters[-1]}) - link is live.")


if __name__ == "__main__":
    main()
