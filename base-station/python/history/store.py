"""History store -- anomaly-score + status time series per node, per
docs/MPU_Software_Architecture.md S3.4/S4.3/S8 M9. Read by the
dashboard's per-motor graphs (S3.9): anomaly score over time, and
(alongside retained raw bins) waterfall reconstruction.

SQLite-backed: indexed per-node queries instead of a full-file scan,
and a real retention policy (see history/retention.py) instead of an
ever-growing file. One long-lived connection is opened at construction
and kept for the store's lifetime -- this is a single-process,
single-writer setup (registry.py documents the same assumption), so
there's no need to open/close per call. WAL mode is set explicitly so
dashboard reads aren't blocked by a write in progress.
"""
import sqlite3
import threading
from dataclasses import asdict, dataclass
from typing import List, Optional

from registry import NodeStatus


@dataclass
class HistoryRecord:
    node_id: str
    timestamp: float
    anomaly_score: float
    status_at_time: NodeStatus

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status_at_time"] = self.status_at_time.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "HistoryRecord":
        d = dict(d)
        d["status_at_time"] = NodeStatus(d["status_at_time"])
        return HistoryRecord(**d)


class HistoryStore:
    """One store covers every motor -- records are tagged by node_id and
    filtered on read (S4.3), via an indexed (node_id, timestamp) query
    rather than a full scan."""

    def __init__(self, path: str):
        self._path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        # check_same_thread=False only lifts sqlite3's same-thread check --
        # it doesn't make one connection safe for concurrent use. A
        # multi-node MQTT fleet still routes every node's frames through
        # this same shared connection, so a lock around each call is
        # required, not optional (concurrent writes across nodes overlap
        # often enough to raise "sqlite3.InterfaceError: bad parameter or
        # other API misuse" otherwise).
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                node_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                anomaly_score REAL NOT NULL,
                status_at_time TEXT NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_node_ts ON history (node_id, timestamp)")
        self._conn.commit()

    def record(self, node_id: str, timestamp: float, anomaly_score: float,
               status_at_time: NodeStatus) -> HistoryRecord:
        rec = HistoryRecord(node_id=node_id, timestamp=timestamp,
                             anomaly_score=anomaly_score, status_at_time=status_at_time)
        with self._lock:
            self._conn.execute(
                "INSERT INTO history (node_id, timestamp, anomaly_score, status_at_time) "
                "VALUES (?, ?, ?, ?)",
                (node_id, timestamp, anomaly_score, status_at_time.value))
            self._conn.commit()
        return rec

    def query(self, node_id: str) -> List[HistoryRecord]:
        """All records for one motor, ordered by timestamp."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT node_id, timestamp, anomaly_score, status_at_time "
                "FROM history WHERE node_id = ? ORDER BY timestamp",
                (node_id,))
            rows = cur.fetchall()
        return [
            HistoryRecord(node_id=row[0], timestamp=row[1], anomaly_score=row[2],
                          status_at_time=NodeStatus(row[3]))
            for row in rows
        ]

    def delete(self, node_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM history WHERE node_id = ?", (node_id,))
            self._conn.commit()

    def prune_before(self, cutoff_timestamp: float, node_id: Optional[str] = None) -> int:
        """Delete rows older than cutoff_timestamp, optionally scoped to
        one node_id. Returns the number of rows deleted."""
        with self._lock:
            if node_id is None:
                cur = self._conn.execute(
                    "DELETE FROM history WHERE timestamp < ?", (cutoff_timestamp,))
            else:
                cur = self._conn.execute(
                    "DELETE FROM history WHERE timestamp < ? AND node_id = ?",
                    (cutoff_timestamp, node_id))
            self._conn.commit()
            return cur.rowcount

    def count(self, node_id: Optional[str] = None) -> int:
        with self._lock:
            if node_id is None:
                cur = self._conn.execute("SELECT COUNT(*) FROM history")
            else:
                cur = self._conn.execute("SELECT COUNT(*) FROM history WHERE node_id = ?", (node_id,))
            return cur.fetchone()[0]
