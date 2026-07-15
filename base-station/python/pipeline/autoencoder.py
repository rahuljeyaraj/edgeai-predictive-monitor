"""Autoencoder model -- architecture parameterized by input dim, train +
save/load functions, per docs/MPU_Software_Architecture.md S3.6/S8 M6.

Resolves open question #1 (S6): PyTorch, since commissioning (S3.5, M7)
trains a small per-motor autoencoder on the QRB2210 itself rather than
just running inference -- TF Lite has no real on-device training story
and ONNX Runtime's training support is experimental, while PyTorch gives
full training + inference on arm64 Linux for a model this size (a dense
autoencoder over the 512- or 1024-dim feature vector from
pipeline/features.py) with no meaningful latency concern.
"""
from typing import Callable, Optional, Sequence, Tuple

import torch
from torch import nn


class Autoencoder(nn.Module):
    """Symmetric dense encoder/decoder. hidden/bottleneck sizes scale with
    input_dim so the 512-dim and 1024-dim variants (S4.2) get proportionally
    scaled capacity rather than a hardcoded architecture."""

    def __init__(self, input_dim: int):
        super().__init__()
        hidden_dim = max(input_dim // 4, 8)
        bottleneck_dim = max(input_dim // 16, 4)
        self.input_dim = input_dim
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, bottleneck_dim),
            nn.ReLU(),
        )
        self.decoder = nn.Sequential(
            nn.Linear(bottleneck_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, input_dim),
            nn.Sigmoid(),  # feature vectors are peak-normalized to [0, 1] (S3.3)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encoder(x))


def build_autoencoder(input_dim: int) -> Autoencoder:
    return Autoencoder(input_dim)


def _check_dim(model: Autoencoder, length: int) -> None:
    if length != model.input_dim:
        raise ValueError(f"vector length {length} != model input_dim {model.input_dim}")


def train_autoencoder(model: Autoencoder, vectors: Sequence[Tuple[float, ...]],
                       epochs: int = 300, lr: float = 1e-3,
                       on_epoch: Optional[Callable[[int, int], None]] = None) -> float:
    """Trains in place on gated healthy feature vectors (S3.5, "running,
    healthy" data only); returns the final epoch's loss so callers/tests can
    confirm convergence.

    on_epoch(epoch, total_epochs), if given, is called once per epoch
    (1-based) so a caller can stream training progress (dashboard redesign
    S6). This module stays transport-agnostic -- throttling how often
    progress is actually broadcast is the caller's job (api/app.py), not
    this training loop's."""
    if not vectors:
        raise ValueError("train_autoencoder requires at least one training vector")
    for v in vectors:
        _check_dim(model, len(v))

    data = torch.tensor(vectors, dtype=torch.float32)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()

    model.train()
    final_loss = float("inf")
    for epoch in range(1, epochs + 1):
        optimizer.zero_grad()
        reconstruction = model(data)
        loss = loss_fn(reconstruction, data)
        loss.backward()
        optimizer.step()
        final_loss = loss.item()
        if on_epoch is not None:
            on_epoch(epoch, epochs)
    return final_loss


def reconstruction_error(model: Autoencoder, vector: Tuple[float, ...]) -> float:
    """Anomaly score input (S3.6): mean squared reconstruction error for one
    feature vector -- low for data like what the model trained on
    ("healthy"), high for data it never saw (candidate fault signatures)."""
    _check_dim(model, len(vector))

    model.eval()
    with torch.no_grad():
        x = torch.tensor([vector], dtype=torch.float32)
        reconstruction = model(x)
        return nn.functional.mse_loss(reconstruction, x).item()


def save_model(model: Autoencoder, path: str) -> None:
    torch.save({"input_dim": model.input_dim, "state_dict": model.state_dict()}, path)


def load_model(path: str) -> Autoencoder:
    checkpoint = torch.load(path, weights_only=True)
    model = Autoencoder(checkpoint["input_dim"])
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    return model
