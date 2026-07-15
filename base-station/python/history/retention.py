"""History retention -- periodic prune of old rows from HistoryStore, so
history.db doesn't grow unbounded now that JSONL's per-line append (and
no read-time scan cost) isn't the storage layer's own natural cap.

No existing scheduler/background-task infrastructure exists in this
codebase (main.py/app.py only have ingestion threads and the ASGI
lifespan hook) -- this follows the same shape as main.py's
start_ingestion/on_startup convention: an asyncio background task
started from create_app's lifespan, rather than pulling in a dependency
(e.g. APScheduler) for a once-a-day housekeeping sweep.
"""
import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Configurable retention window -- how long a history row survives
# before the periodic sweep deletes it. Not hardcoded into the
# scheduler call so it's a one-line change to adjust later.
DEFAULT_RETENTION_SECONDS = 90 * 24 * 60 * 60  # 90 days

# Housekeeping, not a hot path -- once a day is plenty.
_PRUNE_INTERVAL_SECONDS = 24 * 60 * 60


async def run_retention_loop(history_store, retention_seconds: float = DEFAULT_RETENTION_SECONDS,
                              interval_seconds: float = _PRUNE_INTERVAL_SECONDS) -> None:
    """Runs forever (until cancelled), pruning history older than
    retention_seconds every interval_seconds. Intended to be launched
    via asyncio.create_task() from an ASGI lifespan/startup hook."""
    while True:
        cutoff = time.time() - retention_seconds
        deleted = history_store.prune_before(cutoff)
        logger.info("history retention sweep: deleted %d row(s) older than %s",
                    deleted, retention_seconds)
        await asyncio.sleep(interval_seconds)
