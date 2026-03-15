from __future__ import annotations

import torch
import torch.nn as nn


class TinyCenterBwNet(nn.Module):
    """Tiny 1D conv model over log-PSD.

    Outputs:
      - center_logits: [B, F]
      - bw_hat: [B, F] (bandwidth in bins, only trained at center targets)

    Idea: multi-controller separation comes from predicting multiple peaks in center_logits.
    """

    def __init__(self, F: int, width: int = 32):
        super().__init__()
        self.F = int(F)
        self.width = int(width)

        self.net = nn.Sequential(
            nn.Conv1d(1, width, kernel_size=9, padding=4),
            nn.ReLU(),
            nn.Conv1d(width, width, kernel_size=9, padding=4),
            nn.ReLU(),
            nn.Conv1d(width, width, kernel_size=9, padding=4),
            nn.ReLU(),
        )
        self.head_center = nn.Conv1d(width, 1, kernel_size=1)
        self.head_bw = nn.Conv1d(width, 1, kernel_size=1)  # predicts log1p(bw_bins)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        # x: [B, F]
        x = x[:, None, :]  # [B,1,F]
        h = self.net(x)
        center = self.head_center(h)[:, 0, :]  # [B,F]
        bw_log1p = torch.nn.functional.softplus(self.head_bw(h)[:, 0, :])
        return center, bw_log1p
