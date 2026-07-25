# base_station multi-second stall — `spi_arm_stream` investigation

Status: **Diagnosed further, still NOT fixed.** Two dashboard-freeze causes
(registry per-frame disk I/O, MQTT shared-thread blocking) were found and
fixed on 2026-07-25, commit `dc4b3e8` — both confirmed live, deployed,
0 stalls after. A third, separate stall remains: `base_station` itself
(the locally SPI-connected node, not a satellite) still freezes for
**2-3 seconds, roughly every 20-60s**, live-confirmed via WebSocket
message-gap measurement. Root cause has been narrowed from "a single RPC
reply is slow" down to "the MCU's `spi_link_thread` itself stops making any
progress for the full stall duration" — see §3. **The actual fix is still
firmware-side and not yet built** — see the TODO section immediately below.

---

## TODO (next session should start here)

1. **Add WCET-style avg/max timing counters** to `matrix_display.cpp`'s
   redraw tick, `rgb_display.cpp`'s render tick (specifically bracket the
   `irq_lock()`/`irq_unlock()` window inside it, not just the whole tick),
   and `accel_sampler.cpp`'s sample loop — same pattern already proven out
   in this codebase for `fuser.cpp` (`fuser_send_ms_max`, exposed as
   `fus_avg`/`fus_max` via `get_bench_stats`, `bench.cpp`). Expose the new
   numbers the same way (extend `get_bench_stats` or add fields to
   `get_spi_link_stats`).
2. This is a **firmware change** — needs a real `arduino-app-cli app start`
   build+flash cycle, not the fast python-only push+restart used for every
   other step of this investigation so far. Budget real time for it (see
   deploy gotchas below) and don't try to shortcut it.
3. Re-run the live repro (§4) and directly correlate: does a
   redraw/`irq_lock` spike on any of those three threads line up in time
   with a `spi_link_thread` freeze caught via `get_spi_link_stats` polling?
   Is it long enough, or does it repeat/chain enough times back-to-back, to
   account for the full observed 2-3 second stall?
4. **If confirmed:** the fix is firmware-side — either shrink/eliminate the
   `irq_lock()` window (e.g. move WS2812 output to DMA the way the rest of
   `rgb_display.cpp`'s main loop already claims to, so the bit-bang critical
   section stops needing a global interrupt lock at all), or reconsider
   `matrix_display`/`rgb_display`/`accel_sampler`'s priority (currently 3,
   above `spi_link`'s 6 and Bridge's 5) the same way `fuser`/`mic` were
   reprioritized in the 2026-07-20 session below — but demoting them changes
   *their own* real-time guarantees (LED refresh smoothness, accel sample
   timing), so this needs a deliberate choice, not a reflexive copy-paste of
   the earlier fix.
5. **If NOT confirmed** (timing doesn't line up): the next candidate is
   Bridge's own MCU-side thread (priority 5, also above `spi_link`'s 6,
   already named as a suspect in the 2026-07-20 session and never checked)
   doing unknown periodic work of its own. Same instrumentation approach
   would apply if it's exposed via whatever library owns that thread
   (`Arduino_RouterBridge`), otherwise this becomes a harder question about
   a vendored dependency's internals.

Do **not** re-propose `registry.py` or `mqtt_subscriber.py` changes for
this — both already fixed (§2) and confirmed unrelated to what's left.
Do **not** lead with "LED ring/matrix status push contention" as the cause
either — that specific theory (a *Python-side* `Bridge.call()` from
`wire_local_status_led`/`wire_local_matrix_text` colliding with the SPI
consumer thread) was checked live twice this session and never overlapped
a single captured stall. The remaining suspects (§5 above) are MCU-firmware
threads that run continuously regardless of whether Python ever sends them
a new command — a different mechanism than the one first suspected.

---

## 1. Why this matters

