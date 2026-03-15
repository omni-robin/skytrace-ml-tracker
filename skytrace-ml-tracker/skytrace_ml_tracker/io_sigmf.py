from __future__ import annotations

import json
from pathlib import Path


def load_sigmf_core(meta_path: Path) -> tuple[float, float, int]:
    """Return (sample_rate_hz, center_frequency_hz, sample_count)."""
    j = json.loads(meta_path.read_text())
    cap0 = (j.get("captures") or [{}])[0] or {}
    g = j.get("global") or {}
    sr_hz = float(g.get("core:sample_rate"))
    fc_hz = float(cap0.get("core:frequency"))
    sc = int(cap0.get("core:sample_count"))
    return sr_hz, fc_hz, sc


def read_ci16(path: Path, sample_count: int) -> "object":
    import numpy as np

    raw = np.fromfile(path, dtype=np.int16, count=2 * sample_count)
    if raw.size != 2 * sample_count:
        raise ValueError(f"Short read: expected {2*sample_count} int16, got {raw.size}")
    x = raw.reshape(-1, 2).T.astype(np.float32)
    return x
