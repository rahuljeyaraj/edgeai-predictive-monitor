#!/usr/bin/env python3
"""telegram_alerts.py verification (docs/DASHBOARD_IDEAS_BACKLOG.md's
Telegram alerts item): the /start-token-redemption flow, /stop, and the
registry.on_status_change -> bot.send_message wiring -- fault_only/
node_ids filtering, and the "recovered to healthy" all-clear.

Drives everything through a FakeBot double (add_command/send_message only,
no real network) so this never imports arduino.app_bricks.telegram_bot --
that package only exists inside the on-device App Lab container. Alert
sends run on a background thread (telegram_alerts.py's own design, so a
slow network call never blocks the registry lock); tests poll briefly for
the expected send(s) to land instead of assuming synchronous delivery.

Run with PYTHONPATH covering base-station/python/registry, .../alerts:
    PYTHONPATH=base-station/python/registry:base-station/python/alerts \\
        python3 base-station/tests/telegram_alerts_test.py
"""
import io
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request

from registry import NodeStatus, Registry, SensorChannel
from alert_store import AlertStore
from telegram_alerts import fetch_bot_username, wire_telegram_alerts


class FakeSender:
    def __init__(self, bot, chat_id, user_id, first_name, username=None):
        self._bot = bot
        self.chat_id = chat_id
        self.user_id = user_id
        self.first_name = first_name
        self.username = username

    def reply(self, text):
        self._bot.send_message(self.chat_id, text)
        return True


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.caption = None


class FakeBot:
    """Duck-types just the surface wire_telegram_alerts() touches:
    add_command() + send_message() -- everything the real
    arduino:telegram_bot brick offers beyond that (media, scheduling,
    polling threads) is irrelevant to this wiring logic."""

    def __init__(self):
        self.commands = {}
        self.sent = []  # list of (chat_id, text)

    def add_command(self, command, callback, description=""):
        self.commands[command] = callback

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return True

    def deliver_start(self, chat_id, user_id, first_name, token, username=None):
        sender = FakeSender(self, chat_id, user_id, first_name, username)
        self.commands["start"](sender, FakeMessage(f"/start {token}"))
        return sender

    def deliver_stop(self, chat_id, user_id, first_name):
        sender = FakeSender(self, chat_id, user_id, first_name)
        self.commands["stop"](sender, FakeMessage("/stop"))


def _wait_for(predicate, timeout=2.0):
    # Alert sends are dispatched on a daemon thread (telegram_alerts.py's
    # wire_telegram_alerts) so they never block the registry lock the
    # on_status_change callback fires under -- give that thread a moment
    # to actually run rather than asserting immediately.
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_start_with_valid_token_registers_subscriber(tmp_dir):
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    alert_store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    bot = FakeBot()
    wire_telegram_alerts(registry, bot, alert_store)

    token = alert_store.create_connect_token()
    bot.deliver_start(chat_id=111, user_id=222, first_name="Alice", token=token, username="alice_tg")

    sub = alert_store.get_subscriber(111)
    assert sub.first_name == "Alice"
    assert sub.username == "alice_tg"
    assert any("Connected" in text for _, text in bot.sent), bot.sent
    print("/start with a valid token registers the subscriber and replies: PASS")


def test_start_with_invalid_token_does_not_register(tmp_dir):
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    alert_store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    bot = FakeBot()
    wire_telegram_alerts(registry, bot, alert_store)

    bot.deliver_start(chat_id=111, user_id=222, first_name="Alice", token="bogus")

    try:
        alert_store.get_subscriber(111)
        assert False, "should not have been registered"
    except Exception:
        pass
    assert any("expired" in text for _, text in bot.sent), bot.sent
    print("/start with an invalid token is rejected: PASS")


