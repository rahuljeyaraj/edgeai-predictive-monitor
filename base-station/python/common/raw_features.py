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
