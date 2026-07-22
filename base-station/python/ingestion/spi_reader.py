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

Data path: the fuser (MCU) stages each frame via spi_link_stage_frame() (framing
header + CRC32). A single multi-KB SPI3 slave-TX transfer underruns, so the
frame is pulled in CHUNK_SIZE-byte sub-transfers, auto-advancing (2026-07-20):
one spi_arm_stream(chunk_size) RPC call arms the MCU for the WHOLE frame, and
sketch/spi_link.cpp's transport thread re-arms each next chunk itself the
instant the previous one completes - no RPC round trip between chunks. We just
read CHUNK_SIZE bytes off /dev/spi-link.sock (the root host daemon
base-station/host/spi_bridge.py, since the app container can't open spidev0.0
directly - docs/progress2.md 4.2) back-to-back, paced by STREAM_PACING_S so the
MCU has time to re-arm between reads (see that constant's comment - there's no
hardware ready line, docs/progress2.md 4.1, so this pacing is what keeps a
too-early read rare rather than impossible), reassemble all chunks, and verify
the whole-frame CRC32. A CRC failure retries the whole frame a few times, then
drops it (lossy live view is fine - the next pull gets a fresh one). This
replaced a plain per-chunk spi_arm(offset, len) RPC call (one round trip per
chunk) once frames grew past a handful of chunks and RPC round-trip cost, not
SPI clock time, became the fps bottleneck - sketch/spi_link.cpp's spi_arm()
still exists unchanged for tests/spi_link_test.py and ad-hoc diagnostics.

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

try:
    from arduino.app_utils import Bridge
except ImportError:
    # Desktop dev run (docs: base-station/start_desktop_dashboard.sh) --
    # no App Lab container, so this device's own SPI-connected sensors
    # can never produce a frame. Every Bridge.call() site below already
    # wraps the call in a broad try/except, so a raising stub just means
    # this device's own SPI ingestion silently never yields a frame
    # (no base_station node registers) instead of crashing main.py's own
    # import of this module.
    class Bridge:
        @staticmethod
        def call(*args, **kwargs):
            raise RuntimeError("arduino.app_utils unavailable (no App Lab container / MCU)")

import telemetry_schema as schema
from bridge_lock import BRIDGE_LOCK
from registry import SensorChannel
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
# (docs/progress2.md 5.7) - CHUNK_SIZE>=4096 hard-hangs the arm itself
# (2026-07-20 spike, unrelated to SPI clock speed, not root-caused - a future
# TODO if more fps is needed beyond what streaming below already buys). CRC-
# retry absorbs the occasional bad frame either way.
CHUNK_SIZE = 512
# Gap between successive chunk reads in the auto-advancing stream pull
# (spi_arm_stream, sketch/spi_link.cpp, added 2026-07-20). There's no hardware
# ready line telling the MPU when the MCU has re-armed the next chunk (PG13/RDY
# isn't wired to it - docs/progress2.md 4.1), so this is what keeps a read from
# racing ahead of the MCU's re-arm and clocking garbage for that one chunk (the
# whole-frame CRC below still catches it either way - a too-early read is a
# wasted frame, never corruption or a hang - this constant just trades a little
# speed to make that rare instead of routine). Tuned empirically alongside the
# MCU's own re-arm-detection poll (SPI_LINK_DMA_WAIT_TICK_MS, spi_link.cpp).
STREAM_PACING_S = 0.015
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
        """Pull one whole frame via the auto-advancing stream handshake
        (spi_arm_stream): a single RPC call arms the MCU for the whole frame,
        then this reads CHUNK_SIZE-byte pieces back-to-back off spidev, paced
        by STREAM_PACING_S so the MCU's auto-advance (sketch/spi_link.cpp) has
        time to re-arm between reads. Returns (seq, frame_bytes) or None. Does
        not verify CRC (caller does) - a chunk read that raced ahead of the
        MCU's re-arm just produces garbage for that one chunk, caught by the
        whole-frame CRC same as always."""
        reply = None
        for _ in range(ARM_RETRIES):
            try:
                with BRIDGE_LOCK:
                    reply = str(Bridge.call("spi_arm_stream", str(CHUNK_SIZE)))
            except Exception:
                return None
            if reply not in ("busy", "empty"):
                break
            time.sleep(0.005)
        if reply in ("busy", "empty", None):
            with self._lock:
                self.arm_gap += 1
            return None
        parts = reply.split(",")
        if len(parts) != 3:
            return None
        seq, total, clen = int(parts[0]), int(parts[1]), int(parts[2])

        frame = b""
        offset = 0
        while True:
            try:
                data = self._read_socket(clen)
            except OSError:
                return None
            if len(data) != clen:
                return None
            frame += data
            offset += clen
            if offset >= total:
                break
            clen = min(CHUNK_SIZE, total - offset)
            time.sleep(STREAM_PACING_S)
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

        # decoded.bins is schema-driven (every SPECTRUM channel the schema
        # knows about, model-facing or not) -- split off the channels that
        # aren't a SensorChannel (the per-axis accel_x/y/z overlay,
        # docs/CHART_CLUTTER_PLAN.md S1) into display_bins so gate/manager/
        # features keep seeing exactly the model-relevant set in .bins, same
        # as before this frame started carrying extra display channels.
        model_bins, display_bins = {}, {}
        for name, bins in decoded.bins.items():
            try:
                SensorChannel(name)
            except ValueError:
                display_bins[name] = bins
            else:
                model_bins[name] = bins

        # decoded.scalars/.time_series are raw wire ids (telemetry_frame.py's
        # tested contract); resolve to the same friendly names decoded.bins
        # already uses (schema.SCALAR_NAME_BY_ID/CHANNEL_NAME_BY_ID) for
        # SensorFrame's dashboard-facing shape (docs/CHART_CLUTTER_PLAN.md S1).
        # An id with no schema entry is dropped, same as an unmapped
        # channel_id already is for .bins.
        scalars = {schema.SCALAR_NAME_BY_ID[sid]: value
                   for sid, value in decoded.scalars.items()
                   if sid in schema.SCALAR_NAME_BY_ID}
        time_series = {schema.CHANNEL_NAME_BY_ID[cid]: (ts.fs, ts.samples)
                       for cid, ts in decoded.time_series.items()
                       if cid in schema.CHANNEL_NAME_BY_ID}
        # (fs, fft_size) per channel actually present in decoded.bins -- the
        # dashboard's frequency-axis conversion (charts.js) needs this
        # regardless of whether the channel is model- or display-only.
        spectrum_meta = {name: (s.fs, s.fft_size) for name, s in decoded.spectra.items()
                          if name in decoded.bins}

        self._on_frame(SensorFrame(
            node_id=BASE_STATION_NODE_ID,
            source=FrameSource.SPI,
            timestamp=time.time(),
            bins=model_bins,
            display_bins=display_bins,
            scalars=scalars,
            time_series=time_series,
            spectrum_meta=spectrum_meta,
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