def test_on_subscriber_change_fires_on_connect_and_disconnect(tmp_dir):
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    alert_store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    bot = FakeBot()
    calls = []
    wire_telegram_alerts(registry, bot, alert_store, on_subscriber_change=lambda: calls.append(1))

    token = alert_store.create_connect_token()
    bot.deliver_start(chat_id=111, user_id=222, first_name="Alice", token=token)
    assert len(calls) == 1, calls

    bot.deliver_stop(chat_id=111, user_id=222, first_name="Alice")
    assert len(calls) == 2, calls
    print("on_subscriber_change fires on connect and on /stop: PASS")


def test_stop_unsubscribes(tmp_dir):
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    alert_store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    bot = FakeBot()
    wire_telegram_alerts(registry, bot, alert_store)

    token = alert_store.create_connect_token()
    bot.deliver_start(chat_id=111, user_id=222, first_name="Alice", token=token)
    bot.deliver_stop(chat_id=111, user_id=222, first_name="Alice")

    try:
        alert_store.get_subscriber(111)
        assert False, "should have been removed"
    except Exception:
        pass
    print("/stop unsubscribes: PASS")


def test_warning_and_fault_alert_matching_subscribers(tmp_dir):
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    alert_store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    bot = FakeBot()
    wire_telegram_alerts(registry, bot, alert_store)

    # subscriber 1: fault+warning, every node. subscriber 2: fault-only.
    alert_store.add_subscriber(chat_id=1, user_id=1, first_name="Both")
    alert_store.add_subscriber(chat_id=2, user_id=2, first_name="FaultOnly")
    alert_store.update_prefs(2, fault_only=True)

    registry.add("node-1", sensor_config=frozenset({SensorChannel.MIC}))
    registry.start_commissioning("node-1")
    registry.stop_collecting("node-1")
    registry.complete_commissioning("node-1", model_path="unused.pt")  # -> HEALTHY

    registry.set_status("node-1", NodeStatus.WARNING)
    assert _wait_for(lambda: any(c == 1 for c, _ in bot.sent)), bot.sent
    assert not any(c == 2 for c, _ in bot.sent), "fault_only subscriber must not get a WARNING alert"

    bot.sent.clear()
    registry.set_status("node-1", NodeStatus.FAULT)
    assert _wait_for(lambda: {c for c, _ in bot.sent} == {1, 2}), bot.sent
    print("WARNING alerts respect fault_only, FAULT alerts reach everyone in scope: PASS")


def test_node_scope_filters_alerts(tmp_dir):
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    alert_store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    bot = FakeBot()
    wire_telegram_alerts(registry, bot, alert_store)

    alert_store.add_subscriber(chat_id=1, user_id=1, first_name="Node1Only")
    alert_store.update_prefs(1, fault_only=False, node_ids=["node-1"])

    registry.add("node-2", sensor_config=frozenset({SensorChannel.MIC}))
    registry.start_commissioning("node-2")
    registry.stop_collecting("node-2")
    registry.complete_commissioning("node-2", model_path="unused.pt")

    registry.set_status("node-2", NodeStatus.FAULT)
    time.sleep(0.1)
    assert bot.sent == [], f"subscriber scoped to node-1 must not hear about node-2: {bot.sent}"
    print("node_ids scope excludes out-of-scope nodes: PASS")


def test_recovery_to_healthy_sends_all_clear(tmp_dir):
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    alert_store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    bot = FakeBot()
    wire_telegram_alerts(registry, bot, alert_store)
    alert_store.add_subscriber(chat_id=1, user_id=1, first_name="Both")

    registry.add("node-1", sensor_config=frozenset({SensorChannel.MIC}))
    registry.start_commissioning("node-1")
    registry.stop_collecting("node-1")
    registry.complete_commissioning("node-1", model_path="unused.pt")  # HEALTHY, no alert expected

    registry.set_status("node-1", NodeStatus.FAULT)
    assert _wait_for(lambda: len(bot.sent) == 1), bot.sent

    bot.sent.clear()
    registry.set_status("node-1", NodeStatus.HEALTHY)
    assert _wait_for(lambda: len(bot.sent) == 1), bot.sent
    assert "RECOVERED" in bot.sent[0][1], bot.sent
    print("recovering from FAULT to HEALTHY sends an all-clear: PASS")


