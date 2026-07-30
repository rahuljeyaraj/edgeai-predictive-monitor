"""telegram_alerts.py -- wires the official `arduino:telegram_bot` brick to
AlertStore + Registry, per docs/DASHBOARD_IDEAS_BACKLOG.md's Telegram
alerts item.

Only this module touches `arduino.app_bricks.telegram_bot` -- and only
inside build_telegram_bot()/wire_telegram_alerts(), never at module import
time, so importing this file (and everything that transitively imports
it, e.g. main.py) still works on a dev machine with no App Lab container
and no `python-telegram-bot` installed. Same convention as main.py's own
`from arduino.app_utils import Bridge`, deferred inside
wire_local_status_led() rather than imported at module level.

The brick's own enable_builtin_welcome=True /start handler only ever
replies with the user's chat_id/user_id -- it has no way to see a
`?start=<token>` deep-link payload, which is what actually matches a
Telegram chat back to the dashboard session that requested the connection
(docs/DASHBOARD_IDEAS_BACKLOG.md's flow). So this registers its own /start
command instead, left disabled on the brick itself.
"""
import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Callable, Optional

from alert_store import AlertStore
from registry import NodeStatus, Registry

logger = logging.getLogger(__name__)

# NodeStatus values that ever trigger a Telegram notification, and which
# alert tier they count as (AlertStore.subscribers_for's fault_only
# filter). Every other status -- uncommissioned/commissioning_*/paused,
# and OFFLINE, which is frontend-only per registry.py's own comment
# (nothing server-side ever sets it) -- is silent: those are
# operator-driven or in-progress states, not fleet-health events worth
# paging someone over.
_ALERT_TIER = {
    NodeStatus.WARNING: "warning",
    NodeStatus.FAULT: "fault",
}

_RECOVERABLE_FROM = {NodeStatus.WARNING, NodeStatus.FAULT}

_STATUS_LABEL = {
    NodeStatus.WARNING: "⚠️ WARNING",
    NodeStatus.FAULT: "\U0001f534 FAULT",
}


def build_telegram_bot():
    """Constructs the arduino:telegram_bot brick. Reads TELEGRAM_BOT_TOKEN
    from the environment (brick-managed secret, set via App Lab's UI once
    the brick is declared in app.yaml's `bricks:` list) -- raises
    ValueError if unset, same as the brick's own __init__. Callers should
    only call this once TELEGRAM_BOT_TOKEN is already confirmed present
    (main.py checks before wiring at all), so that ValueError should never
    actually fire in practice."""
    from arduino.app_bricks.telegram_bot import TelegramBot
    return TelegramBot()


def fetch_bot_username(token: str, timeout: float = 10.0) -> str:
    """Calls Telegram's own getMe API directly (not the brick -- it has no
    accessor for this) to resolve the bot's @username from its token, so
    the dashboard's "Connect Telegram" deep link (t.me/<username>?start=
    <token>) never needs a second, separately-configured
    TELEGRAM_BOT_USERNAME value that could drift from the token actually
    in use. Raises OSError/ValueError on any network or API failure --
    callers should treat that as "Telegram alerts unavailable this boot"
    rather than crashing startup over it."""
    url = f"https://api.telegram.org/bot{token}/getMe"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    if not payload.get("ok"):
        raise ValueError(f"Telegram getMe failed: {payload}")
    return payload["result"]["username"]


def wire_telegram_alerts(registry: Registry, bot, alert_store: AlertStore,
                          on_subscriber_change: Optional[Callable[[], None]] = None) -> None:
    """Registers on `bot`:
      - a custom /start <token> handler that redeems the dashboard's
        connect-token (AlertStore.consume_token) and registers the
        subscriber;
      - a /stop handler so a subscriber can disconnect from the Telegram
        side, symmetric with the dashboard's own disconnect button;
    and on `registry`:
      - an on_status_change listener that messages every matching
        subscriber (AlertStore.subscribers_for) on entering WARNING/FAULT,
        and sends an "all clear" on recovering to HEALTHY from either.

    `on_subscriber_change`, if given, fires after every connect/disconnect
    so a caller (main.py) can push a live update to the dashboard over
    /ws -- this runs on the bot's own handler thread, not the FastAPI
    event loop, so it must itself be threadsafe (broadcast_threadsafe is).

    Registry only calls on_status_change for a transition the state
    machine actually allowed (_NodeStateMachine has no self-loop
    transitions -- see registry.py), so every call here is already a
    genuine change. That, plus the frame-level status_debounce_frames
    upstream of it (pipeline/manager.py), is what "debounced against
    flapping" (docs/DASHBOARD_IDEAS_BACKLOG.md) means here -- no separate
    cooldown layer is added on top.
    """
    _last_status: dict = {}

    def _handle_start(sender, message) -> None:
        token = (message.text or "").partition(" ")[2].strip()
        if not token or not alert_store.consume_token(token):
            sender.reply(
                "This connect link has expired or wasn't opened from the dashboard. "
                "Go to the Alerts tab and tap “Connect Telegram” again.")
            return
        alert_store.add_subscriber(
            chat_id=sender.chat_id, user_id=sender.user_id,
            first_name=sender.first_name, username=sender.username)
        sender.reply(
            f"✅ Connected, {sender.first_name}! You'll get a message here whenever "
            "a node enters warning/fault, or recovers. Manage which nodes and alert "
            "level from the dashboard's Alerts tab, or send /stop to disconnect.")
        if on_subscriber_change is not None:
            on_subscriber_change()

    def _handle_stop(sender, message) -> None:
        removed = alert_store.remove_subscriber(sender.chat_id)
        if removed:
            sender.reply("Disconnected -- you won't get any more alerts here.")
            if on_subscriber_change is not None:
                on_subscriber_change()
        else:
            sender.reply("This chat wasn't subscribed.")

    bot.add_command("start", _handle_start, "Connect this chat to EdgeAI alerts")
    bot.add_command("stop", _handle_stop, "Disconnect this chat from EdgeAI alerts")

    def on_status_change(node_id: str, status: NodeStatus) -> None:
        previous = _last_status.get(node_id)
        _last_status[node_id] = status

        tier = _ALERT_TIER.get(status)
        if tier is not None:
            text = f"{_STATUS_LABEL[status]} — {node_id}"
            recipients = alert_store.subscribers_for(node_id, tier)
        elif status == NodeStatus.HEALTHY and previous in _RECOVERABLE_FROM:
            text = f"✅ RECOVERED — {node_id} is back to healthy"
            # An "all clear" is exactly as urgent as the event it clears --
            # a fault-only subscriber who got the original FAULT message
            # should get its recovery too, and a warning's recovery
            # respects fault_only the same way the warning itself did.
            recipients = alert_store.subscribers_for(
                node_id, "fault" if previous == NodeStatus.FAULT else "warning")
        else:
            return

        if not recipients:
            return

        def send() -> None:
            for sub in recipients:
                try:
                    bot.send_message(sub.chat_id, text)
                except Exception:
                    logger.exception("failed to send Telegram alert to chat_id=%r", sub.chat_id)

        # Off the registry lock / frame-ingestion path: send_message is a
        # blocking network call (with its own internal retries), and
        # on_status_change fires from inside PipelineManager.route()'s
        # per-node lock (manager.py) -- a slow/broken Telegram network call
        # must never stall inference for every node sharing that path.
        threading.Thread(target=send, daemon=True).start()

    registry.on_status_change(on_status_change)