`ingestion/spi_reader.py`'s `_pull_frame()` holds `BRIDGE_LOCK` and blocks
the entire frame-ingestion pipeline for `base_station` while waiting on the
MCU's reply. During a stall, `base_station`'s charts and anomaly score
genuinely stop updating dashboard-side — confirmed via WebSocket message
inter-arrival gap measurement, not inferred from code reading. Satellite
nodes (ingested over MQTT) are unaffected — this is specific to the one
node wired directly over the on-board SPI/Bridge link.

## 2. What's already fixed (2026-07-25, commit `dc4b3e8`)

Two unrelated causes of dashboard freezes were found and fixed the same day
this investigation started, both confirmed live and deployed:

- **`registry.py` per-frame disk I/O.** `touch_last_seen()` used to
  persist `registry.json` (mkstemp + `json.dump` + `os.replace`) on *every*
  ingested frame, several times a second per node, while holding a lock
  REST mutations (pause/rename/commission/...) also block on. A storage
  hiccup on this device turned that into a total dashboard freeze:
  ingestion stalls inside the lock, REST's worker threadpool piles up
  waiting on it, WebSocket broadcasts (fired only after `route()` returns)
  stop, and the node eventually reads as offline once `last_seen` goes
  stale. Fixed by not persisting on that path at all — `last_seen` is a
  pure liveness heartbeat, and a stale/missing value after a restart reads
  identically to a persisted-but-ancient one (both show "offline" until the
  next real frame).
- **`mqtt_subscriber.py` shared-thread blocking.** paho-mqtt's `on_message`
  callback runs on *one* thread shared by every node on the broker. The
  callback used to call `_handle_message()` (full autoencoder inference +
  a history DB write) directly, so one slow satellite node's inference
  time delayed every other MQTT node's telemetry too. Fixed by splitting
  the callback into a fast enqueue (paho's actual callback, just parse +
  push to a bounded queue) and a dedicated worker thread that does the real
  work off paho's critical path — the same enqueue/process split
  `spi_reader.py` already used for the local SPI path. Verified live:
  before, stalls up to 1.9-3.2s; after, 45s of live measurement showed zero
  stalls over 600ms, max gap 335ms.

Neither of these explains what's left — see §3.

## 3. What's left: `base_station`'s own SPI/Bridge stall

### 3.1 First pass (same 2026-07-25 session): too coarse a diagnosis

Caught via two thread-dump samples 0.3s apart, both showing the SPI
consumer thread on the identical line:

```
File "ingestion/spi_reader.py", line 182, in _pull_frame
    reply = str(Bridge.call("spi_arm_stream", str(CHUNK_SIZE)))
File ".../arduino/app_utils/bridge.py", line 385, in call
    <blocked in a queue.get()>
```

This looked like "a single RPC call's reply just doesn't arrive for
multiple seconds." A follow-up session (below) showed that's not quite
right — the sampling interval (0.3s) was too coarse to see what was
actually happening underneath.

### 3.2 Second pass: the real mechanism

Instrumented every `Bridge.call()` site directly (duration + reply value
logged around each call, not just periodic thread-dump sampling) and
re-captured live. The actual picture:

`_pull_frame()`'s inner retry loop (`ARM_RETRIES = 30`, `time.sleep(0.005)`
between attempts) around the `spi_arm_stream` call **exhausts all 30
attempts**, getting an instant `'busy'` reply every single time — each
individual round-trip is fast (~10-13ms), never itself slow. One full
30-attempt exhaustion costs ~0.36-0.4s. The dashboard-visible multi-second
stall is **six or more of these full exhaustions back-to-back** — e.g. one
capture showed 6 consecutive `TOTAL attempts=30 ... final_reply='busy'`
events, 0.35-0.4s apart, spanning ~2.2s, matching a ~3s WebSocket message
gap measured at the same time. This is not one call hanging — every
individual RPC round-trip completes quickly; the MCU just keeps saying
"busy" for 2+ seconds straight.

