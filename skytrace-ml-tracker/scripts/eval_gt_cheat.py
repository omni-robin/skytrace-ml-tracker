#!/usr/bin/env python3
"""Sanity-check evaluator by using GT as predictions.

Expected: strict_non_merge_ok == True and all_edges_ok == True for every capture.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skytrace_ml_tracker.eval import eval_capture
from skytrace_ml_tracker.proposals import proposals_from_gt
from skytrace_ml_tracker.sigmf_gt import load_gt_bands_from_sigmf_meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", required=True)
    ap.add_argument("--out", default="artifacts/eval_gt_cheat.json")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    in_dir = Path(args.in_dir)
    metas = sorted(in_dir.glob("*.sigmf-meta"))
    if args.limit and args.limit > 0:
        metas = metas[: int(args.limit)]

    rows = []
    n_strict = 0
    n_all_edges = 0

    for mp in metas:
        gt = load_gt_bands_from_sigmf_meta(mp)
        props = proposals_from_gt(gt)
        pred = [p.band for p in props]
        ev = eval_capture(gt, pred)
        n_strict += int(ev.strict_non_merge_ok)
        n_all_edges += int(ev.edge_ok_n == ev.gt_n)
        rows.append(
            {
                "stem": mp.name.removesuffix(".sigmf-meta"),
                "gt_n": ev.gt_n,
                "pred_n": ev.pred_n,
                "strict_non_merge_ok": ev.strict_non_merge_ok,
                "edge_ok_rate": ev.edge_ok_rate,
            }
        )

    summary = {
        "in_dir": str(in_dir),
        "n": len(rows),
        "rate_strict": (n_strict / len(rows)) if rows else 0.0,
        "rate_all_edges_ok": (n_all_edges / len(rows)) if rows else 0.0,
        "rows": rows,
    }

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(summary, indent=2))

    print(f"n={len(rows)} strict_ok={n_strict} all_edges_ok={n_all_edges}")
    print(f"wrote {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
