"""Keeps every physical status readout in sync with the registry's CURRENT
state, rather than only with status *transitions*.

Why this exists (bug found 2026-08-18): the LED wiring used to be purely
edge-triggered -- main.py registered a Registry.on_status_change callback
and pushed a color from inside it, and that was the only thing that ever
drove a ring. Registry._load() fires no such event and Registry.add()
returns early for a node that already exists, so after any restart with a
pre-existing registry.json NOTHING was ever pushed:

  - The base station's own ring blanks itself on MCU boot
    (rgb_display.cpp's rgb_pwm_show(0,0,0), since WS2812 pixels otherwise
    keep their last latch across a reflash) and then sat dark forever
    while the dashboard happily showed "idle" -- the status never
    *changed*, so no event, so no set_rgb.
  - A satellite that reconnected kept showing its own local
    RGB_MQTT_DOWN blue (transport_task.cpp deliberately has no "connected"
    color of its own -- it relies on the base station pushing the real
    NodeStatus color "moments later", which with edge-only wiring never
    came) while the dashboard showed it cyan/new.
  - "offline" never helped either: it's a frontend-computed staleness
    label (registry.py's own note on NodeStatus.OFFLINE), so a node going
    quiet and coming back produces no server-side transition at all.

The fix is to treat a readout as DESIRED STATE to be reconciled, not as a
stream of events to be replayed. This class owns one background thread
that repeatedly compares each sink's last successfully-pushed value
against what the registry says now, and pushes only the difference.

Two properties that fall out of that, both load-bearing:

  - It is self-healing. A push that fails (Bridge RPC attempted before the
    MCU finished booting, MQTT not connected yet) simply isn't recorded as
    pushed, so the next tick tries again. No push-once-and-hope.
  - It never re-pushes an unchanged value. That matters because BREATHE/
    STROBE commands restart their animation phase on every set (see
    transport_task.cpp's own mqtt_led dedupe for the same reasoning): a
    naive "re-assert everything every N seconds" keeper would make every
    non-const status visibly stutter on a fixed period. Diffing means a
    steady color is pushed exactly once and then left alone.

on_status_change stays wired as well, but only as a latency optimization:
it wakes the thread instead of waiting out the tick interval. It
deliberately does NO work of its own -- it fires from inside
PipelineManager.route()'s per-node lock on the frame-ingestion thread, so
anything blocking there (Bridge.call waits on BRIDGE_LOCK, the same lock
the SPI consumer takes on every frame pull) would stall ingestion
fleet-wide. That used to be handled by spawning a thread per event; the
keeper's own thread replaces them all.
"""
import logging
import threading

from status_color import color_for

logger = logging.getLogger("led_keeper")

# How often to reconcile when everything is already in sync. A tick with
# no drift is a handful of dict comparisons and no I/O at all, so this can
# stay short enough that a sink appearing late (MCU still booting, MQTT
# still connecting) is picked up promptly.
RECONCILE_INTERVAL_S = 5.0
# Faster retry cadence while at least one sink is still failing.
RETRY_INTERVAL_S = 2.0


class StatusLedKeeper:
    """Reconciles registered sinks against Registry state.

    A sink is a physical readout: this board's RGB ring, a satellite's ring
    over MQTT, this board's LED matrix. Each is registered with a `render`
    that turns registry state into a value, and a `push` that puts that
    value on the hardware. `push` MUST raise on failure -- a silent
    failure would be recorded as successfully pushed and never retried.
    """

    def __init__(self, registry, interval_s: float = RECONCILE_INTERVAL_S,
                 retry_interval_s: float = RETRY_INTERVAL_S):
        self._registry = registry
        self._interval_s = interval_s
        self._retry_interval_s = retry_interval_s
        self._sinks: list = []
        # (sink name, node_id) -> last value confirmed pushed to hardware.
        self._pushed: dict = {}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        registry.on_status_change(self._on_status_change)

    def add_node_sink(self, name: str, push, accepts=None) -> None:
        """Registers a per-node readout: `push(node_id, LedCommand)` is
        called whenever that node's status color differs from what this
        sink last had. `accepts(node_id)` filters which nodes belong to
        this sink (the local ring takes only base_station; MQTT takes
        everything else)."""
        self._sinks.append(("node", name, push, accepts))

    def add_fleet_sink(self, name: str, render, push) -> None:
        """Registers a whole-fleet readout: `render(entries)` builds a
        value from every registry entry and `push(value)` displays it,
        pushed only when that value changes. The LED matrix's rolling
        fleet-health summary is the one of these."""
        self._sinks.append(("fleet", name, push, render))

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="led-keeper")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _on_status_change(self, node_id: str, status) -> None:
        # Non-blocking by design -- see this module's docstring. All the
        # real work happens on the keeper thread.
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                in_sync = self.reconcile()
            except Exception:  # noqa: BLE001 - a keeper crash would silently freeze every readout
                logger.exception("LED reconcile tick failed")
                in_sync = False
            self._wake.wait(self._interval_s if in_sync else self._retry_interval_s)
            self._wake.clear()

    def reconcile(self) -> bool:
        """Pushes whatever has drifted. Returns True when every sink is
        confirmed in sync (nothing left to retry)."""
        entries = self._registry.list()
        in_sync = True
        for kind, name, push, extra in self._sinks:
            if kind == "node":
                in_sync &= self._reconcile_node_sink(name, push, extra, entries)
            else:
                in_sync &= self._reconcile_fleet_sink(name, push, extra, entries)
        return bool(in_sync)

    def _reconcile_node_sink(self, name, push, accepts, entries) -> bool:
        in_sync = True
        for node_id, entry in entries.items():
            if accepts is not None and not accepts(node_id):
                continue
            desired = color_for(entry.status)
            key = (name, node_id)
            if self._pushed.get(key) == desired:
                continue
            try:
                push(node_id, desired)
            except Exception:
                # Left unrecorded on purpose: the next tick retries.
                logger.exception("status LED push failed (sink %s, node %r)", name, node_id)
                in_sync = False
                continue
            # Logged at INFO because it is genuinely rare -- diffing means
            # this fires once per real status change, not once per tick --
            # and because "was the ring ever actually told anything?" was
            # the exact question that took a while to answer when this was
            # broken.
            logger.info("%s: pushed %s %s/%s/%dms", name, node_id, desired.rgb,
                        desired.mode, desired.period_ms)
            self._pushed[key] = desired
        return in_sync

    def _reconcile_fleet_sink(self, name, push, render, entries) -> bool:
        value = render(entries.values())
        key = (name, None)
        if self._pushed.get(key) == value:
            return True
        try:
            push(value)
        except Exception:
            logger.exception("fleet readout push failed (sink %s)", name)
            return False
        self._pushed[key] = value
        return True