Polling `get_spi_link_stats()` (`sketch/spi_link.cpp`'s
`checkpoint,staged,armed,completed,timeouts,errors,...` counter string)
from inside the already-stalled retry loop, multiple times ~0.3-0.4s apart
across one live ~2.3s cascade, showed **`checkpoint` (frozen at 210),
`armed`, `completed`, `timeouts`, and `errors` bit-for-bit identical across
every single reading for the entire stall** — while `staged` (the fuser
thread's own counter) kept climbing normally the whole time. Fuser is
fine, still producing frames on schedule; `spi_link_thread` itself is
frozen. Checkpoint 210 is the point in `spi_link_thread_entry`'s
auto-advance loop right after successfully arming the next chunk, about to
re-enter `spi_link_wait_transfer()` and wait for its completion.

**This rules out `spi_link_wait_transfer()`'s own documented timeout bound
as the explanation.** That function is bounded at
`SPI_LINK_DMA_WAIT_TICKS(1000) * SPI_LINK_DMA_WAIT_TICK_MS(1)` ≈ 1 second
max before it must register a timeout (incrementing `timeout_count`) and
return. Since `timeout_count` never moved across a 2.3s+ window, the
*same* wait call for the *same* chunk was still in flight the entire time
— which means either `spi_link_thread` (priority 6) wasn't being scheduled
at all for 2+ seconds, or something prevented its own timeout bookkeeping
from advancing (e.g. a stretch with interrupts globally disabled — Zephyr
timeouts don't advance while IRQs are locked, since the RTOS tick that
`k_sem_take(..., K_MSEC(1))`'s timeout depends on can't fire either).

### 3.3 LED ring/matrix Bridge contention — checked live, ruled out (for now)

The original suspect (from the 2026-07-20 session that built
`spi_arm_stream`, see §5) was contention with `main.py`'s
`wire_local_status_led`/`wire_local_matrix_text`, which also call
`Bridge.call()` (`set_rgb`/`set_matrix_scroll_speed`/`set_matrix_text`) —
both serialize through the same process-local `BRIDGE_LOCK` the SPI
consumer thread uses. Instrumented both push sites with start/end/duration
logging and re-captured live twice, across 20+ stalls total. **Zero such
log lines fired in either window** — no node status transition happened
during either capture, so this Python-side path was never even in play
those times. This doesn't fully clear it (a transition-heavy capture might
show something different), but it's no longer the leading theory — see §4.

### 3.4 Current leading suspect: a priority-3 MCU thread starving `spi_link`

`matrix_display.cpp`, `rgb_display.cpp`, and `accel_sampler.cpp` all run at
**priority 3** (`app_config.h`) — strictly above `spi_link_thread`'s
priority 6 and even above Bridge's own thread (priority 5). In Zephyr,
lower priority number preempts higher, so any stretch where one of these
three is actively running can freeze `spi_link_thread`'s progress for as
long as that stretch lasts, regardless of whether Python ever asked for a
new LED color or matrix message — these threads do continuous work
(animation ticks, WS2812 refresh, accelerometer sampling) on their own
schedule, independent of RPC traffic. `mic_sampler.cpp`'s own comments
(around its priority-8 justification) already reference **"a rare Bridge
RPC or `rgb_display.cpp` `irq_lock()` window overlapping a block [costing]
a dropped [mic] block"** as a known, already-tolerated occasional cost —
confirming `rgb_display.cpp` really does have a global-interrupt-disable
critical section somewhere (most likely the WS2812 bit-bang transmit
itself), separate from the "no `irq_lock`, no busy-wait" comment
describing that file's main render loop overall.

This is the exact suspect the 2026-07-20 session named but never checked
("matrix/rgb/accel (priority 3, ABOVE Bridge, untouched by today's
reprioritization)") — this investigation is the first time there's been
live counter-freeze data actually implicating that tier, rather than just
naming it as a theoretical possibility. **It is not yet a confirmed timing
match** — none of the three threads currently expose per-tick timing
stats, so there's no live number yet for how long a real redraw or
`irq_lock` window takes on this hardware, or how often it happens. See the
TODO section above for the instrumentation needed to close this out.

## 4. Repro method (live, on real hardware, no simulator)

All of this was diagnosed directly against the running device over `adb`
— nothing here reproduces in a simulator/mock. Rough recipe, refined
across two sessions:

