#!/usr/bin/env python3
"""Train TinyCenterBwNet on dense center/bw targets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skytrace_ml_tracker.model_centerbw import TinyCenterBwNet


def pick_device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True)
    ap.add_argument("--out", default="artifacts/tiny_centerbw.pt")
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--bw-loss-weight", type=float, default=1.0)
    args = ap.parse_args()

    data = np.load(args.npz)
    X = data["X"].astype(np.float32)
    y_center = data["y_center"].astype(np.float32)
    y_bw = data["y_bw"].astype(np.float32)
    y_mask = data["y_mask"].astype(np.float32)

    N, Fbins = X.shape

    device = pick_device()
    model = TinyCenterBwNet(F=Fbins, width=32).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=float(args.lr))

    def batches():
        idx = np.random.permutation(N)
        for s in range(0, N, int(args.batch)):
            j = idx[s : s + int(args.batch)]
            yield (
                torch.from_numpy(X[j]).to(device),
                torch.from_numpy(y_center[j]).to(device),
                torch.from_numpy(y_bw[j]).to(device),
                torch.from_numpy(y_mask[j]).to(device),
            )

    for ep in range(int(args.epochs)):
        model.train()
        losses = []
        for xb, yc, ybw, ym in batches():
            opt.zero_grad(set_to_none=True)
            logits, bw_log1p_hat = model(xb)

            # center loss: BCE with logits on soft gaussian targets
            loss_center = F.binary_cross_entropy_with_logits(logits, yc)

            # bw loss: L1 on log1p(bw_bins), only at center bins
            bw_t = torch.log1p(ybw)
            denom = ym.sum().clamp(min=1.0)
            loss_bw = (torch.abs(bw_log1p_hat - bw_t) * ym).sum() / denom

            loss = loss_center + float(args.bw_loss_weight) * loss_bw
            loss.backward()
            opt.step()
            losses.append(float(loss.detach().cpu().item()))

        print(f"epoch {ep+1}/{args.epochs} loss={np.mean(losses):.4f}")

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    ckpt = {
        "model": {"F": int(Fbins), "width": 32},
        "state_dict": model.state_dict(),
    }
    torch.save(ckpt, outp)
    print(f"wrote {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
