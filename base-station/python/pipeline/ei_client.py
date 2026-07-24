"""Edge Impulse REST client -- Round A ("Upload") of docs/
EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S4: login, create a project +
impulse + training config, and push labeled feature-vector samples via the
ingestion API. Train/poll/build/fetch (S4 steps 5-9) are a follow-up round.

Plain functions, stdlib `urllib.request` only -- no `requests`/
`edgeimpulse-api` dependency, matching this project's deliberately
dependency-light production path (requirements.txt is just
torch/psutil/python-statemachine; the existing EI tooling
(tools/ei_upload.sh) already prefers curl over a Python HTTP lib for the
same reason).

The impulse/training-config JSON bodies below are built from Edge
Impulse's documented schema but not yet verified against a live account
(a "features" input block instead of the usual "time-series" one is an
uncommon enough impulse shape that Studio's own UI doesn't offer a
byte-identical example to crib from) -- expect to adjust
`_impulse_body()`/`_nn_config_body()` if the live account rejects them.
"""
import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from typing import Dict, List, Optional, Tuple

STUDIO_BASE = "https://studio.edgeimpulse.com/v1"
INGESTION_BASE = "https://ingestion.edgeimpulse.com/api"

_TIMEOUT_S = 30
# Matches tools/ei_upload.sh's BATCH_SIZE default -- validated batch size
# for the ingestion API's one-"data"-part-per-sample multipart upload.
UPLOAD_BATCH_SIZE = 25


class EIClientError(Exception):
    """Wraps Edge Impulse's own `error` message (login failure, duplicate
    project name, malformed impulse config, ...) -- surfaced to the
    dashboard largely verbatim rather than translated, since EI's messages
    are already operator-facing text."""


class EITotpRequiredError(EIClientError):
    """Raised specifically when EI's login response starts with
    ERR_TOTP_TOKEN_IS_REQUIRED, so callers (api/ei_controller.py) can tell
    "wrong password" apart from "need a TOTP code" and re-prompt instead of
    just failing."""


