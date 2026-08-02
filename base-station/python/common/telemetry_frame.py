"""
Generic sensor telemetry frame codec -- the self-describing section-list
payload from docs/SENSOR_TELEMETRY_FRAME_PLAN.md S3 (Phase A).

This is the *payload* format, independent of any transport envelope: the SPI
link (spi_link.cpp) wraps these bytes with magic/seq/CRC and the MPU pulls
them chunked (ingestion/spi_reader.py); a satellite MQTT node (T8, not built
yet) will publish these same bytes as the message body with no extra wrapper.

Wire layout (little-endian throughout):

    [num_sections u8]
    repeated num_sections times:
      [source_id u8][channel_id u8][data_kind u8][section_len u16][body...]

  section_len is the body length in bytes, so a reader can skip a data_kind it
  doesn't recognize by length instead of failing -- that's what makes the MPU
  side need zero code changes when the MCU starts sending a new channel/kind.

Bodies by data_kind (ids from telemetry_schema.DATA_KIND):
  SPECTRUM     : fs f32, fft_size u16, bin_count u16, bins[bin_count] f32
  SCALAR_SET   : count u8, ids[count] u16, values[count] f32
  TIME_SERIES  : fs f32, sample_count u16, samples[sample_count] f32
                 (in the enum for completeness; nothing emits it yet)

SPECTRUM frequency convention -- fs / fft_size is one *wire* bin's width, so
fft_size is NOT the sender's native FFT length whenever it pools bins down
before sending (fuser.cpp divides it by the pooling factor to keep this
identity true). Senders also discard DC, so bin k covers the band

    (k * fs / fft_size, (k + 1) * fs / fft_size]

-- there is no 0 Hz bin on the wire, and k * fs / fft_size is a bin's lower
edge, not its frequency. Reading it as the frequency put the first bin at
0 Hz and the whole axis one bin low, a real error against a tone generator
(2026-08-02); charts.js binFreqsFor() plots the band centre instead.

The semantic meaning of a source_id/channel_id lives only in the schema
(telemetry_schema.py, generated from telemetry_schema.json) -- this codec
carries opaque numbers. decode_frame() resolves channel_id -> SensorChannel
value string via CHANNEL_NAME_BY_ID so a spectrum lands in SensorFrame.bins
under the key features.py/registry already expect.

Zero-fill (plan S4): a channel a node structurally lacks is sent as a present
SPECTRUM section with real bin_count and all-zero values (NOT an omitted
section, and NOT bin_count=0). That's just data here -- the zeros flow through
to SensorFrame.bins and the model learns the slot is always zero. bin_count=0
is the distinct "channel not part of this node at all" case and contributes no
key to bins.
"""
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from wire_protocol import ChannelSpectrum
import telemetry_schema as schema

# --- section header: source_id, channel_id, data_kind, section_len ----------
_SECTION_HEADER_FMT = "<BBBH"
_SECTION_HEADER_LEN = struct.calcsize(_SECTION_HEADER_FMT)   # 5

_SPECTRUM_HEAD_FMT = "<fHH"     # fs, fft_size, bin_count
_SPECTRUM_HEAD_LEN = struct.calcsize(_SPECTRUM_HEAD_FMT)     # 8
_TIMESERIES_HEAD_FMT = "<fH"   # fs, sample_count
_TIMESERIES_HEAD_LEN = struct.calcsize(_TIMESERIES_HEAD_FMT)  # 6

_KIND_SPECTRUM = schema.DATA_KIND["SPECTRUM"]
_KIND_TIME_SERIES = schema.DATA_KIND["TIME_SERIES"]
_KIND_SCALAR_SET = schema.DATA_KIND["SCALAR_SET"]


class MalformedFrameError(ValueError):
    """A frame that can't be parsed at all (truncated header, a section that
    runs past the buffer, a body shorter than its declared kind needs).
    Distinct from an unrecognized-but-well-formed section, which is skipped."""


@dataclass
class TimeSeries:
    fs: float
    samples: Tuple[float, ...]


@dataclass
class DecodedFrame:
    """Everything one telemetry frame carried, resolved against the schema.

    `bins` is the SensorFrame-ready view (channel name -> spectrum bins) so
    ingestion can hand it straight to SensorFrame; a section whose channel_id
    isn't in the schema, or whose bin_count is 0, contributes no key.
    `scalars`/`time_series` are decoded but not yet consumed downstream
    (no SensorChannel/input_dim wired for them) -- kept so they're not lost.
    """
    bins: Dict[str, Tuple[float, ...]] = field(default_factory=dict)
    spectra: Dict[str, ChannelSpectrum] = field(default_factory=dict)
    scalars: Dict[int, float] = field(default_factory=dict)       # scalar_id -> value
    time_series: Dict[int, TimeSeries] = field(default_factory=dict)  # channel_id -> series
    unknown_sections: int = 0     # well-formed sections skipped (unknown kind)


# --- encode (used by tests, and by the satellite sim once T8 lands) ---------

def encode_spectrum_body(fs: float, fft_size: int, bins: Tuple[float, ...]) -> bytes:
    return struct.pack(_SPECTRUM_HEAD_FMT, fs, fft_size, len(bins)) + \
        struct.pack(f"<{len(bins)}f", *bins)


def encode_scalar_body(values: Dict[int, float]) -> bytes:
    ids = sorted(values)
    body = struct.pack("<B", len(ids))
    body += struct.pack(f"<{len(ids)}H", *ids)
    body += struct.pack(f"<{len(ids)}f", *(values[i] for i in ids))
    return body


def encode_timeseries_body(fs: float, samples: Tuple[float, ...]) -> bytes:
    return struct.pack(_TIMESERIES_HEAD_FMT, fs, len(samples)) + \
        struct.pack(f"<{len(samples)}f", *samples)


