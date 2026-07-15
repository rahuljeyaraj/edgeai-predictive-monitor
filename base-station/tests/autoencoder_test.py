#!/usr/bin/env python3
"""
Milestone 6 verification: train an
autoencoder on synthetic "healthy" data and confirm low reconstruction
error on similar data, high error on deliberately perturbed data.

"Healthy" and "perturbed" here are both synthetic single-peak spectrum
shapes (stand-ins for a real motor's mic/accel bins from
pipeline/features.py) -- healthy samples share one peak position with
noise, perturbed samples have the peak shifted to a band the model never
saw during training.

Run with PYTHONPATH covering base-station/python/pipeline:
    PYTHONPATH=base-station/python/pipeline python3 base-station/tests/autoencoder_test.py
"""
import math
import os
import random
import sys
import tempfile

import torch

from autoencoder import build_autoencoder, load_model, reconstruction_error, save_model, train_autoencoder

DIM = 64
HEALTHY_CENTER = DIM / 2.0
PERTURBED_CENTER = DIM * 0.85


def make_vector(dim: int, center: float, width: float, noise: float, rng: random.Random):
    vector = []
    for i in range(dim):
        base = math.exp(-((i - center) ** 2) / (2 * width ** 2))
        vector.append(max(0.0, min(1.0, base + rng.uniform(-noise, noise))))
    peak = max(vector) or 1.0
    return tuple(v / peak for v in vector)


def make_healthy_vector(rng: random.Random):
    return make_vector(DIM, HEALTHY_CENTER, width=DIM / 8.0, noise=0.03, rng=rng)


def make_perturbed_vector(rng: random.Random):
    return make_vector(DIM, PERTURBED_CENTER, width=DIM / 20.0, noise=0.05, rng=rng)


def test_train_converges(rng: random.Random):
    model = build_autoencoder(DIM)
    train_vectors = [make_healthy_vector(rng) for _ in range(200)]
    final_loss = train_autoencoder(model, train_vectors, epochs=300)
    assert final_loss < 0.01, final_loss
    print(f"train_autoencoder converges on healthy data (final_loss={final_loss:.5f}): PASS")
    return model


def test_low_error_on_healthy_high_on_perturbed(model, rng: random.Random):
    healthy_errors = [reconstruction_error(model, make_healthy_vector(rng)) for _ in range(20)]
    perturbed_errors = [reconstruction_error(model, make_perturbed_vector(rng)) for _ in range(20)]

    mean_healthy = sum(healthy_errors) / len(healthy_errors)
    mean_perturbed = sum(perturbed_errors) / len(perturbed_errors)

    assert mean_healthy < 0.01, mean_healthy
    assert mean_perturbed > mean_healthy * 5, (mean_healthy, mean_perturbed)
    print(f"low reconstruction error on healthy data (mean={mean_healthy:.5f}), "
          f"high on perturbed data (mean={mean_perturbed:.5f}): PASS")


def test_save_load_round_trip(model, rng: random.Random):
    vector = make_healthy_vector(rng)
    original_error = reconstruction_error(model, vector)

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "model.pt")
        save_model(model, path)
        loaded = load_model(path)

    assert loaded.input_dim == DIM, loaded.input_dim
    loaded_error = reconstruction_error(loaded, vector)
    assert abs(original_error - loaded_error) < 1e-6, (original_error, loaded_error)
    print("save_model/load_model round trip preserves weights: PASS")


def test_dim_mismatch_raises():
    model = build_autoencoder(DIM)
    wrong_length_vector = tuple(0.0 for _ in range(DIM // 2))

    try:
        train_autoencoder(model, [wrong_length_vector])
        assert False, "expected ValueError for wrong-length training vector"
    except ValueError:
        pass

    try:
        reconstruction_error(model, wrong_length_vector)
        assert False, "expected ValueError for wrong-length vector"
    except ValueError:
        pass
    print("input_dim mismatch raises: PASS")


def test_empty_training_data_raises():
    model = build_autoencoder(DIM)
    try:
        train_autoencoder(model, [])
        assert False, "expected ValueError for empty training data"
    except ValueError:
        pass
    print("empty training data raises: PASS")


def test_on_epoch_callback_fires_once_per_epoch(rng: random.Random):
    """Dashboard redesign S6: train_autoencoder's optional on_epoch hook is
    what lets a caller stream training progress -- confirm it fires
    exactly once per epoch, 1-based, with the fixed total_epochs."""
    model = build_autoencoder(DIM)
    train_vectors = [make_healthy_vector(rng) for _ in range(10)]
    seen = []
    train_autoencoder(model, train_vectors, epochs=7, on_epoch=lambda epoch, total: seen.append((epoch, total)))
    assert seen == [(e, 7) for e in range(1, 8)], seen
    print("on_epoch callback fires once per epoch, 1-based, with fixed total_epochs: PASS")


def main():
    torch.manual_seed(42)
    rng = random.Random(42)

    model = test_train_converges(rng)
    test_low_error_on_healthy_high_on_perturbed(model, rng)
    test_save_load_round_trip(model, rng)
    test_dim_mismatch_raises()
    test_empty_training_data_raises()
    test_on_epoch_callback_fires_once_per_epoch(rng)
    print("RESULT: PASS - autoencoder trains, reconstructs healthy data well, "
          "flags perturbed data, and survives save/load")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
