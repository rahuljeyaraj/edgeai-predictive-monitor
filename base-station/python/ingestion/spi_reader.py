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
import fcntl
import logging
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

logger = logging.getLogger(__name__)

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
# Per-call ceiling for the arm RPC. Bridge.call's own default is timeout=10
# (arduino/app_utils/bridge.py) -- a full 10s parked inside BRIDGE_LOCK with
# zero frames pulled, which is LONGER than the dashboard's OFFLINE_AFTER_S
# (frontend/app.js), so one lost RPC reply was enough to flip base_station to
# "Offline" and back. The arm normally answers in well under 150ms (frames
# land at 6-7/s including chunk reads), so 1s is ~7x headroom and bounds a
# lost reply to a blip the 10s staleness rule never sees.
ARM_CALL_TIMEOUT_S = 1
PULL_INTERVAL_S = 0.02

# Cross-process mutual exclusion for who's allowed to actively pull the SPI
# stream. BRIDGE_LOCK (bridge_lock.py) only serializes Bridge.call() within
# ONE process -- it does nothing when a second process (tools/raw_capture.py,
# tools/raw_capture_server.py) opens its own SpiConsumer against the same
# physical Bridge/SPI link main.py's own SpiConsumer is already pulling. That
# used to silently wedge/corrupt both sides' reads (2026-07-22: raw capture's
# live plots showed no data at all while main.py logged nothing but frame-drop
# exceptions -- two independent SpiConsumers racing the same auto-advancing
# arm/stream handshake, invisible from either process alone). flock is used
# instead of a marker file's mere presence because the kernel releases it
# automatically on ANY process exit, including a crash or kill -9 -- no stale
# lock can outlive its holder.
SPI_EXCLUSIVE_LOCK_PATH = "/tmp/spi_consumer_exclusive.lock"


