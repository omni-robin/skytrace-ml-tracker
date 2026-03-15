from __future__ import annotations

from dataclasses import dataclass

from .metrics import Band


@dataclass(frozen=True)
class Proposal:
    band: Band
    score: float


def proposals_from_gt(gt: list[Band]) -> list[Proposal]:
    """Cheating baseline: use ground-truth bands as perfect proposals.

    This exists to validate that the evaluator harness + edge gates behave as expected.
    """
    return [Proposal(band=g, score=1.0) for g in gt]
