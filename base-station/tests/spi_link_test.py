#!/usr/bin/env python3
"""
SPI fuser-frame verification (MPU side), CHUNKED pull - docs/progress2.md 5.7.

A single ~4KB SPI3 slave-TX transfer underruns, but small byte-DMA sub-transfers
are rock solid, so the MPU pulls each frame in chunks: spi_arm(offset, len) arms
frame_buf[offset..offset+chunk] on the MCU, the MPU clocks <chunk> bytes over
/dev/spi-link.sock, reassembles all chunks, and verifies the whole-frame CRC32.
Chunk size is chosen here (MPU side), so this script sweeps a few sizes and
reports the success rate + throughput of each - use it to pick a reliable size.

SPI frame (little-endian): [magic u32=0x46555331][seq u16][payload_len u16]
[payload][crc32 u32 over header+payload]. Payload = the fuser's generic
section-list telemetry frame (docs/SENSOR_TELEMETRY_FRAME_PLAN.md S3).

Run on the board while the app is running (needs spi-bridge.service):
    adb shell "docker exec edgeai-predictive-monitor-base-station-main-1 python3 /app/tests/spi_link_test.py"
"""
import socket
import struct
import time
import zlib

from arduino.app_utils import Bridge

SOCKET_PATH = "/dev/spi-link.sock"
SPI_MAGIC = 0x46555331
SPI_HEADER_FMT = "<I H H"
SPI_HEADER_LEN = struct.calcsize(SPI_HEADER_FMT)   # 8
SPI_CRC_LEN = 4
# Payload is the generic section-list telemetry frame
# (docs/SENSOR_TELEMETRY_FRAME_PLAN.md S3): [num_sections u8] then per section
# [source u8][channel u8][kind u8][section_len u16][body]. A SPECTRUM body is
# [fs f32][fft_size u16][bin_count u16][bins f32...]. Constants mirror
# sketch/telemetry_schema.h (kept inline so this diagnostic stays self-contained).
TELEM_KIND_SPECTRUM = 1
TELEM_CHANNEL_MIC = 0
TELEM_CHANNEL_ACCEL = 1
SECTION_HEADER_FMT = "<BBBH"
SECTION_HEADER_LEN = struct.calcsize(SECTION_HEADER_FMT)  # 5

CHUNK_SIZES = [512, 256, 128, 64]
FRAMES_PER_SIZE = 20
ARM_RETRIES = 40


def read_socket(n):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5.0)
    try:
        s.connect(SOCKET_PATH)
        s.sendall(struct.pack("<I", n))
        d = b""
        while len(d) < n:
            c = s.recv(n - len(d))
            if not c:
                break
            d += c
        return d
    finally:
        s.close()


def pull_frame(chunk_size):
    """Pull one whole frame in <=chunk_size sub-transfers. Returns (seq, frame)
    or None on give-up. Does NOT verify CRC (caller does)."""
    frame = b""
    offset = 0
    total = None
    seq = None
    while total is None or offset < total:
        r = "busy"
        for _ in range(ARM_RETRIES):
            r = str(Bridge.call("spi_arm", str(offset), str(chunk_size)))
            if r not in ("busy", "empty", "done"):
                break
            time.sleep(0.01)
        if r in ("busy", "empty", "done"):
            return None
        parts = r.split(",")
        if len(parts) != 3:
            return None
        s, t, clen = int(parts[0]), int(parts[1]), int(parts[2])
        if seq is None:
            seq = s
        elif s != seq:
            return None  # frame changed mid-pull (shouldn't happen - latched)
        total = t
        data = read_socket(clen)
        if len(data) != clen:
            return None
        frame += data
        offset += clen
    return seq, frame


def frame_crc_ok(frame):
    if len(frame) < SPI_HEADER_LEN + SPI_CRC_LEN:
        return False
    magic, seq, payload_len = struct.unpack_from(SPI_HEADER_FMT, frame, 0)
    if magic != SPI_MAGIC:
        return False
    body_end = SPI_HEADER_LEN + payload_len
    if body_end + SPI_CRC_LEN != len(frame):
        return False
    (crc,) = struct.unpack_from("<I", frame, body_end)
    return (zlib.crc32(frame[:body_end]) & 0xFFFFFFFF) == crc


def _spectra(frame):
    """channel_id -> (fs, fft_size, bins) for every SPECTRUM section in the
    section-list payload."""
    payload = frame[SPI_HEADER_LEN:-SPI_CRC_LEN]
    (num_sections,) = struct.unpack_from("<B", payload, 0)
    off = 1
    out = {}
    for _ in range(num_sections):
        _source, channel, kind, seclen = struct.unpack_from(SECTION_HEADER_FMT, payload, off)
        off += SECTION_HEADER_LEN
        body = payload[off:off + seclen]
        off += seclen
        if kind == TELEM_KIND_SPECTRUM:
            fs, fft, n = struct.unpack_from("<fHH", body, 0)
            out[channel] = (fs, fft, struct.unpack_from(f"<{n}f", body, 8))
    return out


def decode_peaks(frame):
    spectra = _spectra(frame)
    mic_fs, mic_fft, mic = spectra[TELEM_CHANNEL_MIC]
    accel_fs, accel_fft, accel = spectra[TELEM_CHANNEL_ACCEL]
    mi = max(range(len(mic)), key=lambda k: mic[k])
    ai = max(range(len(accel)), key=lambda k: accel[k])
    return (mi, mic[mi], (mi + 1) * mic_fs / mic_fft,
            ai, accel[ai], (ai + 1) * accel_fs / accel_fft)


def main():
    try:
        print("get_spi_link_stats:", Bridge.call("get_spi_link_stats"))
    except Exception as exc:
        print("stats unavailable:", exc)
    print()

    for cs in CHUNK_SIZES:
        ok = 0
        t0 = time.monotonic()
        last = None
        for _ in range(FRAMES_PER_SIZE):
            res = pull_frame(cs)
            if res and frame_crc_ok(res[1]):
                ok += 1
                last = res[1]
        dt = time.monotonic() - t0
        fps = ok / dt if dt > 0 else 0
        line = f"chunk={cs:4d}B : {ok:2d}/{FRAMES_PER_SIZE} CRC-OK  ~{fps:4.1f} fps"
        if last:
            try:
                mi, mv, mhz, ai, av, ahz = decode_peaks(last)
                line += f"  | mic bin {mi}(~{mhz:.0f}Hz) mag={mv:.0f}  accel bin {ai}(~{ahz:.0f}Hz) mag={av:.1f}"
            except (struct.error, ValueError, IndexError, KeyError):
                line += "  | (payload not fuser data - SELFTEST ramp)"
        print(line)

    print()
    print("Pick the largest chunk with a solid CRC-OK rate. If peaks move with "
          "sound/motion, live capture is flowing end to end over SPI.")


if __name__ == "__main__":
    main()