1. Patch **local, uncommitted** copies of `main.py`/`spi_reader.py` to add
   `faulthandler.register(signal.SIGUSR1, file=open('/tmp/thread_dump.log',
   'a'), all_threads=True)` near the top of `main()`, plus duration/reply
   logging around every `Bridge.call()` site of interest.
2. Push directly to the container's bind-mounted app directory
   (`/home/arduino/ArduinoApps/edgeai-predictive-monitor-base-station`,
   confirmed via `docker inspect --format '{{range .Mounts}}...'`) and
   `docker restart` — much faster than a full `deploy.sh`/
   `start_dashboard.sh` cycle for a pure-Python change (seconds, not
   minutes), and doesn't touch firmware.
3. In one `docker exec -d`, loop `date +%s.%N >> /tmp/signal_times.log;
   kill -USR1 1; sleep 0.3` for periodic timestamped thread dumps.
4. In another `docker exec -d`, run a small `websockets` client against
   `ws://localhost:8080/ws`, logging inter-arrival gaps per
   `(type, node_id)` key, flagging anything over ~600ms.
5. Once a stall is caught, correlate `signal_times.log` entries against
   the WebSocket gap's timestamp window, and read the matching thread-dump
   chunks — or, more precisely, read the duration/reply-value log lines
   added in step 1, which catch every RPC round-trip's real outcome
   instead of whatever line happened to be executing at a sampling
   instant.
6. Query `get_spi_link_stats()` (gated to only fire once already inside a
   slow retry loop, not on every attempt, to avoid adding load that
   contaminates the measurement) to see the MCU-side counters directly.
7. **Always revert the temporary instrumentation and restart clean once
   done** — confirmed via `git status` that no tracked file was left
   modified.

Gotchas hit along the way, worth keeping for next time:
- If you `rm` the thread-dump log path while the process still has it
  open, `cat`/`wc -l` on that path then fails ("No such file") even though
  data is still being appended — recover via `ls -la /proc/1/fd/` to find
  the fd for the now-"(deleted)" path, then `cp /proc/1/fd/<N>
  /tmp/recovered.log`.
- Don't call `get_spi_link_stats()` (or any `Bridge.call()`) from a
  *separate* process/connection while diagnosing — `BRIDGE_LOCK` is
  process-local only, so a second caller could introduce real UART
  contention and contaminate the exact thing being measured. Do it from
  inside the already-running, already-Bridge-holding thread instead.
- One unexplained hiccup: starting the thread-dump loop and the WebSocket
  probe together, immediately after a restart, once caused the container
  to segfault (exit 139) within ~15s. Bringing the two up one at a time
  (dump loop alone stable for 20s, then adding the probe) never
  reproduced it again. Not root-caused — flag if it recurs, don't assume
  it's unrelated to future instrumentation without checking.

## 5. Background: the RPC this bug lives in

`spi_arm_stream` (built 2026-07-20, same day as an SPI clock-speed raise)
replaced the older one-RPC-per-chunk `spi_arm(offset, len)` protocol: a
single call now arms the MCU for the *whole* frame, and
`sketch/spi_link.cpp`'s transport thread auto-advances chunk-by-chunk on
its own, cutting RPC round-trips per frame from ~21-29 down to 1. That
session also built real interrupt-driven DMA completion detection and
reprioritized `fuser`/`mic_sampler` threads after finding fuser's own
per-epoch cost had tripled past a stale code comment, starving
same-priority `spi_link` under Zephyr's non-preemptive-among-equals
scheduling — a very similar class of bug to what's suspected here, just
one priority tier removed and one order of magnitude smaller (that
session's residual gap was multi-*millisecond*, not multi-*second*). That
session's own net result was only a 6% fps improvement (2.17 → 2.30fps),
and it explicitly named two suspects for the remaining gap it never
chased down: **Bridge's own thread (priority 5, doing unknown periodic
work)** and **matrix/rgb/accel (priority 3, above Bridge, untouched by
that session's reprioritization)**. This investigation's §3.4 finding is
the first real evidence pointing at the second of those two.
