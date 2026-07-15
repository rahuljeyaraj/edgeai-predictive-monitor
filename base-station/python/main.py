"""
MPU-side consumer of the fuser spectrum stream over the dedicated MCU<->MPU SPI
link (docs/progress2.md tasks 4-5, chunked transport per 5.7). The bulk stream
rides SPI; the shared Bridge UART carries RPC/control only.

Data path: the fuser (MCU) stages each ~4.1KB frame via spi_link_stage_frame()
(framing header + CRC32). A single 4KB SPI3 slave-TX transfer underruns, so the
MPU pulls each frame in CHUNK_SIZE-byte sub-transfers: spi_arm(offset, len) arms
frame_buf[offset..offset+chunk] on the MCU and replies "<seq>,<total>,<chunk>";
we clock <chunk> bytes off /dev/spi-link.sock (the root host daemon
base-station/host/spi_bridge.py, since the app container can't open spidev0.0
directly - docs/progress2.md 4.2), reassemble all chunks, and verify the
whole-frame CRC32. A CRC failure retries the whole frame a few times, then drops
it (lossy live view is fine - the next pull gets a fresh one).

SPI frame (LE): [magic u32=0x46555331][seq u16][payload_len u16][payload]
[crc32 u32 over header+payload]. Payload = fuser frame:
  header = mic_fs(f32) mic_fft(u16) mic_bins(u16) accel_fs(f32) accel_fft(u16)
           accel_bins(u16)  (16B), then mic_bins f32, then accel_bins f32.
"""
import socket
import struct
import threading
import time
import zlib

from arduino.app_utils import App, Bridge

# --- Wire format (must match sketch/spi_link.cpp + sketch/fuser.cpp) ---------
SOCKET_PATH = "/dev/spi-link.sock"
SPI_MAGIC = 0x46555331
SPI_HEADER_FMT = "<I H H"
SPI_HEADER_LEN = struct.calcsize(SPI_HEADER_FMT)   # 8
SPI_CRC_LEN = 4
FUSER_HEADER_FMT = "<f H H f H H"
FUSER_HEADER_LEN = struct.calcsize(FUSER_HEADER_FMT)  # 16

# --- Transport tuning --------------------------------------------------------
# 512B was the reliable sweet spot in the chunk-size sweep (20/20 CRC-OK, ~8 fps);
# larger chunks are faster but the slave-TX underrun risk rises and gets flaky
# (docs/progress2.md 5.7). CRC-retry absorbs the occasional bad frame.
CHUNK_SIZE = 512
FRAME_RETRIES = 3
ARM_RETRIES = 30
PULL_INTERVAL_S = 0.02
SUMMARY_INTERVAL_S = 2.0


