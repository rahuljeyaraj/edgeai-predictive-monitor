"""One process-wide lock serializing every arduino.app_utils.Bridge.call()
site (ingestion/spi_reader.py's spi_arm loop, main.py's local status LED
push) -- Bridge itself does nothing to protect the shared UART from two
Python threads round-tripping at once, and this board's Bridge link is
already documented (docs/PROGRESS.md) to wedge under far milder contention
than that. Hold this only around the Bridge.call itself, not around
retries/sleeps, so one caller never starves another for longer than a
single round trip.

Kept as standing protection even though today's two call sites happen to
run on the same thread (no actual concurrency to guard against right now):
this codebase has hit "two threads calling Bridge concurrently wedges the
UART" twice already (the original notify-stream incident, and a since-
removed monitoring/mcu_perf.py poller that briefly introduced a second
Bridge-calling thread) -- the next poller that gets added should inherit
this protection for free rather than silently reintroduce the bug a third
time.
"""
import threading

BRIDGE_LOCK = threading.Lock()
