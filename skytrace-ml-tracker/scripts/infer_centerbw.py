#!/usr/bin/env python3
"""Infer controller band proposals with TinyCenterBwNet.

This is ML v0:
- compute log-PSD feature vector per capture
- predict center logits + bw per bin
- pick top peaks in center prob
- convert (center, bw_bins) to absolute-Hz bands

This is intentionally simple; tracking comes later.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skytrace_ml_tracker.features import logpsd_fftshift, normalize_feature
from skytrace_ml_tracker.io_sigmf import load_sigmf_core, read_ci16
from skytrace_ml_tracker.model_centerbw import TinyCenterBwNet


def pick_device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def _pick_peaks(x: np.ndarray, *, min_height: float, min_sep_bins: int, topk: int) -> list[int]:
    n = int(x.shape[0])
    cand = []
    for i in range(1, n - 1):
        if x[i] >= min_height and x[i] >= x[i - 1] and x[i] >= x[i + 1]:
            cand.append(i)
    cand.sort(key=lambda i: float(x[i]), reverse=True)
    picked = []
    for i in cand:
        if all(abs(i - j) >= min_sep_bins for j in picked):
            picked.append(i)
        if len(picked) >= topk:
            break
    picked.sort()
    return picked


def bins_to_hz(bin_idx: float, *, fc_hz: float, sr_hz: float, nfft: int) -> float:
    # inverse of hz_to_bin
    x = bin_idx / float(nfft)
    f_bb = x * sr_hz - sr_hz / 2.0
    return fc_hz + f_bb


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--data", required=True)

    ap.add_argument("--win-len", type=int, default=262_144)
    ap.add_argument("--win-hop", type=int, default=262_144)
    ap.add_argument("--nfft", type=int, default=2048)
    ap.add_argument("--fft-hop", type=int, default=1024)

    ap.add_argument("--min-center-prob", type=float, default=0.5)
    ap.add_argument("--min-sep-bins", type=int, default=16)
    ap.add_argument("--topk", type=int, default=16)
    ap.add_argument("--min-bw-bins", type=float, default=3.0)

    args = ap.parse_args()

    device = pick_device()

    ckpt = torch.load(args.ckpt, map_location="cpu")
    cfg = ckpt["model"]
    Fbins = int(cfg["F"])

    model = TinyCenterBwNet(F=Fbins, width=int(cfg.get("width", 32))).to(device)
    model.load_state_dict(ckpt["state_dict"], strict=True)
    model.eval()

    sr_hz, fc_hz, sc = load_sigmf_core(Path(args.meta))
    x = read_ci16(Path(args.data), sc)

    L = x.shape[1]
    win_len = min(int(args.win_len), L)
    hop = min(int(args.win_hop), win_len)

    feats = []
    for start in range(0, L - win_len + 1, hop):
        w = x[:, start : start + win_len]
        feats.append(logpsd_fftshift(w, nfft=int(args.nfft), hop=int(args.fft_hop)))
    feat = np.mean(np.stack(feats, axis=0), axis=0).astype(np.float32)
    feat = normalize_feature(feat)

    # ensure length matches model
    if feat.shape[0] != Fbins:
        x_old = np.linspace(0.0, 1.0, feat.shape[0], dtype=np.float32)
        x_new = np.linspace(0.0, 1.0, Fbins, dtype=np.float32)
        feat = np.interp(x_new, x_old, feat).astype(np.float32)

    xt = torch.from_numpy(feat[None, :]).to(device)
    with torch.no_grad(), torch.autocast(device_type=device.type, dtype=torch.float16, enabled=device.type in ("mps", "cuda")):
        logits, bw_log1p = model(xt)
        prob = torch.sigmoid(logits).float().cpu().numpy()[0]
        bw_bins = (torch.expm1(bw_log1p)).float().cpu().numpy()[0]

    peaks = _pick_peaks(prob, min_height=float(args.min_center_prob), min_sep_bins=int(args.min_sep_bins), topk=int(args.topk))

    bands = []
    for ci in peaks:
        bw = float(max(float(args.min_bw_bins), bw_bins[ci]))
        lo_bin = float(ci) - 0.5 * bw
        hi_bin = float(ci) + 0.5 * bw
        lower_hz = bins_to_hz(lo_bin, fc_hz=fc_hz, sr_hz=sr_hz, nfft=Fbins)
        upper_hz = bins_to_hz(hi_bin, fc_hz=fc_hz, sr_hz=sr_hz, nfft=Fbins)
        if upper_hz <= lower_hz:
            continue
        bands.append({"lower_hz": float(lower_hz), "upper_hz": float(upper_hz), "score": float(prob[ci])})

    out = {
        "capture": {"center_frequency_hz": fc_hz, "sample_rate_hz": sr_hz, "sample_count": sc},
        "bands": bands,
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