def _request(method: str, url: str, headers: Dict[str, str],
             body: Optional[bytes] = None) -> dict:
    """HTTP status is the authoritative success/failure signal (confirmed
    against tools/ei_upload.sh, which only ever checks the status code) --
    not every EI endpoint's 200 response body carries a `"success"` field
    (the Studio API's does; the ingestion API's may not), so treating a
    missing `success` key as failure would misreport a real 200 as an
    error. The `success`/`error` JSON fields are still used opportunistically
    for a real error message when the body has them."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()

    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {}

    if status < 200 or status >= 300 or parsed.get("success") is False:
        error = parsed.get("error") or raw[:200].decode("utf-8", "replace") or f"HTTP {status}"
        if error.startswith("ERR_TOTP_TOKEN_IS_REQUIRED"):
            raise EITotpRequiredError(error)
        raise EIClientError(f"{url}: {error}")
    return parsed


def _json_post(url: str, headers: Dict[str, str], payload: dict) -> dict:
    headers = {**headers, "Content-Type": "application/json"}
    return _request("POST", url, headers, json.dumps(payload).encode("utf-8"))


def login(username: str, password: str, totp: Optional[str] = None) -> str:
    """Returns a JWT usable (as `x-jwt-token`) for account-level calls like
    create_project() -- used once per new device type, never persisted
    (docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S0's auth-model note)."""
    body = {"username": username, "password": password}
    if totp:
        body["totpToken"] = totp
    resp = _json_post(f"{STUDIO_BASE}/api-login", {}, body)
    return resp["token"]


def create_project(jwt_token: str, project_name: str) -> Tuple[int, str]:
    """Returns (project_id, project_api_key) -- the api_key is what every
    later call (impulse config, ingestion, training) uses instead of the
    JWT, since it's scoped + long-lived rather than an account-level
    session token."""
    resp = _json_post(f"{STUDIO_BASE}/api/projects/create",
                       {"x-jwt-token": jwt_token},
                       {"projectName": project_name, "createApiKey": True})
    return resp["id"], resp["apiKey"]


def _impulse_body(input_dim: int) -> dict:
    """Fixed template, same for every device type: one "features" input
    block (tells EI this data is already a flat feature vector -- no DSP
    reprocessing, sidestepping the one part of impulse config that's
    hardest to drive blindly, e.g. frequency-range DSP parameters), a
    passthrough DSP block referencing it, one "keras" learn block
    referencing that DSP block. `input_dim` isn't actually a field on the
    input block itself (EI infers vector length from the ingested
    samples) -- kept as a parameter anyway since callers need it for
    _nn_config_body()'s first layer and it documents the coupling."""
    del input_dim  # not part of the impulse body itself -- see docstring
    return {
        "inputBlocks": [
            {"id": 1, "type": "features", "name": "features", "title": "Features"},
        ],
        "dspBlocks": [
            {"id": 2, "type": "raw", "name": "raw", "title": "Raw features",
             "axes": ["feature"], "implementationVersion": 1},
        ],
        "learnBlocks": [
            {"id": 3, "type": "keras", "name": "classifier", "title": "Classifier",
             "dsp": [2]},
        ],
    }


def create_impulse(api_key: str, project_id: int, input_dim: int) -> int:
    """Returns the learn block id (fixed at 3 by _impulse_body's template)
    for set_nn_config() below."""
    body = _impulse_body(input_dim)
    _json_post(f"{STUDIO_BASE}/api/{project_id}/impulse", {"x-api-key": api_key}, body)
    return body["learnBlocks"][0]["id"]


def _nn_config_body(input_dim: int, num_classes: int) -> dict:
    """Small fixed dense template -- two hidden layers sized off
    input_dim, softmax output sized to num_classes. Same body every device
    type; layer sizing is the one thing that scales with input_dim so a
    536-dim vector (tools/ei_capture_upload.py's per-axis+scalars layout)
    and a smaller mic-only vector don't share an undersized/oversized
    first layer."""
    hidden1 = max(num_classes * 4, min(input_dim, 64))
    hidden2 = max(num_classes * 2, min(hidden1 // 2, 32))
    return {
        "mode": "visual",
        "visualLayers": [
            {"type": "dense", "neurons": hidden1, "dropoutRate": 0.25},
            {"type": "dense", "neurons": hidden2, "dropoutRate": 0.25},
        ],
        "trainingCycles": 50,
        "learningRate": 0.001,
        "batchSize": 32,
    }


def set_nn_config(api_key: str, project_id: int, learn_id: int,
                   input_dim: int, num_classes: int) -> None:
    _json_post(f"{STUDIO_BASE}/api/{project_id}/training/keras/{learn_id}",
               {"x-api-key": api_key}, _nn_config_body(input_dim, num_classes))


def _multipart_body(samples: List[Tuple[str, bytes]]) -> Tuple[bytes, str]:
    """One "data" part per (filename, csv_bytes) sample -- confirmed by
    tools/ei_upload.sh's own comment that the ingestion API creates one
    sample per attached "data" part, not one merged sample."""
    boundary = uuid.uuid4().hex
    parts = []
    for filename, csv_bytes in samples:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="data"; filename="{filename}"\r\n'
            f"Content-Type: {mimetypes.guess_type(filename)[0] or 'text/csv'}\r\n\r\n"
            .encode("utf-8") + csv_bytes + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary


def upload_samples(api_key: str, category: str, label: str,
                    samples: List[Tuple[str, bytes]]) -> int:
    """category: "training" or "testing". samples: [(filename, csv_bytes),
    ...], already batched by the caller to at most UPLOAD_BATCH_SIZE (same
    batching tools/ei_upload.sh does, cutting hundreds of sequential
    round-trips down to a couple dozen). Returns the number of samples
    uploaded in this call."""
    if not samples:
        return 0
    body, boundary = _multipart_body(samples)
    headers = {
        "x-api-key": api_key,
        "x-label": label,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    _request("POST", f"{INGESTION_BASE}/{category}/files", headers, body)
    return len(samples)


def batched(items: list, batch_size: int = UPLOAD_BATCH_SIZE):
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]


def timestamped_filename(label: str, index: int) -> str:
    return f"{label}.{index}.{int(time.time() * 1000)}.csv"
