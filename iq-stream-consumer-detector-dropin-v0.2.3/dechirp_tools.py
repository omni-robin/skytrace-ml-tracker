"""Dechirp utilities for LoRa-ish bursts.

Purpose: provide a technician-friendly "holy shit" visualization.

Given SigMF IQ and detector chirp metrics, we:
- frequency-shift a burst to baseband (relative to capture center)
- dechirp using an estimated chirp slope k (Hz/s)
- show an STFT waterfall where a chirp collapses to a narrow tone

No payload decode; this is a visualization + sanity tool.
"""

from __future__ import annotations

import json
import numpy as np


def load_sigmf_meta(meta_path: str) -> dict:
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_sigmf_params(meta: dict) -> tuple[float, float, int, int]:
    """Return (fs_hz, fc_hz, sample_start, sample_count)."""
    g = meta.get("global", {})
    caps = meta.get("captures", [])
    if not caps:
        raise ValueError("No captures[] in SigMF meta")
    cap0 = caps[0]

    fc = float(cap0["core:frequency"])
    fs = float(g["core:sample_rate"])
    dtype = g.get("core:datatype")
    if dtype != "ci16_le":
        raise ValueError(f"Unsupported core:datatype={dtype} (need ci16_le)")

    sample_start = int(cap0.get("core:sample_start", 0))
    sample_count = int(cap0.get("core:sample_count", 0))
    return fs, fc, sample_start, sample_count


