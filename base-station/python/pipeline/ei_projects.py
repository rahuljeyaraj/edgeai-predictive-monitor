"""On-disk device_type -> Edge Impulse project mapping, per docs/
EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S4/S0 -- plain-function store
mirroring capture.py's list_captures()/rename_capture() shape.

Holds the one server-side secret this app persists that was typed into the
dashboard rather than passed as a startup env var (unlike
TELEGRAM_BOT_TOKEN) -- each entry's `api_key` can trigger real EI training
jobs and spend account compute, so the file is written 0600 (owner-only),
tighter than registry.json/captures/ which carry no secret. The EI
username/password/JWT used to *create* a project are never written here or
anywhere else -- api/ei_controller.py holds them in memory only for the
duration of one connect() call.
"""
import json
import os
from typing import Dict, Optional


def load_projects(path: str) -> Dict[str, dict]:
    """device_type -> {"project_id": int, "api_key": str}. Empty (not an
    error) if nothing's been connected yet -- mirrors capture.py's
    list_labels()/list_captures() "no file yet" contract."""
    if not os.path.isfile(path):
        return {}
    with open(path) as f:
        return json.load(f)


def get_project(path: str, device_type: str) -> Optional[dict]:
    return load_projects(path).get(device_type)


def save_project(path: str, device_type: str, project_id: int, api_key: str) -> None:
    projects = load_projects(path)
    projects[device_type] = {"project_id": project_id, "api_key": api_key}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # write-then-rename so a crash mid-write can't leave a truncated/corrupt
    # file behind for the next load_projects() call to choke on.
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(projects, f)
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)
