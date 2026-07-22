"""One process-wide lock serializing every arduino.app_utils.Bridge.call()
site (ingestion/spi_reader.py's spi_arm loop, main.py's local status LED +
LED matrix pushes) -- Bridge itself does nothing to protect the shared UART
from two Python threads round-tripping at once, and this board's Bridge
link is already documented (docs/PROGRESS.md) to wedge under far milder
contention than that. Hold this only around the Bridge.call itself, not
around retries/sleeps, so one caller never starves another for longer than
a single round trip.

Real concurrency, not just standing protection: spi_reader.py's SPI-
consumer thread grabs this on every single frame pull (every ~20ms,
forever), while main.py's wire_local_status_led/wire_local_matrix_text
Bridge.call()s fire from Registry.on_status_change -- invoked synchronously
from whichever thread routed the status-changing frame (the SPI-consumer
thread for this device's own sensors, or the MQTT client thread for a
satellite node). Both of those push their actual Bridge.call() onto a
background thread specifically so acquiring this lock never blocks the
frame-ingestion thread itself (2026-07-22 fix: a synchronous call there
froze the whole dashboard for several seconds on every status transition,
contending with the SPI-consumer thread's constant re-acquisition). This
codebase has hit "two threads calling Bridge concurrently wedges the UART"
more than once already (the original notify-stream incident, and a since-
removed monitoring/mcu_perf.py poller that briefly introduced a second
Bridge-calling thread) -- the next Bridge caller that gets added should
inherit this protection for free rather than silently reintroduce the bug
again.
"""
import threading

BRIDGE_LOCK = threading.Lock()
