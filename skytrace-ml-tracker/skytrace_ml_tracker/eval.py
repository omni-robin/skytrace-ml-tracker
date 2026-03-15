from __future__ import annotations

from dataclasses import dataclass

from .metrics import Band, band_iou_1d


@dataclass(frozen=True)
class EdgePctError:
    lower_pct: float
    upper_pct: float

    @property
    def ok(self) -> bool:
        return self.lower_pct <= 0.004 and self.upper_pct <= 0.004


@dataclass
class MatchResult:
    gt: Band
    pred: Band | None
    iou: float
    edge_err: EdgePctError | None


@dataclass
class CaptureEval:
    gt_n: int
    pred_n: int
    matched_n: int
    strict_non_merge_ok: bool
    edge_ok_n: int
    edge_ok_rate: float
    controller_recall: float
    rows: list[MatchResult]


def edge_pct_error(gt: Band, pred: Band) -> EdgePctError:
    bw = gt.bw_hz
    if bw <= 0:
        return EdgePctError(lower_pct=0.0, upper_pct=0.0)
    return EdgePctError(
        lower_pct=abs(pred.lower_hz - gt.lower_hz) / bw,
        upper_pct=abs(pred.upper_hz - gt.upper_hz) / bw,
    )


def greedy_one_to_one_match(gt: list[Band], pred: list[Band], *, min_iou: float = 1e-6) -> list[tuple[int, int, float]]:
    """Greedy 1:1 matching by IoU.

    Returns list of (gt_idx, pred_idx, iou).

    Notes:
    - This is not Hungarian optimal matching, but avoids extra deps.
    - With strict non-merge requirements, greedy is often good enough as a first harness.
    """
    pairs: list[tuple[int, int, float]] = []
    for gi, g in enumerate(gt):
        for pi, p in enumerate(pred):
            iou = band_iou_1d(g, p)
            if iou >= min_iou:
                pairs.append((gi, pi, iou))

    pairs.sort(key=lambda t: t[2], reverse=True)

    used_g: set[int] = set()
    used_p: set[int] = set()
    out: list[tuple[int, int, float]] = []
    for gi, pi, iou in pairs:
        if gi in used_g or pi in used_p:
            continue
        used_g.add(gi)
        used_p.add(pi)
        out.append((gi, pi, float(iou)))

    return out


def eval_capture(
    gt: list[Band],
    pred: list[Band],
    *,
    min_iou: float = 1e-6,
) -> CaptureEval:
    matches = greedy_one_to_one_match(gt, pred, min_iou=min_iou)

    # Build per-GT rows
    pred_by_gt: dict[int, tuple[int, float]] = {gi: (pi, iou) for gi, pi, iou in matches}
    rows: list[MatchResult] = []
    edge_ok_n = 0

    for gi, g in enumerate(gt):
        if gi not in pred_by_gt:
            rows.append(MatchResult(gt=g, pred=None, iou=0.0, edge_err=None))
            continue
        pi, iou = pred_by_gt[gi]
        p = pred[pi]
        ee = edge_pct_error(g, p)
        if ee.ok:
            edge_ok_n += 1
        rows.append(MatchResult(gt=g, pred=p, iou=iou, edge_err=ee))

    matched_n = len(matches)
    gt_n = len(gt)
    pred_n = len(pred)

    # Strict non-merge: enforced by 1:1 matching; what we mean here is:
    #   - every GT has a distinct pred => matched_n == gt_n
    # (This still allows extra predictions; we can track that separately.)
    strict_non_merge_ok = matched_n == gt_n

    controller_recall = (matched_n / gt_n) if gt_n > 0 else 1.0
    edge_ok_rate = (edge_ok_n / gt_n) if gt_n > 0 else 1.0

    return CaptureEval(
        gt_n=gt_n,
        pred_n=pred_n,
        matched_n=matched_n,
        strict_non_merge_ok=bool(strict_non_merge_ok),
        edge_ok_n=edge_ok_n,
        edge_ok_rate=float(edge_ok_rate),
        controller_recall=float(controller_recall),
        rows=rows,
    )