def test_no_alert_on_initial_commissioning_to_healthy(tmp_dir):
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    alert_store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    bot = FakeBot()
    wire_telegram_alerts(registry, bot, alert_store)
    alert_store.add_subscriber(chat_id=1, user_id=1, first_name="Both")

    registry.add("node-1", sensor_config=frozenset({SensorChannel.MIC}))
    registry.start_commissioning("node-1")
    registry.stop_collecting("node-1")
    registry.complete_commissioning("node-1", model_path="unused.pt")  # UNCOMMISSIONED-ish -> HEALTHY

    time.sleep(0.1)
    assert bot.sent == [], f"first-ever HEALTHY (never having been warning/fault) must be silent: {bot.sent}"
    print("commissioning straight to HEALTHY (never warning/fault) sends nothing: PASS")


class FakeResponse:
    def __init__(self, body_bytes):
        self._body = body_bytes

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_fetch_bot_username_parses_getme_result():
    original = urllib.request.urlopen
    try:
        body = json.dumps({"ok": True, "result": {"id": 1, "username": "my_cool_bot"}}).encode()
        urllib.request.urlopen = lambda url, timeout=None: FakeResponse(body)
        assert fetch_bot_username("fake-token") == "my_cool_bot"
        print("fetch_bot_username parses a successful getMe response: PASS")
    finally:
        urllib.request.urlopen = original


def test_fetch_bot_username_raises_on_api_error():
    original = urllib.request.urlopen
    try:
        body = json.dumps({"ok": False, "error_code": 401, "description": "Unauthorized"}).encode()
        urllib.request.urlopen = lambda url, timeout=None: FakeResponse(body)
        try:
            fetch_bot_username("bad-token")
            assert False, "expected ValueError on ok=False"
        except ValueError:
            pass
        print("fetch_bot_username raises on ok=False (e.g. bad token): PASS")
    finally:
        urllib.request.urlopen = original


def test_fetch_bot_username_raises_on_network_error():
    original = urllib.request.urlopen

    def raise_http_error(url, timeout=None):
        raise urllib.error.HTTPError(url, 500, "error", {}, io.BytesIO(b""))

    try:
        urllib.request.urlopen = raise_http_error
        try:
            fetch_bot_username("any-token")
            assert False, "expected an exception on a failed request"
        except urllib.error.HTTPError:
            pass
        print("fetch_bot_username propagates a failed request (caller decides how to handle): PASS")
    finally:
        urllib.request.urlopen = original


def main():
    tmp_dir = tempfile.mkdtemp(prefix="telegram_alerts_test_")

    test_start_with_valid_token_registers_subscriber(tempfile.mkdtemp(dir=tmp_dir))
    test_start_with_invalid_token_does_not_register(tempfile.mkdtemp(dir=tmp_dir))
    test_on_subscriber_change_fires_on_connect_and_disconnect(tempfile.mkdtemp(dir=tmp_dir))
    test_stop_unsubscribes(tempfile.mkdtemp(dir=tmp_dir))
    test_warning_and_fault_alert_matching_subscribers(tempfile.mkdtemp(dir=tmp_dir))
    test_node_scope_filters_alerts(tempfile.mkdtemp(dir=tmp_dir))
    test_recovery_to_healthy_sends_all_clear(tempfile.mkdtemp(dir=tmp_dir))
    test_no_alert_on_initial_commissioning_to_healthy(tempfile.mkdtemp(dir=tmp_dir))
    test_fetch_bot_username_parses_getme_result()
    test_fetch_bot_username_raises_on_api_error()
    test_fetch_bot_username_raises_on_network_error()

    print("RESULT: PASS - telegram_alerts.py's /start /stop handling and status-change "
          "alert wiring all behave as expected")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
