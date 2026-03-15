#!/usr/bin/env python3
"""Evaluate ML v0 proposals (TinyCenterBwNet) on a SigMF folder."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skytrace_ml_tracker.eval import eval_capture
from skytrace_ml_tracker.metrics import Band
from skytrace_ml_tracker.sigmf_gt import load_gt_bands_from_sigmf_meta


def infer_pred_bands(*, ckpt: Path, meta: Path, data: Path, min_center_prob: float, min_sep_bins: int, topk: int) -> list[Band]:
    script = Path(__file__).resolve().parents[1] / "scripts" / "infer_centerbw.py"
    cmd = [
        sys.executable,
        str(script),
        "--ckpt",
        str(ckpt),
        "--meta",
        str(meta),
        "--data",
        str(data),
        "--min-center-prob",
        str(min_center_prob),
        "--min-sep-bins",
        str(min_sep_bins),
        "--topk",
        str(topk),
    ]
    p = subprocess.run(cmd, check=True, capture_output=True, text=True)
    out = json.loads(p.stdout)
    bands = out.get("bands") or []
    return [Band(lower_hz=float(b["lower_hz"]), upper_hz=float(b["upper_hz"])) for b in bands]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--in-dir", required=True)
    ap.add_argument("--out", default="artifacts/eval_centerbw.json")
    ap.add_argument("--limit", type=int, default=0)

    ap.add_argument("--min-center-prob", type=float, default=0.5)
    ap.add_argument("--min-sep-bins", type=int, default=16)
    ap.add_argument("--topk", type=int, default=16)
    args = ap.parse_args()

    in_dir = Path(args.in_dir)
    metas = sorted(in_dir.glob("*.sigmf-meta"))
    if args.limit and args.limit > 0:
        metas = metas[: int(args.limit)]

    rows = []
    n_pass_strict = 0
    n_edge_all_ok = 0

    for mp in metas:
        dp = mp.with_suffix(".sigmf-data")
        gt = load_gt_bands_from_sigmf_meta(mp)
        pred = infer_pred_bands(
            ckpt=Path(args.ckpt),
            meta=mp,
            data=dp,
            min_center_prob=float(args.min_center_prob),
            min_sep_bins=int(args.min_sep_bins),
            topk=int(args.topk),
        )

        ev = eval_capture(gt, pred)
        if ev.strict_non_merge_ok:
            n_pass_strict += 1
        if ev.edge_ok_n == ev.gt_n:
            n_edge_all_ok += 1

        rows.append(
            {
                "stem": mp.name.removesuffix(".sigmf-meta"),
                "gt_n": ev.gt_n,
                "pred_n": ev.pred_n,
                "matched_n": ev.matched_n,
                "strict_non_merge_ok": ev.strict_non_merge_ok,
                "edge_ok_rate": ev.edge_ok_rate,
                "controller_recall": ev.controller_recall,
            }
        )

    summary = {
        "in_dir": str(in_dir),
        "ckpt": str(args.ckpt),
        "n": len(rows),
        "pass_strict_non_merge": n_pass_strict,
        "pass_all_edges_ok": n_edge_all_ok,
        "rate_strict_non_merge": (n_pass_strict / len(rows)) if rows else 0.0,
        "rate_all_edges_ok": (n_edge_all_ok / len(rows)) if rows else 0.0,
        "params": {
            "min_center_prob": args.min_center_prob,
            "min_sep_bins": args.min_sep_bins,
            "topk": args.topk,
        },
        "rows": rows,
    }

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(summary, indent=2))
    print(f"n={len(rows)} strict_ok={n_pass_strict} all_edges_ok={n_edge_all_ok}")
    print(f"wrote {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
