"""
MPU-side consumer of the fuser spectrum stream over the dedicated MCU<->MPU SPI
link (docs/progress2.md tasks 4-5, chunked transport per 5.7). The bulk stream
rides SPI; the shared Bridge UART carries RPC/control only.

This is this repo's direct analog of the old repo's ingestion/uart_reader.py:
one process-wide instance for the one local base-station node (see
BASE_STATION_NODE_ID below), normalizing every decoded frame into a
SensorFrame and handing it to an on_frame callback -- the same push-model
shape ingestion/mqtt_subscriber.py uses for satellite nodes, so main.py wires
both identically into PipelineManager.route.

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
[crc32 u32 over header+payload]. The SPI envelope (this file) is unchanged and
payload-agnostic; the payload is now the generic section-list telemetry frame
(common/telemetry_frame.py, docs/SENSOR_TELEMETRY_FRAME_PLAN.md S3), decoded
here via decode_frame() -- so which/how many sensor channels a frame carries no
longer lives in this file at all (it loops over sections and dispatches on
data_kind), which was the whole point of the plan.
"""
import socket
import struct
import threading
import time
import zlib
from typing import Callable, Optional

from arduino.app_utils import Bridge

from bridge_lock import BRIDGE_LOCK
from sensor_frame import BASE_STATION_NODE_ID, FrameSource, SensorFrame
from telemetry_frame import DecodedFrame, MalformedFrameError, decode_frame

# --- SPI transport envelope (must match sketch/spi_link.cpp) -----------------
SOCKET_PATH = "/dev/spi-link.sock"
SPI_MAGIC = 0x46555331
SPI_HEADER_FMT = "<I H H"
SPI_HEADER_LEN = struct.calcsize(SPI_HEADER_FMT)   # 8
SPI_CRC_LEN = 4

# --- Transport tuning --------------------------------------------------------
# 512B was the reliable sweet spot in the chunk-size sweep (20/20 CRC-OK, ~8 fps);
# larger chunks are faster but the slave-TX underrun risk rises and gets flaky
# (docs/progress2.md 5.7). CRC-retry absorbs the occasional bad frame.
CHUNK_SIZE = 512
FRAME_RETRIES = 3
ARM_RETRIES = 30
PULL_INTERVAL_S = 0.02


class SpiConsumer:
    """Pulls fuser frames over SPI (chunked) on a background thread, normalizing
    each newly-decoded frame into a SensorFrame and handing it to on_frame --
    the push-model counterpart to MqttSubscriber, feeding PipelineManager the
    same way. Keeps running stats for diagnostics (snapshot())."""

    def __init__(self, on_frame: Callable[[SensorFrame], None],
                 on_decoded: Optional[Callable[[DecodedFrame], None]] = None):
        self._on_frame = on_frame
        # Optional hook for whatever a decoded frame carried beyond .bins
        # (e.g. .time_series -- see tools/raw_capture.py). Not used by the
        # live pipeline (main.py); SensorFrame.bins is still the only thing
        # PipelineManager.route ever sees.
        self._on_decoded = on_decoded
        self._lock = threading.Lock()
        self._thread = None
        self.last_seq = None
        self.last_bins = None          # {channel_name: bins} of the last decoded frame
        self.last_meta = None          # {channel_name: (fs, fft_size)}
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
                    with BRIDGE_LOCK:
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
    def _store(self, seq, frame) -> bool:
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
            decoded = decode_frame(payload)
        except (MalformedFrameError, struct.error):
            return False

        with self._lock:
            if fseq == self.last_seq:
                self.frames_dup += 1
                return True
            self.last_seq = fseq
            self.last_bins = dict(decoded.bins)
            self.last_meta = {name: (s.fs, s.fft_size)
                              for name, s in decoded.spectra.items()}
            self.frames_ok += 1

        if self._on_decoded is not None:
            self._on_decoded(decoded)

        self._on_frame(SensorFrame(
            node_id=BASE_STATION_NODE_ID,
            source=FrameSource.SPI,
            timestamp=time.time(),
            bins=decoded.bins,
        ))
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

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="spi-consumer", daemon=True)
        self._thread.start()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(seq=self.last_seq, bins=self.last_bins, meta=self.last_meta,
                        frames_ok=self.frames_ok, frames_dup=self.frames_dup,
                        frames_dropped=self.frames_dropped, crc_fail=self.crc_fail,
                        arm_gap=self.arm_gap)
