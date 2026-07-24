#!/usr/bin/env python3
"""
Edge Impulse project-mapping store verification (pipeline/ei_projects.py,
docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S4): save/load round-trip,
"no file yet" returns empty rather than raising, and the file is written
0600 (owner-only) -- this is the one server-side secret this app persists
that was typed into the dashboard rather than passed as a startup env var.

Run with PYTHONPATH covering base-station/python/pipeline:
    PYTHONPATH=base-station/python/pipeline \\
        python3 base-station/tests/ei_projects_test.py
"""
import os
import stat
import sys
import tempfile

from ei_projects import get_project, load_projects, remove_project, save_project


def test_missing_file_returns_empty_dict():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "ei_projects.json")
        assert load_projects(path) == {}
        assert get_project(path, "motor001") is None


def test_save_then_load_round_trips():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "ei_projects.json")
        save_project(path, "motor001", project_id=42, api_key="ei_abc")
        assert get_project(path, "motor001") == {"project_id": 42, "api_key": "ei_abc"}
        assert get_project(path, "pump002") is None


def test_save_preserves_other_device_types():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "ei_projects.json")
        save_project(path, "motor001", project_id=42, api_key="ei_abc")
        save_project(path, "pump002", project_id=43, api_key="ei_def")
        projects = load_projects(path)
        assert projects["motor001"]["project_id"] == 42
        assert projects["pump002"]["project_id"] == 43


def test_save_overwrites_same_device_type():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "ei_projects.json")
        save_project(path, "motor001", project_id=42, api_key="ei_abc")
        save_project(path, "motor001", project_id=99, api_key="ei_new")
        assert get_project(path, "motor001") == {"project_id": 99, "api_key": "ei_new"}


def test_file_written_owner_only():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "ei_projects.json")
        save_project(path, "motor001", project_id=42, api_key="ei_abc")
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_save_creates_parent_directory():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "nested", "data", "ei_projects.json")
        save_project(path, "motor001", project_id=42, api_key="ei_abc")
        assert get_project(path, "motor001")["project_id"] == 42


def test_remove_project_drops_only_that_device_type():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "ei_projects.json")
        save_project(path, "motor001", project_id=42, api_key="ei_abc")
        save_project(path, "pump002", project_id=43, api_key="ei_def")

        assert remove_project(path, "motor001") is True
        assert get_project(path, "motor001") is None
        assert get_project(path, "pump002") == {"project_id": 43, "api_key": "ei_def"}


def test_remove_project_missing_device_type_returns_false():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "ei_projects.json")
        save_project(path, "motor001", project_id=42, api_key="ei_abc")
        assert remove_project(path, "pump002") is False
        assert get_project(path, "motor001") is not None


def main():
    test_missing_file_returns_empty_dict()
    test_save_then_load_round_trips()
    test_save_preserves_other_device_types()
    test_save_overwrites_same_device_type()
    test_file_written_owner_only()
    test_save_creates_parent_directory()
    test_remove_project_drops_only_that_device_type()
    test_remove_project_missing_device_type_returns_false()
    print("RESULT: PASS - ei_projects round-trips device_type -> project "
          "mappings and writes the file owner-only")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
