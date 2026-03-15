#!/usr/bin/env python3
"""Build a small training NPZ from SigMF + GT controller bands.

Creates dense targets suitable for TinyCenterBwNet:
- y_center: [N,F] with Gaussians at GT centers
- y_bw: [N,F] bandwidth (in bins) at GT center bins, else 0
- y_mask: [N,F] 1 at GT center bins, else 0 (for masking bw loss)

We keep this simple and fast to iterate.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skytrace_ml_tracker.features import logpsd_fftshift, normalize_feature
from skytrace_ml_tracker.io_sigmf import load_sigmf_core, read_ci16
from skytrace_ml_tracker.sigmf_gt import load_gt_bands_from_sigmf_meta


def hz_to_bin(freq_hz: float, *, fc_hz: float, sr_hz: float, nfft: int) -> float:
    # fftshift bins cover [-sr/2, sr/2) with nfft bins
    f_bb = freq_hz - fc_hz
    x = (f_bb + sr_hz / 2.0) / sr_hz  # 0..1
    return x * nfft


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", required=True)
    ap.add_argument("--out", default="artifacts/train_subset.npz")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--win-len", type=int, default=262_144)
    ap.add_argument("--win-hop", type=int, default=262_144)
    ap.add_argument("--nfft", type=int, default=2048)
    ap.add_argument("--fft-hop", type=int, default=1024)
    ap.add_argument("--center-sigma-bins", type=float, default=2.0)
    args = ap.parse_args()

    in_dir = Path(args.in_dir)
    metas = sorted(in_dir.glob("*.sigmf-meta"))
    if args.limit and args.limit > 0:
        metas = metas[: int(args.limit)]

    Xs = []
    yC = []
    yBW = []
    yM = []

    for mp in metas:
        dp = mp.with_suffix(".sigmf-data")
        sr_hz, fc_hz, sc = load_sigmf_core(mp)
        x = read_ci16(dp, sc)

        # build a single averaged feature vector per capture by averaging windows
        L = x.shape[1]
        win_len = min(int(args.win_len), L)
        hop = min(int(args.win_hop), win_len)

        feats = []
        for start in range(0, L - win_len + 1, hop):
            w = x[:, start : start + win_len]
            feats.append(logpsd_fftshift(w, nfft=int(args.nfft), hop=int(args.fft_hop)))
        feat = np.mean(np.stack(feats, axis=0), axis=0).astype(np.float32)
        feat = normalize_feature(feat)

        F = feat.shape[0]
        assert F == int(args.nfft)

        gt = load_gt_bands_from_sigmf_meta(mp)

        yc = np.zeros((F,), dtype=np.float32)
        ybw = np.zeros((F,), dtype=np.float32)
        ym = np.zeros((F,), dtype=np.float32)

        for b in gt:
            c_hz = 0.5 * (b.lower_hz + b.upper_hz)
            bw_hz = max(0.0, b.upper_hz - b.lower_hz)
            c_bin = hz_to_bin(c_hz, fc_hz=fc_hz, sr_hz=sr_hz, nfft=F)
            bw_bins = (bw_hz / sr_hz) * F

            ci = int(np.clip(round(c_bin), 0, F - 1))
            ym[ci] = 1.0
            ybw[ci] = float(bw_bins)

            sigma = float(args.center_sigma_bins)
            # gaussian bump
            for i in range(max(0, int(ci - 6 * sigma)), min(F, int(ci + 6 * sigma) + 1)):
                d = (i - c_bin) / sigma
                yc[i] = max(yc[i], float(math.exp(-0.5 * d * d)))

        Xs.append(feat)
        yC.append(yc)
        yBW.append(ybw)
        yM.append(ym)

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        outp,
        X=np.stack(Xs, axis=0).astype(np.float16),
        y_center=np.stack(yC, axis=0).astype(np.float32),
        y_bw=np.stack(yBW, axis=0).astype(np.float32),
        y_mask=np.stack(yM, axis=0).astype(np.float32),
    )
    print(f"wrote {outp} N={len(Xs)} F={Xs[0].shape[0] if Xs else 'NA'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
