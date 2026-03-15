from __future__ import annotations

import json
from pathlib import Path

from .metrics import Band


def load_gt_bands_from_sigmf_meta(meta_path: Path) -> list[Band]:
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