class SpiConsumer:
    """Pulls fuser frames over SPI (chunked) on a background thread, normalizing
    each newly-decoded frame into a SensorFrame and handing it to on_frame --
    the push-model counterpart to MqttSubscriber, feeding PipelineManager the
    same way. Keeps running stats for diagnostics (snapshot())."""

    def __init__(self, on_frame: Callable[[SensorFrame], None],
                 on_decoded: Optional[Callable[[DecodedFrame], None]] = None,
                 exclusive: bool = False):
        self._on_frame = on_frame
        # Optional hook for whatever a decoded frame carried beyond .bins
        # (e.g. .time_series -- see tools/raw_capture.py). Not used by the
        # live pipeline (main.py); SensorFrame.bins is still the only thing
        # PipelineManager.route ever sees.
        self._on_decoded = on_decoded
        # True for the offline raw-capture tools (tools/raw_capture.py,
        # tools/raw_capture_server.py): they hold SPI_EXCLUSIVE_LOCK_PATH for
        # their whole run so main.py's own (default, non-exclusive) SpiConsumer
        # backs off instead of contending with them over the same physical
        # link -- see SPI_EXCLUSIVE_LOCK_PATH's comment above.
        self._exclusive = exclusive
        self._exclusive_lock_fd = None
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
        self.on_frame_errors = 0       # on_frame (PipelineManager.route) raised
        self.paused_for_exclusive = 0  # cycles skipped because another process holds the lock
        self.arm_errors = 0            # arm RPC raised (timeout/transport), not a busy/empty reply

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
                    reply = str(Bridge.call("spi_arm_stream", str(CHUNK_SIZE),
                                            timeout=ARM_CALL_TIMEOUT_S))
            except Exception:
                # Measured 2026-08-20: the router's read loop hits a msgpack
                # decode error ("Unexpected error in read loop: 'utf-8' codec
                # can't decode byte 0x91"), `continue`s past the pending reply,
                # and this call then waits out Bridge.call's whole timeout with
                # BRIDGE_LOCK held -- 10.4s of frozen last_seen, seq jumping
                # 164 unpulled frames on recovery, and the node reading
                # "Offline" the entire time. See ARM_CALL_TIMEOUT_S above.
                #
                # Counted, not silent: this used to be a bare `return None`
                # that incremented nothing (arm_gap below only covers a
                # busy/empty *reply*), so a 10s blackout left every ingest
                # counter looking healthy -- frames_ok simply stopped
                # advancing, which reads identically to "nothing to pull".
                # That blind spot is what made this take three sessions to
                # pin down.
                with self._lock:
                    self.arm_errors += 1
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
        # aren't a SensorChannel (today, just the fused/combined `accel`
        # channel -- accel_x/y/z and mic ARE SensorChannel members and stay
        # in model_bins) into display_bins so gate/manager/features keep
        # seeing exactly the model-relevant set in .bins.
        model_bins, display_bins = {}, {}
        for name, bins in decoded.bins.items():
            try:
                SensorChannel(name)
            except ValueError:
                display_bins[name] = bins
            else:
                model_bins[name] = bins

        # decoded.scalars are raw wire ids (telemetry_frame.py's tested
        # contract); resolve to the same friendly names decoded.bins already
        # uses (schema.SCALAR_NAME_BY_ID) for the SensorFrame shape
        # features.py expects. An id with no schema entry is dropped, same as
        # an unmapped channel_id already is for .bins.
        #
        # decoded.time_series is deliberately NOT carried into SensorFrame:
        # normal-mode firmware doesn't send TIME_SERIES sections at all, and
        # the raw-capture tools that do read them go through on_decoded
        # (DecodedFrame) instead -- see sensor_frame.py's docstring.
        scalars = {schema.SCALAR_NAME_BY_ID[sid]: value
                   for sid, value in decoded.scalars.items()
                   if sid in schema.SCALAR_NAME_BY_ID}
        # (fs, fft_size) per channel actually present in decoded.bins -- the
        # dashboard's frequency-axis conversion (charts.js) needs this
        # regardless of whether the channel is model- or display-only.
        spectrum_meta = {name: (s.fs, s.fft_size) for name, s in decoded.spectra.items()
                          if name in decoded.bins}

        # on_frame (PipelineManager.route) can raise -- e.g. a bin-count
        # mismatch against what this device already committed to
        # (manager.py's _validate_frame_bins). This runs inside _run()'s own
        # dedicated background thread with no other supervisor: an uncaught
        # exception here has previously killed that thread outright,
        # silently ending all SPI ingestion for the rest of the process.
        # Log + drop this frame instead, same as a CRC/decode failure above.
        try:
            self._on_frame(SensorFrame(
                node_id=BASE_STATION_NODE_ID,
                source=FrameSource.SPI,
                timestamp=time.time(),
                bins=model_bins,
                display_bins=display_bins,
                scalars=scalars,
                spectrum_meta=spectrum_meta,
            ))
        except Exception:
            with self._lock:
                self.on_frame_errors += 1
            logger.exception("on_frame failed for node_id=%r -- frame dropped", BASE_STATION_NODE_ID)
        return True

    def _acquire_exclusive_lock(self) -> None:
        fd = open(SPI_EXCLUSIVE_LOCK_PATH, "a+")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fd.close()
            raise RuntimeError(
                f"another exclusive SPI consumer already holds {SPI_EXCLUSIVE_LOCK_PATH} "
                "-- only one raw-capture tool (or main.py) can own the SPI link at a time; "
                "stop the other one first")
        # Held open (never explicitly unlocked/closed) for this object's whole
        # lifetime -- the kernel drops the flock the moment this process exits,
        # however it exits, so there's nothing to clean up on a crash/kill -9.
        self._exclusive_lock_fd = fd

    @staticmethod
    def _exclusive_lock_held_elsewhere() -> bool:
        # Runs every ~20ms while paused (PULL_INTERVAL_S) -- fd must be closed
        # every call or this leaks a descriptor per cycle.
        try:
            fd = open(SPI_EXCLUSIVE_LOCK_PATH, "a+")
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            fd.close()

    def _run(self):
        while True:
            if not self._exclusive and self._exclusive_lock_held_elsewhere():
                with self._lock:
                    self.paused_for_exclusive += 1
                time.sleep(PULL_INTERVAL_S)
                continue
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
        if self._exclusive:
            self._acquire_exclusive_lock()
        self._thread = threading.Thread(target=self._run, name="spi-consumer", daemon=True)
        self._thread.start()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(seq=self.last_seq, bins=self.last_bins, meta=self.last_meta,
                        frames_ok=self.frames_ok, frames_dup=self.frames_dup,
                        frames_dropped=self.frames_dropped, crc_fail=self.crc_fail,
                        arm_gap=self.arm_gap, arm_errors=self.arm_errors,
                        on_frame_errors=self.on_frame_errors,
                        paused_for_exclusive=self.paused_for_exclusive)