def encode_section(source_id: int, channel_id: int, data_kind: int, body: bytes) -> bytes:
    if len(body) > 0xFFFF:
        raise ValueError(f"section body {len(body)} B exceeds u16 section_len")
    return struct.pack(_SECTION_HEADER_FMT, source_id, channel_id, data_kind, len(body)) + body


def encode_frame(sections: List[bytes]) -> bytes:
    """sections: the output of encode_section() calls, in wire order."""
    if len(sections) > 0xFF:
        raise ValueError(f"{len(sections)} sections exceeds u8 num_sections")
    return struct.pack("<B", len(sections)) + b"".join(sections)


def encode_spectrum_frame(source_id: int,
                          channel_bins: List[Tuple[int, float, int, Tuple[float, ...]]]) -> bytes:
    """Convenience for the common all-SPECTRUM frame: channel_bins is a list of
    (channel_id, fs, fft_size, bins). Sections are emitted in the given order."""
    sections = [
        encode_section(source_id, cid, _KIND_SPECTRUM, encode_spectrum_body(fs, fft, bins))
        for (cid, fs, fft, bins) in channel_bins
    ]
    return encode_frame(sections)


# --- decode -----------------------------------------------------------------

def decode_frame(payload: bytes) -> DecodedFrame:
    """Parse a section-list payload. Iterates by (data_kind, section_len),
    dispatching known kinds to a decoder and skipping unknown kinds by length.
    Raises MalformedFrameError only for structural corruption (a section that
    doesn't fit, a recognized body too short for its kind)."""
    if len(payload) < 1:
        raise MalformedFrameError("empty payload (no num_sections byte)")
    (num_sections,) = struct.unpack_from("<B", payload, 0)
    off = 1

    out = DecodedFrame()
    for _ in range(num_sections):
        if off + _SECTION_HEADER_LEN > len(payload):
            raise MalformedFrameError(
                f"section header at offset {off} runs past payload ({len(payload)} B)")
        source_id, channel_id, data_kind, section_len = struct.unpack_from(
            _SECTION_HEADER_FMT, payload, off)
        off += _SECTION_HEADER_LEN
        body_end = off + section_len
        if body_end > len(payload):
            raise MalformedFrameError(
                f"section body (channel {channel_id}, {section_len} B) at offset {off} "
                f"runs past payload ({len(payload)} B)")
        body = payload[off:body_end]
        off = body_end

        if data_kind == _KIND_SPECTRUM:
            _decode_spectrum(out, channel_id, body)
        elif data_kind == _KIND_SCALAR_SET:
            _decode_scalar_set(out, body)
        elif data_kind == _KIND_TIME_SERIES:
            _decode_time_series(out, channel_id, body)
        else:
            out.unknown_sections += 1   # well-formed but unrecognized: skip by len

    return out


def _decode_spectrum(out: DecodedFrame, channel_id: int, body: bytes) -> None:
    if len(body) < _SPECTRUM_HEAD_LEN:
        raise MalformedFrameError(f"SPECTRUM body {len(body)} B shorter than header")
    fs, fft_size, bin_count = struct.unpack_from(_SPECTRUM_HEAD_FMT, body, 0)
    need = _SPECTRUM_HEAD_LEN + bin_count * 4
    if len(body) < need:
        raise MalformedFrameError(
            f"SPECTRUM body {len(body)} B too short for {bin_count} bins (need {need})")
    bins = struct.unpack_from(f"<{bin_count}f", body, _SPECTRUM_HEAD_LEN)
    spectrum = ChannelSpectrum(fs=fs, fft_size=fft_size, bins=bins)

    name = schema.CHANNEL_NAME_BY_ID.get(channel_id)
    if name is None:
        # No schema mapping for this channel_id -- keep the spectrum under a
        # synthetic key so nothing silently vanishes, but it won't match a
        # SensorChannel so it never enters the model vector.
        out.unknown_sections += 1
        return
    out.spectra[name] = spectrum
    # bin_count==0 is the "channel not part of this node" case (plan S4) --
    # contributes no bins key. An all-zero but present spectrum (zero-fill)
    # has bin_count>0 and does land here, exactly as intended.
    if bin_count > 0:
        out.bins[name] = bins


def _decode_scalar_set(out: DecodedFrame, body: bytes) -> None:
    if len(body) < 1:
        raise MalformedFrameError("SCALAR_SET body missing count byte")
    (count,) = struct.unpack_from("<B", body, 0)
    need = 1 + count * 2 + count * 4
    if len(body) < need:
        raise MalformedFrameError(
            f"SCALAR_SET body {len(body)} B too short for {count} scalars (need {need})")
    ids = struct.unpack_from(f"<{count}H", body, 1)
    values = struct.unpack_from(f"<{count}f", body, 1 + count * 2)
    for sid, val in zip(ids, values):
        out.scalars[sid] = val


def _decode_time_series(out: DecodedFrame, channel_id: int, body: bytes) -> None:
    if len(body) < _TIMESERIES_HEAD_LEN:
        raise MalformedFrameError(f"TIME_SERIES body {len(body)} B shorter than header")
    fs, sample_count = struct.unpack_from(_TIMESERIES_HEAD_FMT, body, 0)
    need = _TIMESERIES_HEAD_LEN + sample_count * 4
    if len(body) < need:
        raise MalformedFrameError(
            f"TIME_SERIES body {len(body)} B too short for {sample_count} samples (need {need})")
    samples = struct.unpack_from(f"<{sample_count}f", body, _TIMESERIES_HEAD_LEN)
    out.time_series[channel_id] = TimeSeries(fs=fs, samples=samples)
