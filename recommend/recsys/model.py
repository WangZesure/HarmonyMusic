"""Content two-tower (PyTorch).

Why this shape:

* The song tower takes the **frozen bge-m3 vector** (1024-d, already L2-normalized
  by the index build) instead of an id embedding.  That is what makes cold start
  work: a song that has never been played still has a vector, because the vector
  comes from its text card, not from its play history.
* The user tower takes the **mean of the vectors of songs the user played**, so a
  user is represented in the same content space.  No user id is learned either,
  which means a brand-new user with a few plays can be served immediately.
* Both towers end in a 64-d unit vector and are combined by a dot product
  (= cosine, since both sides are normalized), scaled by a fixed factor so the
  BCE logits sit in a useful range.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class ContentTwoTower(nn.Module):
    def __init__(self, in_dim: int = 1024, hidden: int = 256,
                 out_dim: int = 64, dropout: float = 0.2, scale: float = 5.0):
        super().__init__()
        self.scale = scale
        self.song_tower = nn.Sequential(
            nn.Linear(in_dim, hidden), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden, out_dim),
        )
        self.user_tower = nn.Sequential(
            nn.Linear(in_dim, hidden), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden, out_dim),
        )

    def song_emb(self, song_vecs: torch.Tensor) -> torch.Tensor:
        return F.normalize(self.song_tower(song_vecs), dim=-1)

    def user_emb(self, history_mean: torch.Tensor) -> torch.Tensor:
        return F.normalize(self.user_tower(history_mean), dim=-1)

    def forward(self, history_mean: torch.Tensor, song_vecs: torch.Tensor) -> torch.Tensor:
        """Returns logits (before sigmoid) for the (user, song) pair."""
        u = self.user_emb(history_mean)
        s = self.song_emb(song_vecs)
        return self.scale * (u * s).sum(dim=-1)