def read_ci16_le_iq(data_path: str, sample_start: int, sample_count: int) -> np.ndarray:
    """Read interleaved i16 IQ -> complex64."""
    offset = sample_start * 4
    nbytes = sample_count * 4
    with open(data_path, "rb") as f:
        f.seek(offset)
        b = f.read(nbytes)
    x = np.frombuffer(b, dtype="<i2")
    x = x[: (len(x) // 2) * 2]
    i = x[0::2].astype(np.float32)
    q = x[1::2].astype(np.float32)
    # Normalize to ~[-1,1]
    return ((i + 1j * q) / 32768.0).astype(np.complex64)


def extract_iq_window(
    iq: np.ndarray,
    fs_hz: float,
    t_start_s: float,
    t_end_s: float,
    pad_s: float = 0.0,
) -> tuple[np.ndarray, float]:
    """Extract IQ window. Returns (iq_win, t0_s) where t0_s is the start time of the window."""
    t0 = max(0.0, t_start_s - pad_s)
    t1 = min((len(iq) / fs_hz), t_end_s + pad_s)
    i0 = int(np.floor(t0 * fs_hz))
    i1 = int(np.ceil(t1 * fs_hz))
    i0 = max(0, min(len(iq), i0))
    i1 = max(0, min(len(iq), i1))
    if i1 <= i0:
        raise ValueError("Empty time window")
    return iq[i0:i1], t0


def freq_shift(iq: np.ndarray, fs_hz: float, f_hz: float) -> np.ndarray:
    """Mix by -f_hz to shift a tone at +f_hz to DC."""
    if f_hz == 0.0:
        return iq
    n = np.arange(len(iq), dtype=np.float64)
    ph = -2.0 * np.pi * f_hz * (n / fs_hz)
    return (iq * np.exp(1j * ph)).astype(np.complex64)


def dechirp(iq: np.ndarray, fs_hz: float, slope_hz_per_s: float) -> np.ndarray:
    """Dechirp a linear chirp with slope k (Hz/s).

    For a baseband chirp exp(j*2π*(f0 t + 0.5*k*t^2)), multiplying by
    exp(-j*2π*(0.5*k*t^2)) removes the chirp term.

    We do: exp(-j*π*k*t^2) where t is in seconds.
    """
    if slope_hz_per_s == 0.0:
        return iq
    t = np.arange(len(iq), dtype=np.float64) / fs_hz
    ph = -np.pi * slope_hz_per_s * (t * t)
    return (iq * np.exp(1j * ph)).astype(np.complex64)


def stft_power_db(iq: np.ndarray, fs_hz: float, nfft: int = 2048, hop: int = 512, window: str = "hann"):
    """Return (t_s, f_hz, p_db) for a centered STFT."""
    nfft = int(nfft)
    hop = int(hop)
    if len(iq) < nfft:
        raise ValueError("Not enough samples for STFT")

    if window == "hann":
        w = np.hanning(nfft).astype(np.float32)
    else:
        w = np.ones(nfft, dtype=np.float32)

    n_frames = 1 + (len(iq) - nfft) // hop
    frames = np.lib.stride_tricks.as_strided(
        iq,
        shape=(n_frames, nfft),
        strides=(iq.strides[0] * hop, iq.strides[0]),
        writeable=False,
    )
    X = np.fft.fftshift(np.fft.fft(frames * w[None, :], n=nfft, axis=1), axes=1)
    p = (np.abs(X) / nfft) ** 2
    p_db = 10.0 * np.log10(np.maximum(p, 1e-20)).astype(np.float32)

    f = np.fft.fftshift(np.fft.fftfreq(nfft, d=1.0 / fs_hz)).astype(np.float32)
    t = (np.arange(n_frames, dtype=np.float32) * hop) / float(fs_hz)
    return t, f, p_db


def _peak_track(ff_hz: np.ndarray, p_db: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (peak_f_hz, peak_db) per time frame.

    p_db is shape [frames, bins] (as returned by stft_power_db).
    """
    idx = np.argmax(p_db, axis=1)
    peak_f = ff_hz[idx]
    peak_db = p_db[np.arange(p_db.shape[0]), idx]
    return peak_f.astype(np.float32), peak_db.astype(np.float32)


def make_shifted_waterfall(
    meta_path: str,
    data_path: str,
    t_start_s: float,
    t_end_s: float,
    capture_center_hz: float,
    target_center_hz: float,
    pad_s: float = 0.05,
    nfft: int = 2048,
    hop: int = 512,
):
    """STFT waterfall after shifting target_center to DC (no dechirp)."""
    meta = load_sigmf_meta(meta_path)
    fs_hz, _fc_hz, sample_start, sample_count = parse_sigmf_params(meta)
    iq = read_ci16_le_iq(data_path, sample_start, sample_count)

    iqw, t0 = extract_iq_window(iq, fs_hz, t_start_s, t_end_s, pad_s=pad_s)

    f_off = float(target_center_hz - capture_center_hz)
    x = freq_shift(iqw, fs_hz, f_off)

    tt, ff, p_db = stft_power_db(x, fs_hz, nfft=nfft, hop=hop, window="hann")
    peak_f, peak_db = _peak_track(ff, p_db)

    return (tt + t0).astype(np.float32), ff.astype(np.float32), p_db, peak_f, peak_db


def make_dechirp_waterfall(
    meta_path: str,
    data_path: str,
    t_start_s: float,
    t_end_s: float,
    capture_center_hz: float,
    target_center_hz: float,
    slope_hz_per_s: float,
    pad_s: float = 0.05,
    nfft: int = 2048,
    hop: int = 512,
):
    """STFT waterfall after shifting target_center to DC and dechirping with slope."""
    meta = load_sigmf_meta(meta_path)
    fs_hz, _fc_hz, sample_start, sample_count = parse_sigmf_params(meta)
    iq = read_ci16_le_iq(data_path, sample_start, sample_count)

    iqw, t0 = extract_iq_window(iq, fs_hz, t_start_s, t_end_s, pad_s=pad_s)

    f_off = float(target_center_hz - capture_center_hz)
    x = freq_shift(iqw, fs_hz, f_off)
    x = dechirp(x, fs_hz, float(slope_hz_per_s))

    tt, ff, p_db = stft_power_db(x, fs_hz, nfft=nfft, hop=hop, window="hann")
    peak_f, peak_db = _peak_track(ff, p_db)

    return (tt + t0).astype(np.float32), ff.astype(np.float32), p_db, peak_f, peak_db
