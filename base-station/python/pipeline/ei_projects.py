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
duration of one link() call.
"""
import json
import os
from typing import Dict, Optional


def load_projects(path: str) -> Dict[str, dict]:
    """device_type -> {"project_id": int, "api_key": str, "project_name":
    str (optional -- absent on entries saved before this field existed)}.
    Empty (not an error) if nothing's been linked yet -- mirrors
    capture.py's list_labels()/list_captures() "no file yet" contract."""
    if not os.path.isfile(path):
        return {}
    with open(path) as f:
        return json.load(f)


def get_project(path: str, device_type: str) -> Optional[dict]:
    return load_projects(path).get(device_type)


def save_project(path: str, device_type: str, project_id: int, api_key: str,
                  project_name: Optional[str] = None) -> None:
    projects = load_projects(path)
    entry = {"project_id": project_id, "api_key": api_key}
    if project_name is not None:
        entry["project_name"] = project_name
    projects[device_type] = entry
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # write-then-rename so a crash mid-write can't leave a truncated/corrupt
    # file behind for the next load_projects() call to choke on.
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(projects, f)
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)


def rename_project(path: str, old_device_type: str, new_device_type: str) -> bool:
    """Moves old_device_type's linked-project entry (if any) onto
    new_device_type's key -- the EI-project half of an asset-class rename
    (api/app.py's /device_types/rename, alongside ei_scaling.py's
    rename_scaling() and EIController.rename_device_type()'s fetched-model
    file move). Returns whether there was anything to move. Caller is
    responsible for checking new_device_type doesn't already have an entry
    first (a rename onto an in-use name is a merge, which isn't supported
    here) -- this overwrites it silently if it does."""
    projects = load_projects(path)
    if old_device_type not in projects:
        return False
    projects[new_device_type] = projects.pop(old_device_type)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(projects, f)
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)
    return True


def remove_project(path: str, device_type: str) -> bool:
    """Drops device_type's saved mapping (e.g. its Studio project was
    deleted by hand and there's nothing left locally worth keeping).
    Returns whether there was anything to remove."""
    projects = load_projects(path)
    if device_type not in projects:
        return False
    del projects[device_type]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(projects, f)
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)
    return True
