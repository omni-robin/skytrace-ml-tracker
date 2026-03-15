#!/usr/bin/env python3
"""Evaluate SkytraceRT_poc predictions against strict no-merge + ±0.4% BW edge gates.

This is an *experiment harness* for the new project. We reuse the existing
SkytraceRT_poc feature model inference as a baseline predictor.

Example:
  cd skytrace-ml-tracker
  python scripts/eval_skytracert_poc_folder.py \
    --ckpt ../SkytraceRT_poc/artifacts/tiny_feat_occ_subset100_hz.pt \
    --in-dir ../gcs_capture_data_config_info/subset100 \
    --out artifacts/eval_baseline_subset100.json \
    --limit 50

You can also pass through splitting parameters (still baseline postprocess).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skytrace_ml_tracker.eval import eval_capture
from skytrace_ml_tracker.metrics import Band


def load_gt_bands(meta_path: Path) -> list[Band]:
    j = json.loads(meta_path.read_text())
    rc = (
        j.get("global", {})
        .get("annotations", {})
        .get("custom", {})
        .get("rc_configuration", {})
    )
    out: list[Band] = []
    for r in (rc.get("rcs") or []):
        out.append(
            Band(
                lower_hz=float(r["min_frequency_mhz"]) * 1e6,
                upper_hz=float(r["max_frequency_mhz"]) * 1e6,
            )
        )
    return out


def infer_pred_bands(
    *,
    ckpt: Path,
    meta: Path,
    data: Path,
    thr: float,
    hysteresis: float,
    smooth_radius: int,
    min_bins: int,
    merge_gap_bins: int,
    max_bands: int,
    split: bool,
    split_min_peak_height: float | None,
    split_min_peak_sep_bins: int,
    split_min_valley_drop: float,
) -> list[Band]:
    script = Path(__file__).resolve().parents[2] / "SkytraceRT_poc" / "scripts" / "infer_feat_occ.py"
    if not script.exists():
        raise FileNotFoundError(f"Could not find infer script: {script}")

    cmd = [
        sys.executable,
        str(script),
        "--ckpt",
        str(ckpt),
        "--meta",
        str(meta),
        "--data",
        str(data),
        "--thr",
        str(thr),
        "--hysteresis",
        str(hysteresis),
        "--smooth-radius",
        str(smooth_radius),
        "--min-bins",
        str(min_bins),
        "--merge-gap-bins",
        str(merge_gap_bins),
        "--max-bands",
        str(max_bands),
    ]

    if split:
        cmd.append("--split")
        if split_min_peak_height is not None:
            cmd += ["--split-min-peak-height", str(split_min_peak_height)]
        cmd += ["--split-min-peak-sep-bins", str(split_min_peak_sep_bins)]
        cmd += ["--split-min-valley-drop", str(split_min_valley_drop)]

    p = subprocess.run(cmd, check=True, capture_output=True, text=True)
    out = json.loads(p.stdout)
    bands = out.get("bands") or []
    return [Band(lower_hz=float(b["lower_hz"]), upper_hz=float(b["upper_hz"])) for b in bands]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--in-dir", required=True)
    ap.add_argument("--out", default="artifacts/eval_baseline.json")
    ap.add_argument("--limit", type=int, default=0)

    ap.add_argument("--thr", type=float, default=0.5)
    ap.add_argument("--hysteresis", type=float, default=0.05)
    ap.add_argument("--smooth-radius", type=int, default=1)
    ap.add_argument("--min-bins", type=int, default=3)
    ap.add_argument("--merge-gap-bins", type=int, default=0)
    ap.add_argument("--max-bands", type=int, default=16)

    ap.add_argument("--split", action="store_true")
    ap.add_argument("--split-min-peak-height", type=float, default=None)
    ap.add_argument("--split-min-peak-sep-bins", type=int, default=16)
    ap.add_argument("--split-min-valley-drop", type=float, default=0.005)

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
        gt = load_gt_bands(mp)
        pred = infer_pred_bands(
            ckpt=Path(args.ckpt),
            meta=mp,
            data=dp,
            thr=args.thr,
            hysteresis=args.hysteresis,
            smooth_radius=args.smooth_radius,
            min_bins=args.min_bins,
            merge_gap_bins=args.merge_gap_bins,
            max_bands=args.max_bands,
            split=bool(args.split),
            split_min_peak_height=args.split_min_peak_height,
            split_min_peak_sep_bins=int(args.split_min_peak_sep_bins),
            split_min_valley_drop=float(args.split_min_valley_drop),
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
                "matches": [
                    {
                        "gt": {"lower_hz": r.gt.lower_hz, "upper_hz": r.gt.upper_hz},
                        "pred": None
                        if r.pred is None
                        else {"lower_hz": r.pred.lower_hz, "upper_hz": r.pred.upper_hz},
                        "iou": r.iou,
                        "edge_err_pct": None
                        if r.edge_err is None
                        else {"lower": r.edge_err.lower_pct, "upper": r.edge_err.upper_pct, "ok": r.edge_err.ok},
                    }
                    for r in ev.rows
                ],
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
            "thr": args.thr,
            "hysteresis": args.hysteresis,
            "smooth_radius": args.smooth_radius,
            "min_bins": args.min_bins,
            "merge_gap_bins": args.merge_gap_bins,
            "max_bands": args.max_bands,
            "split": bool(args.split),
            "split_min_peak_height": args.split_min_peak_height,
            "split_min_peak_sep_bins": args.split_min_peak_sep_bins,
            "split_min_valley_drop": args.split_min_valley_drop,
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
