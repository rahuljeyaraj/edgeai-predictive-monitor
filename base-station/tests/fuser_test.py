#!/usr/bin/env python3
"""
fuser end-to-end verification (MPU side).

Receives the fuser's chunked binary push (sketch/fuser.cpp), reassembles full
frames, decodes the self-describing header + full-resolution float32 mic/accel
spectra, and prints a live per-frame summary so you can confirm real, reactive
capture is flowing (not stuck/zero data).

Wire format (little-endian throughout):
  each "spec_chunk" notify payload = bytes:
    [0]    magic 0xF5
    [1..2] frame_seq (u16)
    [3]    chunk_idx
    [4]    chunk_count
    [5..]  frame-byte slice
  reassembled frame = header(16B) + mic_bin_count f32 + accel_bin_count f32:
    header = mic_fs(f32) mic_fft_size(u16) mic_bin_count(u16)
             accel_fs(f32) accel_fft_size(u16) accel_bin_count(u16)

Run on the board while the app is running:
    adb shell "docker exec edgeai-predictive-monitor-base-station-main-1 python3 /app/tests/fuser_test.py"
"""
import struct
import threading
import time

from arduino.app_utils import Bridge

MAGIC = 0xF5
HEADER_FMT = "<f H H f H H"
HEADER_LEN = struct.calcsize(HEADER_FMT)  # 16
RUN_SECONDS = 12

_lock = threading.Lock()
_partial = {}          # frame_seq -> {chunk_idx: bytes}
_frames = []           # completed (frame_seq, frame_bytes)
_stats = {"chunks": 0, "bad": 0}


def on_spec_chunk(payload):
    """Handler the MCU notifies with each chunk (msgpack bin -> bytes)."""
    if not isinstance(payload, (bytes, bytearray)) or len(payload) < 5 or payload[0] != MAGIC:
        with _lock:
            _stats["bad"] += 1
        return
    data = bytes(payload)
    frame_seq = data[1] | (data[2] << 8)
    chunk_idx = data[3]
    chunk_count = data[4]
    with _lock:
        _stats["chunks"] += 1
        slot = _partial.setdefault(frame_seq, {})
        slot[chunk_idx] = data[5:]
        if len(slot) == chunk_count:
            frame = b"".join(slot[i] for i in range(chunk_count))
            _frames.append((frame_seq, frame))
            del _partial[frame_seq]
            # Bound memory: forget partial frames older than the one just completed.
            for seq in [s for s in _partial if s < frame_seq]:
                del _partial[seq]


def decode(frame):
    mic_fs, mic_fft, mic_n, accel_fs, accel_fft, accel_n = struct.unpack_from(HEADER_FMT, frame, 0)
    off = HEADER_LEN
    mic = struct.unpack_from(f"<{mic_n}f", frame, off)
    off += mic_n * 4
    accel = struct.unpack_from(f"<{accel_n}f", frame, off)
    return (mic_fs, mic_fft, mic, accel_fs, accel_fft, accel)


def peak(bins):
    idx = max(range(len(bins)), key=lambda k: bins[k])
    return idx, bins[idx]


def main():
    Bridge.provide("spec_chunk", on_spec_chunk)
    print(f'Registered "spec_chunk". Reassembling fuser frames for {RUN_SECONDS}s.')
    print("Make some noise / tap-shake the board and watch the peaks move.\n")

    seen = 0
    first_header_printed = False
    start = time.monotonic()
    while time.monotonic() - start < RUN_SECONDS:
        with _lock:
            frames = _frames[seen:]
            seen = len(_frames)
        for frame_seq, frame in frames:
            try:
                mic_fs, mic_fft, mic, accel_fs, accel_fft, accel = decode(frame)
            except struct.error as e:
                print(f"  [seq {frame_seq}] decode error: {e} (len={len(frame)})")
                continue
            if not first_header_printed:
                print(f"header: mic fs={mic_fs:.0f}Hz fft={mic_fft} bins={len(mic)} | "
                      f"accel fs={accel_fs:.0f}Hz fft={accel_fft} bins={len(accel)} | "
                      f"frame={len(frame)}B\n")
                first_header_printed = True
            mi, mv = peak(mic)
            ai, av = peak(accel)
            mic_hz = (mi + 1) * mic_fs / mic_fft
            accel_hz = (ai + 1) * accel_fs / accel_fft
            print(f"  [seq {frame_seq:4d}] mic peak bin {mi:3d} (~{mic_hz:5.0f}Hz) mag={mv:8.0f}"
                  f"  |  accel peak bin {ai:3d} (~{accel_hz:4.0f}Hz) mag={av:8.1f}")
        time.sleep(0.25)

    with _lock:
        chunks, bad, ncomplete = _stats["chunks"], _stats["bad"], len(_frames)
    print()
    print(f"chunks={chunks} bad={bad} complete_frames={ncomplete} "
          f"(~{ncomplete / RUN_SECONDS:.1f} fps)")
    if ncomplete == 0:
        print("RESULT: FAIL - no complete frames reassembled.")
    elif bad:
        print("RESULT: PARTIAL - frames flowing but some malformed chunks seen.")
    else:
        print("RESULT: PASS - full-res float32 frames reassembled cleanly. If the "
              "peaks move with sound/motion, live capture is flowing end to end.")


if __name__ == "__main__":
    main()
