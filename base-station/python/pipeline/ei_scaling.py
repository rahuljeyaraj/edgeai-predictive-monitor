"""On-disk device_type -> scalar-tail standardization baseline, per docs/
EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S8.4 -- sibling store to
ei_projects.py, same load/save/write-then-rename shape.

Fit once per Upload run (api/ei_controller.py's upload()) from the pooled
TRAIN-split vectors across every local recording for that device_type,
every label -- replaces the old per-node commissioning-baseline approach
(see S8.4 for why: it silently produced inconsistent data across nodes and
depended on commissioning, which capture/upload were never supposed to
need). Persisted so anything that later runs this classifier for real can
reuse the exact same baseline the uploaded/trained data was standardized
with (train/serve skew otherwise).

Deliberately separate from RegistryEntry.scalar_mu/scalar_sigma (per-node,
fit at commissioning, owned by the autoencoder) -- conflating the two
risks one silently overwriting the other. Not a secret, unlike
ei_projects.json's api_key, so no 0600.
"""
import json
import os
from typing import Dict, Optional, Tuple


def load_scaling(path: str) -> Dict[str, dict]:
    """device_type -> {"spectral_dim": int, "mu": [float...], "sigma":
    [float...]}. Empty (not an error) if nothing's been uploaded yet --
    mirrors ei_projects.py's load_projects() "no file yet" contract."""
    if not os.path.isfile(path):
        return {}
    with open(path) as f:
        return json.load(f)


def get_scaling(path: str, device_type: str) -> Optional[dict]:
    return load_scaling(path).get(device_type)


def save_scaling(path: str, device_type: str, spectral_dim: int,
                  mu: Tuple[float, ...], sigma: Tuple[float, ...]) -> None:
    scaling = load_scaling(path)
    scaling[device_type] = {
        "spectral_dim": spectral_dim,
        "mu": list(mu),
        "sigma": list(sigma),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # write-then-rename so a crash mid-write can't leave a truncated/corrupt
    # file behind for the next load_scaling() call to choke on.
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(scaling, f)
    os.replace(tmp_path, path)


def rename_scaling(path: str, old_device_type: str, new_device_type: str) -> bool:
    """Moves old_device_type's fitted baseline (if any) onto
    new_device_type's key -- the scaling half of an asset-class rename
    (api/app.py's /device_types/rename, sibling to ei_projects.py's
    rename_project()). Returns whether there was anything to move."""
    scaling = load_scaling(path)
    if old_device_type not in scaling:
        return False
    scaling[new_device_type] = scaling.pop(old_device_type)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(scaling, f)
    os.replace(tmp_path, path)
    return True
