#!/usr/bin/env python3
"""AlertStore verification (docs/DASHBOARD_IDEAS_BACKLOG.md's Telegram
alerts item): connect-token mint/consume/expiry/one-shot semantics,
subscriber CRUD + persistence across a reload, and subscribers_for()'s
fault_only/node_ids filtering -- the logic the dashboard's Alerts tab and
alerts/telegram_alerts.py's status-change hook both depend on. No
dependency on the real arduino:telegram_bot brick anywhere in this file.

Run with PYTHONPATH covering base-station/python/alerts:
    PYTHONPATH=base-station/python/alerts python3 base-station/tests/alert_store_test.py
"""
import os
import sys
import tempfile
import time

from alert_store import AlertStore, SubscriberNotFoundError, TOKEN_TTL_SECONDS


def test_connect_token_is_one_shot(tmp_dir):
    store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    token = store.create_connect_token()
    assert store.consume_token(token) is True, "first consume should succeed"
    assert store.consume_token(token) is False, "second consume of the same token should fail"
    print("connect token is one-shot: PASS")


def test_unknown_token_is_rejected(tmp_dir):
    store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    assert store.consume_token("not-a-real-token") is False
    assert store.consume_token("") is False
    print("unknown/empty token is rejected: PASS")


def test_expired_token_is_rejected(tmp_dir, monkeypatch_time):
    store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    token = store.create_connect_token()
    monkeypatch_time(time.time() + TOKEN_TTL_SECONDS + 1)
    assert store.consume_token(token) is False, "expired token must not be consumable"
    print("expired token is rejected: PASS")


def test_add_subscriber_persists_and_defaults(tmp_dir):
    path = os.path.join(tmp_dir, "alerts.json")
    store = AlertStore(path)
    sub = store.add_subscriber(chat_id=111, user_id=222, first_name="Alice", username="alice_tg")
    assert sub.fault_only is False, "fresh subscriber defaults to fault+warning"
    assert sub.node_ids is None, "fresh subscriber defaults to every node"

    reopened = AlertStore(path)
    reloaded = reopened.get_subscriber(111)
    assert reloaded.first_name == "Alice"
    assert reloaded.username == "alice_tg"
    print("add_subscriber persists across reload with sane defaults: PASS")


def test_readd_while_still_subscribed_keeps_prefs(tmp_dir):
    store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    store.add_subscriber(chat_id=111, user_id=222, first_name="Alice")
    store.update_prefs(111, fault_only=True, node_ids=["node-1"])

    # A stale /start deep link tapped again while still subscribed (no
    # disconnect in between) shouldn't silently reset prefs back to
    # defaults.
    resub = store.add_subscriber(chat_id=111, user_id=222, first_name="Alice")
    assert resub.fault_only is True
    assert resub.node_ids == ["node-1"]
    print("re-adding an already-subscribed chat_id keeps its prefs: PASS")


def test_disconnect_then_reconnect_resets_to_defaults(tmp_dir):
    store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    store.add_subscriber(chat_id=111, user_id=222, first_name="Alice")
    store.update_prefs(111, fault_only=True, node_ids=["node-1"])

    # A genuine disconnect (Telegram-side /stop or dashboard button)
    # deletes the entry outright -- a later reconnect is a fresh
    # subscribe, not a resume, so it lands back on defaults.
    store.remove_subscriber(111)
    resub = store.add_subscriber(chat_id=111, user_id=222, first_name="Alice")
    assert resub.fault_only is False
    assert resub.node_ids is None
    print("disconnect then reconnect resets to default prefs: PASS")


def test_remove_subscriber(tmp_dir):
    store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    store.add_subscriber(chat_id=111, user_id=222, first_name="Alice")
    assert store.remove_subscriber(111) is True
    assert store.remove_subscriber(111) is False, "already removed"
    try:
        store.get_subscriber(111)
        assert False, "expected SubscriberNotFoundError"
    except SubscriberNotFoundError:
        pass
    print("remove_subscriber is idempotent and evicts the entry: PASS")


def test_update_prefs_unknown_chat_raises(tmp_dir):
    store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    try:
        store.update_prefs(999, fault_only=True)
        assert False, "expected SubscriberNotFoundError"
    except SubscriberNotFoundError:
        pass
    print("update_prefs on an unknown chat_id raises: PASS")


def test_subscribers_for_filters_by_tier_and_node_scope(tmp_dir):
    store = AlertStore(os.path.join(tmp_dir, "alerts.json"))
    # all-nodes, fault+warning
    store.add_subscriber(chat_id=1, user_id=1, first_name="AllNodesBoth")
    # all-nodes, fault-only
    store.add_subscriber(chat_id=2, user_id=2, first_name="AllNodesFaultOnly")
    store.update_prefs(2, fault_only=True)
    # node-1 only, fault+warning
    store.add_subscriber(chat_id=3, user_id=3, first_name="Node1Only")
    store.update_prefs(3, fault_only=False, node_ids=["node-1"])

    warning_node1 = {s.chat_id for s in store.subscribers_for("node-1", "warning")}
    assert warning_node1 == {1, 3}, warning_node1

    fault_node1 = {s.chat_id for s in store.subscribers_for("node-1", "fault")}
    assert fault_node1 == {1, 2, 3}, fault_node1

    warning_node2 = {s.chat_id for s in store.subscribers_for("node-2", "warning")}
    assert warning_node2 == {1}, warning_node2

    fault_node2 = {s.chat_id for s in store.subscribers_for("node-2", "fault")}
    assert fault_node2 == {1, 2}, fault_node2

    print("subscribers_for filters by fault_only tier and node_ids scope: PASS")


def main():
    tmp_dir = tempfile.mkdtemp(prefix="alert_store_test_")

    # Minimal monkeypatch for time.time() so the TTL test doesn't need a
    # real 15-minute sleep -- patches the module time.py imports from,
    # restored immediately after use.
    import alert_store as alert_store_module
    real_time = time.time

    def monkeypatch_time(fake_now):
        alert_store_module.time.time = lambda: fake_now

    try:
        test_connect_token_is_one_shot(tempfile.mkdtemp(dir=tmp_dir))
        test_unknown_token_is_rejected(tempfile.mkdtemp(dir=tmp_dir))
        test_expired_token_is_rejected(tempfile.mkdtemp(dir=tmp_dir), monkeypatch_time)
        test_add_subscriber_persists_and_defaults(tempfile.mkdtemp(dir=tmp_dir))
        test_readd_while_still_subscribed_keeps_prefs(tempfile.mkdtemp(dir=tmp_dir))
        test_disconnect_then_reconnect_resets_to_defaults(tempfile.mkdtemp(dir=tmp_dir))
        test_remove_subscriber(tempfile.mkdtemp(dir=tmp_dir))
        test_update_prefs_unknown_chat_raises(tempfile.mkdtemp(dir=tmp_dir))
        test_subscribers_for_filters_by_tier_and_node_scope(tempfile.mkdtemp(dir=tmp_dir))
    finally:
        alert_store_module.time.time = real_time

    print("RESULT: PASS - AlertStore token/subscriber/filtering logic all behave as expected")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
