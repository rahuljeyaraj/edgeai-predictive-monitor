"""Edge Impulse REST client -- Round A ("Upload") of docs/
EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S4: login, create a project +
impulse + training config, and push labeled feature-vector samples via the
ingestion API. Round B ("Train/Build/Fetch", S4 steps 5-9) adds
generate_features()/train()/build_model() (async EI jobs) plus
wait_for_job() to poll them from the calling thread, and
download_model()/extract_tflite() to pull the built model down.

Plain functions, stdlib `urllib.request` only -- no `requests`/
`edgeimpulse-api` dependency, matching this project's deliberately
dependency-light production path (requirements.txt is just
torch/psutil/python-statemachine; the existing EI tooling
(tools/ei_upload.sh) already prefers curl over a Python HTTP lib for the
same reason).

The impulse/training-config JSON bodies below are built from Edge
Impulse's documented schema. `_impulse_body()` went through two wrong
turns against a live account on 2026-07-25 before landing here: (1) a
"features" input block with a made-up single axis name ("feature") that
matched none of the axes EI derives from a CSV's header row, leaving the
DSP block's axis selection empty; (2) a "time-series" input block sized
to fit the whole vector in one window -- functionally fine, but rejected
as a workaround, since this data is a precomputed feature vector, not a
raw time-series, and EI's docs confirm a "features" input block is the
semantically correct fit. Current shape: back to a "features" input
block, but with real per-column axis names (pipeline/features.py's
axis_names_for(), e.g. "accel_x_bin0", "accel_x_rms") computed by the caller
(api/ei_controller.py) from the node's actual sensor_config and passed
in, matched by `_to_csv()`'s wide CSV -- one header row of those same
names, one data row per sample (EI's documented "single, multi-axis
reading" format). Still not exercised against a live account.
Round B's job endpoints carry the same caveat: EI's own docs describe the
`generate-features`/`train/keras`/`build-ondevice-model` jobs and the
`{finished, finishedSuccessful}` status shape, but this has not been
exercised against a live account yet either -- expect to adjust
`job_status()`'s field names first if a real run disagrees. Round B
deliberately polls the plain status endpoint instead of EI's WebSocket
"remote management protocol" for job logs (documented as the one
legitimate non-ingestion use of EI's WS in this whole flow) -- adding a
second WS client just for progress text would cut against this module's
"stdlib only" rule for one cosmetic upgrade (a log tail vs. a bare "still
running" poll), so it's left for later if the plain poll turns out to be
too coarse in practice.
"""
import io
import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from typing import Callable, Dict, List, Optional, Tuple

STUDIO_BASE = "https://studio.edgeimpulse.com/v1"
INGESTION_BASE = "https://ingestion.edgeimpulse.com/api"

_TIMEOUT_S = 30
# Matches tools/ei_upload.sh's BATCH_SIZE default -- validated batch size
# for the ingestion API's one-"data"-part-per-sample multipart upload.
UPLOAD_BATCH_SIZE = 25

# Fixed ids from _impulse_body()'s template below -- shared module
# constants (rather than each caller re-guessing 2/3) so generate_features()/
# train() default to the same block create_impulse() actually declared.
DSP_BLOCK_ID = 2
LEARN_BLOCK_ID = 3

# Round B jobs (generate-features/train/build) run for real minutes on EI's
# side, not seconds -- polled well below _TIMEOUT_S (that's the per-HTTP-call
# timeout, not the whole job's).
_JOB_POLL_INTERVAL_S = 5.0
_JOB_TIMEOUT_S = 1800.0


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


def _impulse_body(input_dim: int, axes: List[str]) -> dict:
    """Fixed template, same for every device type: one "features" input
    block (this data IS already a computed feature vector -- no DSP
    windowing/reprocessing should happen at all, unlike a raw-sensor
    "time-series" input), a passthrough DSP block declaring the real
    per-column axis names, one "keras" learn block referencing it.

    `axes` is the caller's (api/ei_controller.py's) list of real
    per-column names -- pipeline/features.py's axis_names_for(), which
    must list EXACTLY the same names in the same order as _to_csv()'s CSV
    header, since EI derives each axis from a CSV's header row rather
    than from anything declared here. A made-up/mismatched axis list
    leaves the DSP block's axis selection empty (confirmed live
    2026-07-25 with a single made-up "feature" name). Still not confirmed
    live whether declaring `axes` here is sufficient to also pre-*select*
    them for training, or whether that's a separate step done after data
    exists."""
    return {
        "inputBlocks": [
            {"id": 1, "type": "features", "name": "features", "title": "Features"},
        ],
        "dspBlocks": [
            {"id": DSP_BLOCK_ID, "type": "raw", "name": "raw", "title": "Raw features",
             "axes": axes, "implementationVersion": 1},
        ],
        "learnBlocks": [
            {"id": LEARN_BLOCK_ID, "type": "keras", "name": "classifier", "title": "Classifier",
             "dsp": [DSP_BLOCK_ID]},
        ],
    }


def create_impulse(api_key: str, project_id: int, input_dim: int, axes: List[str]) -> int:
    """Returns the learn block id (fixed at LEARN_BLOCK_ID by _impulse_body's
    template) for set_nn_config() below. axes: real per-column names, same
    order as the CSV header _to_csv() will use for every uploaded sample --
    see _impulse_body()."""
    body = _impulse_body(input_dim, axes)
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


# ---------------------------------------------------------------------------
# Round B -- Train/Build/Fetch (S4 steps 5-9)
# ---------------------------------------------------------------------------

