from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Band:
    lower_hz: float
    upper_hz: float

    @property
    def bw_hz(self) -> float:
        return max(0.0, self.upper_hz - self.lower_hz)


def band_iou_1d(a: Band, b: Band) -> float:
    inter_lo = max(a.lower_hz, b.lower_hz)
    inter_hi = min(a.upper_hz, b.upper_hz)
    inter = max(0.0, inter_hi - inter_lo)
    union = max(1e-12, a.bw_hz + b.bw_hz - inter)
    return inter / union
