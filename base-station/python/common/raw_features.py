"""FFT/scalar feature helpers for raw (FUSER_RAW_CAPTURE_MODE) time-domain
windows -- shared by tools/raw_capture_server.py's live preview and anything
else that wants the exact math tools/offline_experiment.py's own copies use,
without needing that file's autoencoder/torch dependencies.

Kept as a standalone copy rather than an import from offline_experiment.py:
that file already has other work in flight and this module has no reason to
carry its sweep/CLI/plotting code along for the ride.
"""
import numpy as np


def fft_magnitude(window: np.ndarray) -> np.ndarray:
    """Same convention as the firmware's *_fft_magnitude(): DC (bin 0)
    dropped, bins 1..N/2 kept -- N/2 bins total for an N-sample window."""
    return np.abs(np.fft.rfft(window))[1:]


def mic_useful_magnitude(mag: np.ndarray) -> np.ndarray:
    """Mic-only trim: keep just the first half of fft_magnitude()'s unique
    bins (== the first quarter of the full FFT length). Without an external
    MCLK the INMP441's own natural rate is Fs/2, upsampled 2x to Fs -- that
    folds an aliasing image above Fs/4, so only the bins below it are real
    audio (mic_sampler.cpp's MIC_FFT_BIN_COUNT = MIC_FFT_LEN/4, same
    reasoning/numbers). Call this on mic_raw's fft_magnitude() output before
    downsample() -- accel has no such restriction, don't apply this to it."""
    return mag[: len(mag) // 2]


def downsample(mag: np.ndarray, bin_count: int) -> np.ndarray:
    """Average-pool down to bin_count buckets, same scheme as the firmware's
    accel_spectrum_downsample()/get_mic_spectrum(). len(mag) must divide
    evenly by bin_count."""
    if len(mag) % bin_count != 0:
        raise ValueError(f"{len(mag)} FFT bins doesn't divide evenly by "
                          f"bin_count={bin_count} (divisors of {len(mag)}: "
                          f"{[d for d in range(1, len(mag) + 1) if len(mag) % d == 0]})")
    factor = len(mag) // bin_count
    return mag.reshape(bin_count, factor).mean(axis=1)


def peak_normalize(bins: np.ndarray) -> np.ndarray:
    """Rescale to this block's own peak so absolute amplitude (motor load,
    placement, mic gain) doesn't swamp the shape a downstream model needs to
    learn -- same as pipeline/features.py's normalize_bins."""
    peak = bins.max()
    if peak <= 0:
        return np.zeros_like(bins)
    return bins / peak


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2)))


def kurtosis(x: np.ndarray) -> float:
    std = x.std()
    if std <= 0:
        return 0.0
    return float(np.mean(((x - x.mean()) / std) ** 4) - 3.0)  # excess kurtosis


def std(x: np.ndarray) -> float:
    return float(x.std())  # population std (ddof=0), matches fuser.cpp's compute_scalars


def peak(x: np.ndarray) -> float:
    return float(x.max())


def crest_factor(x: np.ndarray) -> float:
    r = rms(x)
    return float(x.max() / r) if r > 0 else 0.0


def skewness(x: np.ndarray) -> float:
    s = x.std()
    if s <= 0:
        return 0.0
    return float(np.mean(((x - x.mean()) / s) ** 3))


def vector_magnitude(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Combined tri-axial magnitude, sample-by-sample -- the same "overall
    vibration" signal fuser.cpp's compute_scalars() derives its own on-device
    scalar tiles from (normal mode only; this is that math's raw-mode twin)."""
    return np.sqrt(x ** 2 + y ** 2 + z ** 2)
