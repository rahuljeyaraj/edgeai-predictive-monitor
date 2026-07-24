#!/usr/bin/env python3
"""
Edge Impulse REST client verification (pipeline/ei_client.py, docs/
EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S4): request construction (URLs,
headers, JSON/multipart bodies) and response handling (HTTP status is the
authoritative success signal, not a `"success"` JSON field every endpoint
carries; TOTP-required detection) -- all against a hand-rolled fake
urlopen, no real network call, same "duck-typed fake, no mock library"
convention as api_test.py's FakeTelegramBot.

Run with PYTHONPATH covering base-station/python/pipeline:
    PYTHONPATH=base-station/python/pipeline \\
        python3 base-station/tests/ei_client_test.py
"""
import io
import json
import sys
import urllib.error
import urllib.request

import ei_client
from ei_client import EIClientError, EITotpRequiredError


class FakeResponse:
    def __init__(self, status, body_bytes):
        self.status = status
        self._body = body_bytes

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeUrlopen:
    """Queue of (status, body_bytes) to return in call order; records every
    urllib.request.Request it was called with so tests can assert on
    method/url/headers/body."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        status, body = self._responses.pop(0)
        if status < 200 or status >= 300:
            raise urllib.error.HTTPError(req.full_url, status, "error", {}, io.BytesIO(body))
        return FakeResponse(status, body)


def patched(responses):
    fake = FakeUrlopen(responses)
    original = urllib.request.urlopen
    urllib.request.urlopen = fake
    return fake, original


def restore(original):
    urllib.request.urlopen = original


def json_body(status, obj):
    return status, json.dumps(obj).encode("utf-8")


def test_login_returns_token():
    fake, original = patched([json_body(200, {"success": True, "token": "jwt-123"})])
    try:
        token = ei_client.login("me@example.com", "hunter2")
    finally:
        restore(original)
    assert token == "jwt-123"
    req = fake.requests[0]
    assert req.full_url == f"{ei_client.STUDIO_BASE}/api-login"
    body = json.loads(req.data)
    assert body == {"username": "me@example.com", "password": "hunter2"}


def test_login_includes_totp_when_given():
    fake, original = patched([json_body(200, {"success": True, "token": "jwt-123"})])
    try:
        ei_client.login("me@example.com", "hunter2", totp="654321")
    finally:
        restore(original)
    body = json.loads(fake.requests[0].data)
    assert body["totpToken"] == "654321"


def test_login_raises_totp_required():
    _fake, original = patched(
        [json_body(200, {"success": False, "error": "ERR_TOTP_TOKEN_IS_REQUIRED: need a code"})])
    try:
        try:
            ei_client.login("me@example.com", "hunter2")
            raise AssertionError("expected EITotpRequiredError")
        except EITotpRequiredError:
            pass
    finally:
        restore(original)


def test_login_raises_generic_error_on_bad_credentials():
    _fake, original = patched(
        [json_body(200, {"success": False, "error": "ERR_INVALID_CREDENTIALS: bad password"})])
    try:
        try:
            ei_client.login("me@example.com", "wrong")
            raise AssertionError("expected EIClientError")
        except EITotpRequiredError:
            raise AssertionError("bad password must not be classified as TOTP-required")
        except EIClientError as e:
            assert "bad password" in str(e)
    finally:
        restore(original)


def test_create_project_uses_jwt_header_and_returns_id_and_key():
    fake, original = patched([json_body(200, {"success": True, "id": 42, "apiKey": "ei_abc"})])
    try:
        project_id, api_key = ei_client.create_project("jwt-123", "EdgeAI - motor001")
    finally:
        restore(original)
    assert (project_id, api_key) == (42, "ei_abc")
    req = fake.requests[0]
    assert req.full_url == f"{ei_client.STUDIO_BASE}/api/projects/create"
    assert req.get_header("X-jwt-token") == "jwt-123"
    body = json.loads(req.data)
    assert body["projectName"] == "EdgeAI - motor001"
    assert body["createApiKey"] is True


def test_create_impulse_declares_a_features_input_block():
    fake, original = patched([json_body(200, {"success": True})])
    try:
        learn_id = ei_client.create_impulse("ei_abc", 42, input_dim=536)
    finally:
        restore(original)
    req = fake.requests[0]
    assert req.full_url == f"{ei_client.STUDIO_BASE}/api/42/impulse"
    assert req.get_header("X-api-key") == "ei_abc"
    body = json.loads(req.data)
    assert body["inputBlocks"][0]["type"] == "features"
    assert body["learnBlocks"][0]["type"] == "keras"
    # learn_id returned must be the id the impulse body actually declared --
    # set_nn_config()'s caller (EIController.connect()) depends on this
    # matching, not just being *some* integer.
    assert learn_id == body["learnBlocks"][0]["id"]


def test_set_nn_config_posts_to_learn_id_scoped_url():
    fake, original = patched([json_body(200, {"success": True})])
    try:
        ei_client.set_nn_config("ei_abc", 42, learn_id=3, input_dim=536, num_classes=4)
    finally:
        restore(original)
    req = fake.requests[0]
    assert req.full_url == f"{ei_client.STUDIO_BASE}/api/42/training/keras/3"
    body = json.loads(req.data)
    assert "visualLayers" in body


def test_request_treats_http_200_with_no_success_field_as_success():
    # The ingestion API's response shape isn't confirmed to carry a
    # "success" field the way the Studio API's does (tools/ei_upload.sh
    # only ever checks the HTTP status code) -- a missing key must not be
    # misread as failure.
    _fake, original = patched([(200, b'{"someOtherField": true}')])
    try:
        result = ei_client._request("POST", "https://example.invalid/x", {})
    finally:
        restore(original)
    assert result == {"someOtherField": True}


def test_request_raises_on_non_2xx_even_without_json_body():
    _fake, original = patched([(500, b"internal error, not json")])
    try:
        try:
            ei_client._request("POST", "https://example.invalid/x", {})
            raise AssertionError("expected EIClientError")
        except EIClientError as e:
            assert "internal error" in str(e)
    finally:
        restore(original)


def test_upload_samples_empty_list_makes_no_request():
    fake, original = patched([])
    try:
        count = ei_client.upload_samples("ei_abc", "training", "bearing_fault", [])
    finally:
        restore(original)
    assert count == 0
    assert fake.requests == []


def test_upload_samples_posts_one_multipart_part_per_sample():
    fake, original = patched([(200, b"")])
    samples = [("bearing_fault.0.csv", b"timestamp,feature\n0.0,1.0\n"),
               ("bearing_fault.1.csv", b"timestamp,feature\n0.0,2.0\n")]
    try:
        count = ei_client.upload_samples("ei_abc", "training", "bearing_fault", samples)
    finally:
        restore(original)
    assert count == 2
    req = fake.requests[0]
    assert req.full_url == f"{ei_client.INGESTION_BASE}/training/files"
    assert req.get_header("X-api-key") == "ei_abc"
    assert req.get_header("X-label") == "bearing_fault"
    assert req.data.count(b'name="data"') == 2


def test_batched_splits_into_chunks_of_batch_size():
    items = list(range(7))
    chunks = list(ei_client.batched(items, batch_size=3))
    assert chunks == [[0, 1, 2], [3, 4, 5], [6]]


def test_timestamped_filename_ends_with_csv():
    name = ei_client.timestamped_filename("bearing_fault", 0)
    assert name.startswith("bearing_fault.0.")
    assert name.endswith(".csv")


def main():
    test_login_returns_token()
    test_login_includes_totp_when_given()
    test_login_raises_totp_required()
    test_login_raises_generic_error_on_bad_credentials()
    test_create_project_uses_jwt_header_and_returns_id_and_key()
    test_create_impulse_declares_a_features_input_block()
    test_set_nn_config_posts_to_learn_id_scoped_url()
    test_request_treats_http_200_with_no_success_field_as_success()
    test_request_raises_on_non_2xx_even_without_json_body()
    test_upload_samples_empty_list_makes_no_request()
    test_upload_samples_posts_one_multipart_part_per_sample()
    test_batched_splits_into_chunks_of_batch_size()
    test_timestamped_filename_ends_with_csv()
    print("RESULT: PASS - ei_client builds correct requests and treats HTTP "
          "status as the authoritative success signal")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
