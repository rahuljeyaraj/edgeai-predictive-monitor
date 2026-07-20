"""AlertStore -- persistence for Telegram alert subscribers and pending
connect tokens, per docs/DASHBOARD_IDEAS_BACKLOG.md's Telegram alerts item.

Disk-backed JSON, same shape/durability contract as registry.py (atomic
temp-file-then-rename write, survives a process restart). Deliberately has
no dependency on the `arduino.app_bricks.telegram_bot` brick or on
registry.py's NodeStatus -- this module is pure bookkeeping, testable on
any machine, importable from telegram_alerts.py (which does own the brick
dependency) and api/app.py's REST routes alike.

Pending tokens are NOT persisted to disk: a token survives only in memory,
and a process restart invalidates every in-flight "Connect Telegram" click
(the dashboard's token was minted for this run; a stale token from before
a restart has no session behind it to match anyway, and TOKEN_TTL_SECONDS
already expires it on its own in the common case).
"""
import json
import os
import secrets
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

TOKEN_TTL_SECONDS = 15 * 60

# Sentinel distinguishing "node_ids not provided" (leave as-is) from an
# explicit "node_ids=None" (meaning "all nodes" -- a legitimate value, not
# an unset one) in update_prefs() below.
_UNSET = object()


class SubscriberNotFoundError(KeyError):
    pass


@dataclass
class Subscriber:
    chat_id: int
    user_id: int
    first_name: str
    username: Optional[str] = None
    connected_at: float = field(default_factory=time.time)
    # False = fault+warning (the default a fresh /start lands on); True =
    # fault-only. None = every node (the default); otherwise an explicit
    # allow-list of node_ids.
    fault_only: bool = False
    node_ids: Optional[List[str]] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Subscriber":
        return Subscriber(**d)


class AlertStore:
    """CRUD over Subscriber + connect-token bookkeeping, persisted to a
    single JSON file. Every mutating call writes the full file back out
    immediately, matching registry.py's write-through (not write-behind)
    choice -- subscriber changes are rare next to reads."""

    def __init__(self, path: str):
        self._path = path
        self._subscribers: Dict[str, Subscriber] = {}
        self._pending_tokens: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        with open(self._path, "r") as f:
            raw = json.load(f)
        self._subscribers = {chat_id: Subscriber.from_dict(entry)
                              for chat_id, entry in raw.get("subscribers", {}).items()}

    def _save(self) -> None:
        # Same atomic write pattern as registry.py's _save(): write to a
        # temp file in the same directory, then os.replace() -- a crash
        # mid-write can't leave a truncated/corrupt file behind.
        directory = os.path.dirname(self._path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".alerts-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"subscribers": {chat_id: sub.to_dict()
                                            for chat_id, sub in self._subscribers.items()}},
                          f, indent=2)
            os.replace(tmp_path, self._path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def _prune_expired_tokens(self, now: float) -> None:
        expired = [t for t, created_at in self._pending_tokens.items()
                   if now - created_at > TOKEN_TTL_SECONDS]
        for t in expired:
            del self._pending_tokens[t]

    def create_connect_token(self) -> str:
        """Mints a one-time token for the dashboard's "Connect Telegram"
        deep link (t.me/<bot>?start=<token>). Not persisted -- see module
        docstring."""
        with self._lock:
            self._prune_expired_tokens(time.time())
            token = secrets.token_urlsafe(16)
            self._pending_tokens[token] = time.time()
            return token

    def consume_token(self, token: str) -> bool:
        """One-shot: a token is valid exactly once, whether or not it's
        expired. Returns whether `token` was a live, unexpired pending
        token at call time."""
        with self._lock:
            now = time.time()
            self._prune_expired_tokens(now)
            created_at = self._pending_tokens.pop(token, None)
            return created_at is not None

    def add_subscriber(self, chat_id: int, user_id: int, first_name: str,
                        username: Optional[str] = None) -> Subscriber:
        """Registers a chat as a subscriber, defaulting to fault+warning on
        every node. Re-adding an already-subscribed chat_id (e.g. a stale
        /start deep link tapped again without disconnecting first) keeps
        its current fault_only/node_ids rather than resetting them --
        remove_subscriber() deletes the entry outright, so a genuine
        disconnect really does forget prefs; only this no-op-ish
        double-/start path preserves them."""
        with self._lock:
            key = str(chat_id)
            existing = self._subscribers.get(key)
            sub = Subscriber(
                chat_id=chat_id, user_id=user_id, first_name=first_name, username=username,
                fault_only=existing.fault_only if existing else False,
                node_ids=existing.node_ids if existing else None,
            )
            self._subscribers[key] = sub
            self._save()
            return sub

    def remove_subscriber(self, chat_id) -> bool:
        with self._lock:
            key = str(chat_id)
            if key not in self._subscribers:
                return False
            del self._subscribers[key]
            self._save()
            return True

    def get_subscriber(self, chat_id) -> Subscriber:
        with self._lock:
            try:
                return self._subscribers[str(chat_id)]
            except KeyError:
                raise SubscriberNotFoundError(chat_id)

    def update_prefs(self, chat_id, fault_only: Optional[bool] = None,
                      node_ids=_UNSET) -> Subscriber:
        with self._lock:
            sub = self.get_subscriber(chat_id)
            if fault_only is not None:
                sub.fault_only = fault_only
            if node_ids is not _UNSET:
                sub.node_ids = node_ids
            self._save()
            return sub

    def list_subscribers(self) -> Dict[str, Subscriber]:
        with self._lock:
            return dict(self._subscribers)

    def subscribers_for(self, node_id: str, tier: str) -> List[Subscriber]:
        """Subscribers who should be notified for an event of this tier on
        `node_id`. tier="fault" always matches; tier="warning" excludes
        fault_only subscribers. node_ids=None on a subscriber means "every
        node"."""
        with self._lock:
            matches = []
            for sub in self._subscribers.values():
                if tier == "warning" and sub.fault_only:
                    continue
                if sub.node_ids is not None and node_id not in sub.node_ids:
                    continue
                matches.append(sub)
            return matches
