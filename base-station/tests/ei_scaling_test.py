#!/usr/bin/env python3
"""
Edge Impulse scalar-tail baseline store verification (pipeline/
ei_scaling.py, docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S8.4):
save/load round-trip, "no file yet" returns empty rather than raising,
save() overwrites only the given device_type -- same shape as
ei_projects_test.py, minus the 0600 check (this store holds no secret).

Run with PYTHONPATH covering base-station/python/pipeline:
    PYTHONPATH=base-station/python/pipeline \\
        python3 base-station/tests/ei_scaling_test.py
"""
import os
import sys
import tempfile

from ei_scaling import get_scaling, load_scaling, rename_scaling, save_scaling


def test_missing_file_returns_empty_dict():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "ei_scaling.json")
        assert load_scaling(path) == {}
        assert get_scaling(path, "motor001") is None


def test_save_then_load_round_trips():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "ei_scaling.json")
        save_scaling(path, "motor001", spectral_dim=128, mu=(1.0, 2.0), sigma=(0.5, 0.25))
        assert get_scaling(path, "motor001") == {
            "spectral_dim": 128, "mu": [1.0, 2.0], "sigma": [0.5, 0.25]}
        assert get_scaling(path, "pump002") is None


def test_save_preserves_other_device_types():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "ei_scaling.json")
        save_scaling(path, "motor001", spectral_dim=128, mu=(1.0,), sigma=(0.5,))
        save_scaling(path, "pump002", spectral_dim=64, mu=(2.0,), sigma=(1.0,))
        scaling = load_scaling(path)
        assert scaling["motor001"]["spectral_dim"] == 128
        assert scaling["pump002"]["spectral_dim"] == 64


def test_save_overwrites_same_device_type():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "ei_scaling.json")
        save_scaling(path, "motor001", spectral_dim=128, mu=(1.0,), sigma=(0.5,))
        save_scaling(path, "motor001", spectral_dim=128, mu=(3.0,), sigma=(2.0,))
        assert get_scaling(path, "motor001") == {
            "spectral_dim": 128, "mu": [3.0], "sigma": [2.0]}


def test_save_creates_parent_directory():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "nested", "data", "ei_scaling.json")
        save_scaling(path, "motor001", spectral_dim=128, mu=(1.0,), sigma=(0.5,))
        assert get_scaling(path, "motor001")["spectral_dim"] == 128


def test_rename_scaling_moves_entry_to_new_key():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "ei_scaling.json")
        save_scaling(path, "motor001", spectral_dim=128, mu=(1.0,), sigma=(0.5,))
        save_scaling(path, "pump002", spectral_dim=64, mu=(2.0,), sigma=(1.0,))

        assert rename_scaling(path, "motor001", "conveyor001") is True
        assert get_scaling(path, "motor001") is None
        assert get_scaling(path, "conveyor001") == {
            "spectral_dim": 128, "mu": [1.0], "sigma": [0.5]}
        assert get_scaling(path, "pump002")["spectral_dim"] == 64


def test_rename_scaling_missing_old_device_type_returns_false():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "ei_scaling.json")
        save_scaling(path, "motor001", spectral_dim=128, mu=(1.0,), sigma=(0.5,))
        assert rename_scaling(path, "pump002", "conveyor001") is False
        assert get_scaling(path, "motor001") is not None


def main():
    test_missing_file_returns_empty_dict()
    test_save_then_load_round_trips()
    test_save_preserves_other_device_types()
    test_save_overwrites_same_device_type()
    test_save_creates_parent_directory()
    test_rename_scaling_moves_entry_to_new_key()
    test_rename_scaling_missing_old_device_type_returns_false()
    print("RESULT: PASS - ei_scaling round-trips device_type -> scalar-tail "
          "baseline mappings")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
