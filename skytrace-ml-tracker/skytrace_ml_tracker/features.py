from __future__ import annotations

import numpy as np


def normalize_feature(x: np.ndarray) -> np.ndarray:
    """Robust per-sample normalization for log-PSD features."""
    x = x.astype(np.float32, copy=False)
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med)))
    scale = max(1e-6, 1.4826 * mad)  # MAD -> ~std
    return (x - med) / scale


def logpsd_fftshift(x_iq: np.ndarray, *, nfft: int = 2048, hop: int = 1024, eps: float = 1e-12) -> np.ndarray:
    """Compute log-PSD for a 2xL IQ window.

    Returns fftshifted bins length nfft.

    This mirrors the feature frontend used in SkytraceRT_poc (hanning + fftshift).
    """
    assert x_iq.ndim == 2 and x_iq.shape[0] == 2
    iq = x_iq[0] + 1j * x_iq[1]
    L = int(iq.shape[0])

    if L < nfft:
        iq = np.pad(iq, (0, nfft - L))
        L = int(iq.shape[0])

    win = np.hanning(nfft).astype(np.float32)
    acc = None
    nseg = 0
    for start in range(0, L - nfft + 1, hop):
        seg = iq[start : start + nfft] * win
        X = np.fft.fftshift(np.fft.fft(seg, nfft))
        p = (np.abs(X) ** 2).astype(np.float64)
        acc = p if acc is None else (acc + p)
        nseg += 1

    psd = (acc / max(nseg, 1)).astype(np.float32)
    return np.log(psd + eps)