class SpiConsumer:
    """Pulls fuser frames over SPI (chunked) on a background thread; keeps the
    latest decoded spectrum + running stats for loop() (and, later, inference)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self.last_seq = None
        self.last_mic = None
        self.last_accel = None
        self.last_meta = None          # (mic_fs, mic_fft, accel_fs, accel_fft)
        self.frames_ok = 0
        self.frames_dup = 0
        self.frames_dropped = 0        # exhausted retries
        self.crc_fail = 0
        self.arm_gap = 0               # empty/busy/done stalls

    # -- transport ----------------------------------------------------------
    @staticmethod
    def _read_socket(n):
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

    def _pull_frame(self):
        """Pull one whole frame in <=CHUNK_SIZE sub-transfers. Returns
        (seq, frame_bytes) or None. Does not verify CRC (caller does)."""
        frame = b""
        offset = 0
        total = None
        seq = None
        while total is None or offset < total:
            reply = None
            for _ in range(ARM_RETRIES):
                try:
                    reply = str(Bridge.call("spi_arm", str(offset), str(CHUNK_SIZE)))
                except Exception:
                    return None
                if reply not in ("busy", "empty", "done"):
                    break
                time.sleep(0.005)
            if reply in ("busy", "empty", "done", None):
                with self._lock:
                    self.arm_gap += 1
                return None
            parts = reply.split(",")
            if len(parts) != 3:
                return None
            s_, t_, clen = int(parts[0]), int(parts[1]), int(parts[2])
            if seq is None:
                seq = s_
            elif s_ != seq:
                return None            # frame changed mid-pull (latched - shouldn't)
            total = t_
            try:
                data = self._read_socket(clen)
            except OSError:
                return None
            if len(data) != clen:
                return None
            frame += data
            offset += clen
        return seq, frame

    # -- decode -------------------------------------------------------------
    def _store(self, seq, frame):
        if len(frame) < SPI_HEADER_LEN + SPI_CRC_LEN:
            return False
        magic, fseq, payload_len = struct.unpack_from(SPI_HEADER_FMT, frame, 0)
        body_end = SPI_HEADER_LEN + payload_len
        if magic != SPI_MAGIC or body_end + SPI_CRC_LEN != len(frame):
            return False
        (crc,) = struct.unpack_from("<I", frame, body_end)
        if zlib.crc32(frame[:body_end]) & 0xFFFFFFFF != crc:
            with self._lock:
                self.crc_fail += 1
            return False

        payload = frame[SPI_HEADER_LEN:body_end]
        try:
            mic_fs, mic_fft, mic_n, accel_fs, accel_fft, accel_n = struct.unpack_from(
                FUSER_HEADER_FMT, payload, 0)
            off = FUSER_HEADER_LEN
            mic = struct.unpack_from(f"<{mic_n}f", payload, off)
            off += mic_n * 4
            accel = struct.unpack_from(f"<{accel_n}f", payload, off)
        except struct.error:
            return False

        with self._lock:
            if fseq == self.last_seq:
                self.frames_dup += 1
                return True
            self.last_seq = fseq
            self.last_mic = mic
            self.last_accel = accel
            self.last_meta = (mic_fs, mic_fft, accel_fs, accel_fft)
            self.frames_ok += 1
        # TODO(inference): feed (mic, accel) full-res float32 to the autoencoder.
        return True

    def _run(self):
        while True:
            got = False
            for _ in range(FRAME_RETRIES):
                res = self._pull_frame()
                if res and self._store(*res):
                    got = True
                    break
            if not got:
                with self._lock:
                    self.frames_dropped += 1
            time.sleep(PULL_INTERVAL_S)

    def start(self):
        self._thread = threading.Thread(target=self._run, name="spi-consumer", daemon=True)
        self._thread.start()

    def snapshot(self):
        with self._lock:
            return dict(seq=self.last_seq, mic=self.last_mic, accel=self.last_accel,
                        meta=self.last_meta, frames_ok=self.frames_ok,
                        frames_dup=self.frames_dup, frames_dropped=self.frames_dropped,
                        crc_fail=self.crc_fail, arm_gap=self.arm_gap)


def _peak(bins):
    idx = max(range(len(bins)), key=lambda k: bins[k])
    return idx, bins[idx]


_consumer = SpiConsumer()
_consumer.start()
_last = {"frames_ok": 0, "t": time.monotonic()}
print("main: SPI fuser consumer started (chunked pull over /dev/spi-link.sock)", flush=True)


def loop():
    time.sleep(SUMMARY_INTERVAL_S)
    snap = _consumer.snapshot()
    now = time.monotonic()
    dt = now - _last["t"]
    fps = (snap["frames_ok"] - _last["frames_ok"]) / dt if dt > 0 else 0.0
    _last["frames_ok"] = snap["frames_ok"]
    _last["t"] = now

    if snap["mic"] is None:
        print(f"main: no frames yet (dropped={snap['frames_dropped']} "
              f"crc_fail={snap['crc_fail']} arm_gap={snap['arm_gap']})", flush=True)
        return
    mic_fs, mic_fft, accel_fs, accel_fft = snap["meta"]
    mi, mv = _peak(snap["mic"])
    ai, av = _peak(snap["accel"])
    print(f"main: seq={snap['seq']:5d} ~{fps:4.1f}fps  "
          f"mic bin {mi:3d} (~{(mi+1)*mic_fs/mic_fft:5.0f}Hz) mag={mv:8.0f}  |  "
          f"accel bin {ai:3d} (~{(ai+1)*accel_fs/accel_fft:4.0f}Hz) mag={av:8.1f}  "
          f"[ok={snap['frames_ok']} dup={snap['frames_dup']} drop={snap['frames_dropped']} "
          f"crc_fail={snap['crc_fail']}]", flush=True)


App.run(user_loop=loop)