def generate_features(api_key: str, project_id: int, dsp_id: int = DSP_BLOCK_ID) -> int:
    """Step 5: kicks off the DSP feature-generation job over every sample
    already ingested (upload_samples() above) for this project's dsp_id
    block. Must complete before train() below can see any data. Returns
    the job id for wait_for_job()."""
    resp = _json_post(f"{STUDIO_BASE}/api/{project_id}/jobs/generate-features",
                       {"x-api-key": api_key}, {"dspId": dsp_id})
    return resp["id"]


def train(api_key: str, project_id: int, learn_id: int = LEARN_BLOCK_ID) -> int:
    """Step 6: kicks off the Keras training job (config already set by
    set_nn_config() at link() time). Returns the job id for wait_for_job()."""
    resp = _json_post(f"{STUDIO_BASE}/api/{project_id}/jobs/train/keras/{learn_id}",
                       {"x-api-key": api_key}, {})
    return resp["id"]


def build_model(api_key: str, project_id: int, engine: str = "tflite") -> int:
    """Step 8: kicks off the on-device-model build job for the just-trained
    weights. `engine="tflite"` matches the already-locked TFLite decision
    (docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md) -- not the EON
    compiler, not a `.eim` runner. `type=zip` is a *separate* query param
    naming the deployment target (confirmed against EI's own API docs) --
    `zip` is the only target that bundles a `.tflite` inside a ZIP the way
    download_model()/extract_tflite() below expect; omitting it (as this
    did before a live-account run surfaced the bug) leaves EI to pick a
    default target, which does not reliably yield a ZIP. Returns the job
    id for wait_for_job()."""
    resp = _json_post(f"{STUDIO_BASE}/api/{project_id}/jobs/build-ondevice-model?type=zip",
                       {"x-api-key": api_key}, {"engine": engine})
    return resp["id"]


def job_status(api_key: str, project_id: int, job_id: int) -> dict:
    """Step 7 (poll): raw `job` object from EI's job-status endpoint --
    `{"finished": <truthy once done>, "finishedSuccessful": bool, ...}`.
    Returned as-is (not reshaped into this module's own type) since
    wait_for_job() is the only caller and the exact extra fields EI
    includes are still unconfirmed against a live account (see module
    docstring)."""
    resp = _request("GET", f"{STUDIO_BASE}/api/{project_id}/jobs/{job_id}/status",
                     {"x-api-key": api_key})
    return resp.get("job", resp)


def wait_for_job(api_key: str, project_id: int, job_id: int,
                  on_poll: Optional[Callable[[], None]] = None,
                  poll_interval_s: float = _JOB_POLL_INTERVAL_S,
                  timeout_s: float = _JOB_TIMEOUT_S) -> None:
    """Blocks the calling thread, polling job_status() until EI reports the
    job finished. Callers (api/ei_controller.py) run this off a background
    thread the same way commissioning.py's own long fixed-epoch train() is,
    and pass on_poll to re-broadcast a "still going" progress tick each
    round -- EI jobs run for real minutes, so a caller with no progress
    signal at all between "started" and "done" would look hung.
    Raises EIClientError if the job finishes unsuccessfully or never
    finishes within timeout_s."""
    deadline = time.monotonic() + timeout_s
    while True:
        job = job_status(api_key, project_id, job_id)
        if job.get("finished"):
            if job.get("finishedSuccessful", True):
                return
            raise EIClientError(f"job {job_id} on project {project_id} did not finish successfully")
        if time.monotonic() > deadline:
            raise EIClientError(f"job {job_id} on project {project_id} timed out after "
                                 f"{timeout_s:.0f}s")
        if on_poll:
            on_poll()
        time.sleep(poll_interval_s)


def download_model(api_key: str, project_id: int, engine: str = "tflite") -> bytes:
    """Step 9: downloads the deployment ZIP built by build_model() (must
    already be finished -- wait_for_job() first). `type` and `engine` are
    two independent query params on this endpoint (see build_model()'s
    docstring) -- previously conflated into a single `type={engine}`
    (i.e. `type=tflite`), which asked EI for a bare, non-zip `.tflite`
    and broke extract_tflite()'s zipfile.ZipFile() call (confirmed against
    EI's own API docs after a live-account fetch actually failed this
    way). Returns the raw ZIP bytes; not routed through _request()/
    _json_post() since those always JSON-decode the response body and
    this endpoint returns binary."""
    url = f"{STUDIO_BASE}/api/{project_id}/deployment/download?type=zip&engine={engine}"
    req = urllib.request.Request(url, headers={"x-api-key": api_key}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    if status < 200 or status >= 300:
        raise EIClientError(f"{url}: HTTP {status} - {raw[:200].decode('utf-8', 'replace')}")
    return raw


def delete_all_samples(api_key: str, project_id: int) -> None:
    """docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S8.5: wipes every
    sample across all categories for the project and invalidates its
    DSP/learn block state (features + trained model) -- not deleted from
    EI's cold storage, but a clean wipe from Studio's perspective. Called
    at the start of every upload() (api/ei_controller.py) so there's never
    a partial/stale remote state to reconcile against. Unverified against
    a live account, like every other job endpoint in this module (see
    module docstring)."""
    _request("POST", f"{STUDIO_BASE}/api/{project_id}/raw-data/delete-all",
              {"x-api-key": api_key})


def extract_tflite(zip_bytes: bytes) -> bytes:
    """Pulls the single .tflite entry out of download_model()'s deployment
    ZIP. Raises EIClientError (not a bare zipfile/KeyError) if the archive
    holds no .tflite file, or more than one, so a shape this module didn't
    expect fails loudly at the extraction point rather than silently
    grabbing the wrong file."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".tflite")]
        if not names:
            raise EIClientError("deployment ZIP contained no .tflite file")
        if len(names) > 1:
            raise EIClientError(f"deployment ZIP contained multiple .tflite files: {names}")
        return zf.read(names[0])
