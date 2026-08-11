# Bench Signal-Gen — Real Hardware Run

**Date:** 2026-08-08
**Branch:** feat/base-station-interop
**Tool:** `tools/bench_signal_gen/` (ADR-035)
**Hardware:** real Seeed XIAO satellite (node_id `5ab004`), Lenovo Legion 5 Pro
built-in speaker and phone (browser tone generator) as excitation sources.

## Summary

Neither channel achieved a confident tone-frequency detection this session:

- **Mic:** no detection at 200 Hz or 1 kHz across 3 physical placements, 2
  excitation sources, and system volume from 80-100%. 4 kHz was found to be
  architecturally unobservable on this firmware's wire spectrum (0-1992 Hz
  ceiling), a genuine tool/protocol-scoping gap discovered this session, not
  a test failure at that frequency. A reproducible 257.8 Hz artifact of
  unconfirmed origin (likely chassis/fan vibration or phone haptics) appeared
  repeatedly but never matched a played tone frequency.
- **Accelerometer:** no confident 60 Hz lock on any axis (contact coupling
  against a resting, unclamped chassis) — peak bin wandered at noise-floor
  levels across all three axes; formal `capture_and_compare.py` deltas are
  large in both directions, most extremely `accel_z`'s kurtosis_excess
  (+29.5 actual vs. -1.5 expected).

Both results are most consistent with equipment/coupling limitations named
in ADR-035 (unclamped resting contact for accel; unknown/unverified mic
gain-staging and unlocated speaker grille for the laptop-speaker mic
attempts) rather than a confirmed DSP-pipeline defect — the DSP/FFT pipeline
itself is demonstrably functional, since it cleanly resolved the real
257.8 Hz artifact to a single dominant bin when one was physically present.
No pass/fail tolerance band can be calibrated from this run's numbers (per
ADR-035's stated plan) since no channel produced a clean positive detection
to calibrate against.

---

## Mic channel — tone sweep

### Setup

- Laptop speaker path: system volume 80% initially, later raised to 100%;
  ambient room noise checked before testing (fan noise only, no other
  sources) via `ambient_check.py`.
- Satellite tried at three physical placements relative to the laptop: 15 cm
  from a side USB port (open air), direct contact at the keyboard deck, and
  resting on the underside/bottom-right of the chassis — the laptop's actual
  speaker grille location was never conclusively identified.
- Phone path (after laptop attempts were exhausted): browser-based tone
  generator (szynalski.com/tone-generator, no app install), held near and
  then flush against the satellite's mic opening.
- All captures used a full-timeline scan (every frame's peak-bin frequency/
  dBFS/RMS across the entire capture window, not just the last frame) to
  rule out capture-timing artifacts — see methodology note below.

### Methodology note: capture timing hazard

`capture_and_compare.py`'s `select_frame()` deliberately picks the temporally
**last** matching frame in a capture window (ADR-035: not averaging away
frame-to-frame variance). This creates a hazard for a bench operator
following the literal README procedure ("play tone and let it finish, then
capture") — if the capture window's tail extends past when playback actually
stopped, the "last frame" reflects silence, not the tone, giving a false
negative. Worked around this session by playing tones in the background
(longer duration) and running a short foreground capture window that closes
well before playback ends, cross-checked with a full-timeline scan
(`idx / peak_hz / peak_dbfs / rms` for every frame) rather than trusting a
single frame. This ruled out timing as the explanation for the results
below.

### Results

| Attempt | Source | Freq (Hz) | Placement | Result |
|---|---|---|---|---|
| 1 | Laptop speaker | 200 | 15 cm, open air | No detection — peak stayed at ambient (7.8 Hz) throughout |
| 2 | Laptop speaker | 1000 | Closer/keyboard deck | No detection — brief 257.8 Hz transient at capture start, then ambient baseline for rest of window |
| 3 | Laptop speaker | 1000 | Bottom-right chassis, direct contact | Same as #2 |
| 4 | Laptop speaker | 1000 | Same, at 100% system volume + max synth amplitude | Same as #2 |
| 5 | Phone (browser tone gen) | 1000 | Near mic | No detection at the 1000 Hz bin (-104 dBFS, flat noise floor); a real, sustained, growing signal appeared at 257.8 Hz instead |
| 6 | Phone (browser tone gen) | 1000 | Flush against mic | Same as #5 — 1000 Hz bin still -103 dBFS noise floor |

**No attempt at 200 Hz or 1000 Hz produced a detectable peak at the played
frequency**, across 3 physical placements, 2 excitation sources (laptop
speaker and phone), and system volume from 80% to 100%.

### The 257.8 Hz artifact

A recurring, reproducible signal at exactly 257.8 Hz (FFT bin 16, this
device's 15.625 Hz/bin resolution) appeared in attempts 2-6, but its
behavior is inconsistent with being the played tone:

- It appeared **only** once the satellite was moved into contact with the
  laptop chassis (absent in attempt 1's open-air 15 cm placement) — points
  to chassis/fan vibration, not acoustic content.
- In laptop-speaker attempts (2-4) it was a **transient**: elevated for the
  first several frames of a capture window, then decayed to ambient baseline
  for the remainder of the window despite the tone still playing.
- In the phone attempts (5-6) it was instead **sustained and growing**
  (-64.7 -> -55.3 dBFS over the capture window) — a different character,
  plausibly the phone's own haptic/vibration motor or speaker-housing
  resonance at contact, not confirmed.

Root cause is **not confirmed**. Flagged here as an open item, not asserted
as diagnosed.

### 4 kHz — architecturally out of range, not tested

The wire spectrum for the mic channel covers only **0-1992 Hz** (128 bins x
15.625 Hz/bin), despite the reported 16 kHz sample rate (which would allow
FFT bins up to 8 kHz Nyquist). **4 kHz cannot appear in this telemetry
channel's spectrum at all** — this is a firmware/wire-protocol bandwidth
limit discovered during this run, not a result of any test. The original
sweep plan (200 Hz / 1 kHz / 4 kHz, per `tools/bench_signal_gen/README.md`)
included a target this tool's current wire format cannot observe; the
README's sweep guidance should be corrected in a follow-up (see "Follow-up"
below).

While probing this with the phone at a confirmed 4000 Hz setting, a sharp,
narrow (non-broadband) peak appeared at ~1000-1008 Hz (bins 63-64, -51 to
-44 dBFS, all neighboring bins at noise floor) — qualitatively different
from the broad 257.8 Hz artifact above, and structurally consistent with a
real tone-like signal. This is suspicious rather than confirmatory: the same
bin showed pure noise floor (-103 dBFS) when the phone was actually set to
1000 Hz (attempts 5-6). A signal appearing near 1000 Hz specifically when
4000 Hz is played, but not when 1000 Hz is played, suggests aliasing or
harmonic/subharmonic distortion somewhere in the chain (phone speaker
nonlinearity at 4 kHz, or anti-aliasing filter leakage ahead of the mic's
decimation stage) rather than genuine 4 kHz detection. **Not confirmed as
either a real detection or a specific mechanism** — noted as an anomaly
worth follow-up, not resolved here.

### Conclusion — mic channel

No successful validation of mic-channel tone detection was achieved this
session at any tested frequency, source, placement, or volume. This is a
genuine negative result, not a tooling bug: the capture-timing methodology
was independently validated (full-timeline scans covering entire playback
windows), MQTT client-ID reuse was ruled out as a cause, and the DSP/FFT
pipeline is demonstrably functional (it clearly resolves a real signal at
257.8 Hz to a sharp single bin when one is physically present). The most
likely explanation is a real-world SPL/mic-sensitivity mismatch — the tested
excitation sources' acoustic output at the tested distances did not exceed
whatever threshold this satellite's mic gain staging needs — but this has
not been isolated from other candidates (mic gain/AGC configuration, an
unexpectedly aggressive DSP noise gate, or a hardware fault) within this
session.

### Follow-up (not done this session)

- Correct `tools/bench_signal_gen/README.md`'s sweep guidance: either drop
  4 kHz from the recommended sweep points or note the mic wire spectrum's
  0-1992 Hz ceiling explicitly.
- Investigate mic gain/AGC and DSP noise-gate configuration in
  `mic_task.c`/`dsp_task.c` as a candidate explanation for the total lack of
  tone detection, independent of further bench attempts.
- A calibrated reference speaker or an SPL meter would remove the "was it
  loud enough" ambiguity entirely — not available this session.

---

## Accelerometer channel — contact coupling test

### Setup

Satellite board rested in direct contact with the underside of the Legion 5
Pro chassis (bottom-right), the same placement used for the final mic
attempts. Tone: 60 Hz, amplitude 1.0, per `tools/bench_signal_gen/README.md`'s
recommended ~20-150 Hz contact-coupling range. Wire spectrum resolution for
accel is much coarser than mic: 12.5 Hz/bin (vs. mic's 15.625 Hz/bin), so the
60 Hz target's nearest bin center is 56.2 Hz.

An initial full-timeline capture (43 frames, all three axes) caught a large,
simultaneous multi-axis spike at 6.2 Hz around frames 26-30 (`accel_x` RMS
1.02g -> 1.17g, `accel_y` 0.10g -> 0.38g, `accel_z` 0.07g -> 0.52g) — traced
to the satellite being physically repositioned mid-capture (confirmed with
the operator), not a signal or hardware artifact. Discarded; a second,
undisturbed full-timeline capture was taken after repositioning settled.

### Full-timeline scan result (undisturbed capture)

Across all three axes, 43 frames, the peak bin **wanders** between adjacent
bins (18.8 / 31.2 / 43.8 / 56.2 / 68.8 / 81.2 / 106.2 / 118.8 / 181.2 Hz
variously) at essentially flat dBFS levels (-49 to -54 dBFS throughout, all
axes) — no bin holds a consistent, dominant lock the way a real driven
sinusoid would produce (contrast with the mic channel's genuine 257.8 Hz
artifact earlier, which locked a single bin ~50 dB above its neighbors for
dozens of consecutive frames). `accel_y` does read the correct 56.2 Hz target
bin in 2 of 8 blocks, but at -51.6 dBFS it is not distinguishable from the
surrounding noise-floor wander. This reads as noise-floor-level content, not
a resolved 60 Hz response, on all three axes.

`accel_x`'s baseline RMS sits at ~1.0-1.02g throughout — this is gravity (X
is the board's vertical axis in this resting orientation), a large DC
component that may be masking a smaller genuine AC response if the FFT path
doesn't remove DC the way `mic_task.c` does for the mic channel (per
`docs/performance/HARDWARE_AUDIT_RESULTS.md` Phase 4) — not confirmed, flagged
as a candidate explanation only.

### Formal actual-vs-expected (`capture_and_compare.py`, manifest
`tone_60hz_20260808_185329.json`, amplitude 1.0)

**accel_x** (2.5s window, 13 frames):

| metric | expected | actual | % delta |
|---|---|---|---|
| peak_bin_freq_hz | 60.000 | 43.750 | -27.083% |
| peak_bin_db | -- | -53.233 | -- |
| rms | 0.707107 | 1.014928 | +43.532% |
| crest_factor | 1.414214 | 1.263848 | -10.632% |
| kurtosis_excess | -1.500000 | -1.995463 | +33.031% |
| skewness | 0.000000 | -0.562604 | (abs delta -0.562604) |

**accel_y** (2.5s window, 13 frames):

| metric | expected | actual | % delta |
|---|---|---|---|
| peak_bin_freq_hz | 60.000 | 18.750 | -68.750% |
| peak_bin_db | -- | -50.718 | -- |
| rms | 0.707107 | 0.096664 | -86.330% |
| crest_factor | 1.414214 | 4.525992 | +220.036% |
| kurtosis_excess | -1.500000 | -0.697105 | -53.526% |
| skewness | 0.000000 | -1.616033 | (abs delta -1.616033) |

**accel_z** (3.0s window, 16 frames):

| metric | expected | actual | % delta |
|---|---|---|---|
| peak_bin_freq_hz | 60.000 | 31.250 | -47.917% |
| peak_bin_db | -- | -53.288 | -- |
| rms | 0.707107 | 0.039929 | -94.353% |
| crest_factor | 1.414214 | 11.763922 | +731.835% |
| kurtosis_excess | -1.500000 | 29.495106 | -2066.340% |
| skewness | 0.000000 | 1.751229 | (abs delta +1.751229) |

**Not averaged and not cherry-picked** — `accel_z`'s kurtosis_excess
(+29.5 actual vs. -1.5 expected, a -2066% delta) is a genuine outlier from
this run's raw numbers, reported as observed. A value that extreme on a
supposedly-sinusoidal signal is itself informative: it indicates the
captured `accel_z` waveform is dominated by sparse, high-amplitude
transients (consistent with intermittent mechanical contact/rattle from a
board merely resting against a chassis, rather than a clean sinusoidal
vibration coupling), not a modeling error in `ideal_sine_stats()`.

### Conclusion — accelerometer channel

No axis showed a confident, locked detection of the 60 Hz tone. All three
peak-bin frequencies missed the 60 Hz target substantially (43.75 / 18.75 /
31.25 Hz actual vs. 60 Hz expected), and scalar deltas are large and
inconsistent in direction across axes (RMS over-reads on `accel_x`,
under-reads by >85% on `accel_y`/`accel_z`; crest_factor and kurtosis swing
wildly, especially `accel_z`). This is consistent with ADR-035's acknowledged
limitation: a bare laptop chassis under a resting (not clamped/bolted) board
is a poor, uncontrolled contact-vibration coupling path — cone excursion at
60 Hz through an unclamped resting contact is not guaranteed to transmit a
clean sinusoid, unlike the mic channel's air-coupling which at least
delivers an unambiguous (if apparently too-quiet) acoustic waveform. Root
cause is most likely coupling quality (equipment limitation, matching
ADR-035's stated trade-off), not the DSP pipeline itself — but this was not
isolated from a DSP/gain-path explanation within this session, same caveat
as the mic-channel conclusion above.

### Follow-up (not done this session)

- A cheap vibration motor or bass-shaker transducer, bolted/clamped directly
  to the enclosure (not resting), would remove the coupling-quality ambiguity
  entirely — already named in ADR-035 as the preferred future upgrade, not
  available this session.
- Investigate whether accel FFT removes DC before transform (candidate
  explanation for `accel_x`'s gravity-dominated spectrum swamping any real
  AC content) — code-level check, not a bench-test action.

---

## Addendum (2026-08-08): re-test after the wire fft_size fix

`docs/decisions/ADR-020-bin-count-downsampled-not-buffer-enlarged.md`'s
same-day addendum documents a wire-protocol bug found via this document's
"4 kHz — architecturally out of range" section above: `net_task.c` reported
the *native* fft_size (1024 mic / 2048 accel) instead of the pooled,
effective fft_size, so any consumer computing bin width via `fs / fft_size`
— including this tool's `capture_and_compare.py` — recovered the wrong bin
width: 15.625 Hz/bin instead of the true 62.5 Hz/bin for mic, 12.5 Hz/bin
instead of the true 100 Hz/bin for accel. Every search window in the
results above was scaled wrong by exactly that factor. This addendum
re-runs the same tone sweep against the fixed firmware to see how much of
the original negative results that explains. **Not assumed either
way going in** — re-tested and reported below.

### Pre-test blocker: stale MQTT broker host in NVS

Before any tone could be captured, the satellite failed to connect to its
MQTT broker (`select() timeout` against `10.42.0.1`) — a value seeded into
NVS from an earlier hotspot-based session and never updated for this
session's `MUTHIYATTIRI 2.4GHz` / `192.168.1.5` (Mosquitto on the laptop)
setup. The *compiled* default (`EPM_MQTT_BROKER_HOST` in `link_mqtt.c` /
`wifi_task.c`) is also still `10.42.0.1`, so a bare NVS erase alone would
not have fixed it — confirmed by reading the source before touching
hardware. Worked around by a full `pio run -t erase` (the device's
`node_id` is derived from chip MAC, not NVS-stored, so identity survived)
and reflash with `EPM_MQTT_BROKER_HOST=192.168.1.5` passed via
`PLATFORMIO_BUILD_FLAGS` for this session only — not committed to
`platformio.ini`. A gitignored `.env.local`-style override for the broker
host, matching `tools/devrig/.env.local`'s existing pattern for the
reference-repo URL, is a planned follow-up so a bench-network change
doesn't require a source edit and reflash every time.

### Verification the fix landed on the wire

Decoded a live frame directly, before running any tone test:

| channel | fs (Hz) | fft_size | bin_count | bin width |
|---|---|---|---|---|
| mic | 16000 | 256 | 128 | 62.5 Hz |
| accel_x / accel_y / accel_z | 25600 | 256 | 128 | 100.0 Hz |
| accel_x/y/z_envelope | 3200 | 256 | 128 | 12.5 Hz (unchanged — never pooled) |

Matches the fix's target values exactly.

### Mic channel — re-test

Setup differed from the original run in one respect: the tone was played
continuously by the operator (phone tone generator held near the mic,
same placement family as the original run's phone attempts) rather than
a timed one-shot, so `generate_and_play.py tone --no-play` was used to
produce the ground-truth manifest only, without double-sourcing the tone
through the tool's own laptop-speaker playback.

**200 Hz:**

| metric | expected | actual | % delta |
|---|---|---|---|
| peak_bin_freq_hz | 200.000 | 218.750 | +9.375% |
| peak_bin_db | -- | -69.662 | -- |
| rms | 0.565685 | 0.000414 | -99.927% |
| crest_factor | 1.414214 | 2.239069 | +58.326% |
| kurtosis_excess | -1.500000 | -0.886397 | -40.907% |
| skewness | 0.000000 | -0.049843 | (abs delta -0.049843) |

**1000 Hz:**

| metric | expected | actual | % delta |
|---|---|---|---|
| peak_bin_freq_hz | 1000.000 | 1031.250 | +3.125% |
| peak_bin_db | -- | -57.549 | -- |
| rms | 0.565685 | 0.001700 | -99.700% |
| crest_factor | 1.414214 | 1.816666 | +28.458% |
| kurtosis_excess | -1.500000 | -1.125915 | -24.939% |
| skewness | 0.000000 | -0.010187 | (abs delta -0.010187) |

Both `peak_bin_freq_hz` values land in the bin that structurally contains
the played frequency — 200 Hz falls in bin 3's (187.5, 250] Hz range
(reported as its 218.75 Hz center); 1000 Hz sits on the bin 15/16 boundary,
effectively a single-bin match. Both are clean, confident detections,
against a genuinely quiet signal (-70 to -58 dBFS) that the old,
wrongly-scaled search window would have had to find by accident. This is a
reversal from the original run's 0/6 detections at these same two
frequencies. `rms`/`crest_factor`/`kurtosis_excess` deltas remain large —
expected, since `ideal_sine_stats()`'s closed-form manifold models a clean
sine at the played amplitude, not a real captured signal at unmeasured
distance/volume through a real mic's noise floor; only `peak_bin_freq_hz`
is a frequency-domain claim this fix bears on.

**Conclusion: the wire fft_size mislabeling, not equipment/coupling, was
the dominant cause of the original mic-channel non-detections.**
`capture_and_compare.py` was reading `fs`/`fft_size` straight off the wire
the whole time (`gateway/common/telemetry_frame.py` already decoded them
correctly, per its own pooled-aware convention) — it was the *satellite*
misreporting `fft_size`, so every comparison in the original run was
checking the wrong bin's frequency label against the right raw data. The
original run's "equipment/coupling limitation" conclusion for the mic
channel is superseded by this finding. Original numbers above are left
unedited, per this doc's own convention.

### Accelerometer channel — re-test

Setup changed from the original run in a way this addendum does **not**
control for: rather than resting the board loosely against the underside
of the laptop chassis, the laptop was placed directly on top of the
satellite board this session — a firmer but uncontrolled contact path,
different from the original run's placement. This is a confound flagged
explicitly, not folded silently into the conclusion below.

60 Hz tone, amplitude 1.0:

**accel_x:**

| metric | expected | actual | % delta |
|---|---|---|---|
| peak_bin_freq_hz | 60.000 | 450.000 | +650.000% |
| peak_bin_db | -- | -52.214 | -- |
| rms | 0.707107 | 1.015044 | +43.549% |
| crest_factor | 1.414214 | 1.285832 | -9.078% |
| kurtosis_excess | -1.500000 | -1.994434 | +32.962% |
| skewness | 0.000000 | -3.749820 | (abs delta -3.749820) |

**accel_y:**

| metric | expected | actual | % delta |
|---|---|---|---|
| peak_bin_freq_hz | 60.000 | 50.000 | -16.667% |
| peak_bin_db | -- | -52.638 | -- |
| rms | 0.707107 | 0.097083 | -86.270% |
| crest_factor | 1.414214 | 5.912220 | +318.057% |
| kurtosis_excess | -1.500000 | 0.305227 | -120.348% |
| skewness | 0.000000 | -0.437151 | (abs delta -0.437151) |

**accel_z:**

| metric | expected | actual | % delta |
|---|---|---|---|
| peak_bin_freq_hz | 60.000 | 250.000 | +316.667% |
| peak_bin_db | -- | -51.391 | -- |
| rms | 0.707107 | 0.044455 | -93.713% |
| crest_factor | 1.414214 | 8.935349 | +531.825% |
| kurtosis_excess | -1.500000 | 21.882227 | -1558.815% |
| skewness | 0.000000 | -0.007263 | (abs delta -0.007263) |

`accel_y` now lands in the *structurally correct* bin: at 100 Hz/bin
resolution, 60 Hz falls inside bin 0's (0, 100] Hz range, reported as that
bin's 50 Hz center — a real, if coarse, detection. This matches the
original run's full-timeline scan finding that `accel_y` touched the
correct bin in 2 of 8 blocks even under the old mislabeling; here it is
reproduced in a formal single-frame capture. `accel_x` and `accel_z` still
miss. `accel_x` keeps the same gravity-DC-dominated baseline
(rms ~1.0-1.02g both sessions) already flagged as a candidate explanation
in the original run's conclusion, unrelated to this fix. `accel_z`'s miss
(250 Hz instead of 60 Hz) is new and not explained by the wire bug alone —
plausibly broadband mechanical noise from a full laptop (fan, chassis
resonance) now resting directly on the board, a difference introduced by
this session's firmer, uncontrolled coupling method, not isolated further
here.

**Conclusion: partial support for the same finding as the mic channel, but
weaker and confounded.** One axis (`accel_y`) flipped from
wandering-noise-floor to a genuine correct-bin detection under the fix;
`accel_x` and `accel_z` still show no lock, for reasons plausibly unrelated
to the wire bug (gravity DC on X; likely new broadband noise on Z from
this session's different, firmer coupling). Because the physical coupling
method changed between the original run and this one, this result cannot
cleanly separate "the wire fix helped" from "the new coupling method is
different." A follow-up holding the *exact* original resting-contact
placement constant, changing only the firmware, would isolate the two.

### Updated overall conclusion

The wire fft_size bug explains the *entirety* of the original mic-channel
non-detections, and at least *part* of the original accelerometer
non-detections (`accel_y`). The original run's "equipment/coupling
limitation" explanation, while still plausible for `accel_x`/`accel_z`'s
remaining misses, was not the right explanation for the mic channel or for
`accel_y` — those were the wire protocol reporting bin frequencies scaled
wrong by a factor the DSP pipeline itself never got wrong.

### Follow-up (not done this session)

- Parameterize `EPM_MQTT_BROKER_HOST` via a gitignored `.env.local`-style
  override (matching `tools/devrig/.env.local`'s pattern), so a bench
  network change doesn't require a source edit and reflash.
- Re-test the accelerometer channel holding the *exact* original
  resting-against-chassis placement constant, to separate the wire fix's
  effect from this session's laptop-on-board coupling change.
- Investigate `accel_z`'s new 250 Hz miss under the firmer coupling method
  used this session (candidate: broadband fan/chassis noise from direct
  laptop contact) — not investigated further here.
- Try acoustic (near-field speaker, no physical contact) excitation for the
  accelerometer channel rather than resting/direct-contact coupling —
  reported informally this session as having worked for a teammate on a
  separate setup. Untested here; airborne excitation driving a board's
  resonance is a plausible, cleaner alternative to both of this doc's
  contact-coupling methods, worth a dedicated re-test rather than folding
  into this addendum's numbers.

---

## Addendum (2026-08-09): accel full-spectrum characterization + combined mic+accel stimulus test

**Hardware:** same satellite (node_id `5ab004`), laptop body resting
directly on top of the board (chassis-coupled, same placement as this
addendum's 2026-08-08 accel re-test above). **Goal:** same rigor as this
document's mic `MIC_FS_HZ` work — audit the accel driver for the same class
of bug, then empirically characterize the bench rig's real usable range and
the accel path's capture fidelity, rather than trusting datasheet/design
assumptions.

### 1. Driver audit — accel-side equivalent of the mic bug, found and fixed

`components/epm_drivers/accel_kx134_spi.c` independently mirrors
`src/epm_config.h`'s `IMU_FS_HZ` the same way `mic_inmp441_i2s.c` mirrors
`MIC_FS_HZ` (architectural rule: `epm_drivers` never includes `src/
epm_config.h`, to avoid a component dependency cycle). Unlike the mic
driver, the accel mirror had drifted: the chip's programmed ODR was
`OSA=0x0E` (12800Hz), while `IMU_FS_HZ=25600` was assumed everywhere
downstream — `epm_dsp_envelope_init()`'s band-pass filter design,
`net_task.c`'s wire `.fs` fields, and the gateway's bearing-marker math. This
was already named in `docs/decisions/ADR-017` ("ODR mismatch, out of scope
to fix here") but understated: it also invalidated the envelope filter (its
8kHz upper band edge sat *above* the real 6400Hz Nyquist) and put every
wire-reported accel frequency off by 2×, not just the `vTaskDelay`/comment
staleness ADR-017 originally flagged.

No `FFT_IMU_N`-vs-raw-block-size landmine (the `MIC_RAW_BLOCK_SAMPLES`-class
bug) was found: `KX134_STAGE_MAX_SAMPLES` is decoupled from `FFT_IMU_N` but
bound-checked with a loud `-EINVAL` in `kx134_fill_epoch()`, not a silent
truncation — this part of the driver was already clean.

Per direction, the fix raised the KX134's programmed ODR to 25600Hz to match
`IMU_FS_HZ` (not the cheaper option of lowering `IMU_FS_HZ` to match the old
12800Hz reality) — `IMU_FS_HZ=25600` was chosen specifically to clear the
reference project's reported 8kHz accel ceiling with margin, and lowering it
would have locked in falling short of that goal instead of meeting it.
Full writeup, options considered, and hardware validation (965 epochs, 2895
`hal_accel_read_block()` calls, 0 `read_errors`, 0 `reinit_attempts`, stable
readings and cadence over a 150s capture) in `docs/decisions/ADR-037-kx134-
odr-raised-to-25600hz.md`. No other file needed to change — every downstream
consumer was already written against `IMU_FS_HZ=25600`.

### 2. Pre-test blocker: MQTT broker unreachable (self-inflicted, not a repo bug)

Before any sweep could run, the satellite could not connect to MQTT at all
(`esp-tls: select() timeout` / `Failed to open a new connection: 32774`
repeating every ~13s — the exact signature `docs/performance/
SATELLITE_STRESS_STABILITY_TEST.md` §3 already root-caused as the vendored
esp-mqtt/esp-tls library's reconnect state machine getting stuck against an
unreachable broker). Root cause here was different from that document's:
every reflash performed during today's ODR validation work (§1) called
`platformio.exe` directly by its full path rather than through this repo's
`pio.ps1` wrapper — the wrapper is what reads `.env.local`
(`EPM_MQTT_BROKER_HOST=192.168.1.5`) and injects it as a build flag; bypassing
it silently falls back to the compiled default `10.42.0.1`, an address that
doesn't exist on this network. Confirmed via `link_mqtt.c`: the broker host
is a pure compile-time macro, never NVS-provisioned, so there was no
runtime workaround short of a correct reflash.

Fixed by reflashing through `pio.ps1` (`$env:PATH` needed the platformio
`Scripts` directory prepended first, since raw `pio` also isn't on PATH by
default on this machine). Verified fixed: `mqtt: connects=1 disconnects=0
publishes=125 publish_failures=0` immediately after. This is the second
session in a row this exact `.env.local`-bypass footgun has appeared (the
2026-08-08 addendum above hit the same broker-host mismatch before the
wrapper existed) — worth a harder guard (e.g. `pio.ps1` failing loudly, or a
build-time assertion, if `EPM_MQTT_BROKER_HOST` is ever the compiled
default in a non-CI build) as a follow-up, not done here.

### 3. Rig frequency-response characterization (15-250Hz sweep)

`capture_and_compare.py sweep`, `accel_z` channel, amplitude 0.9, 3 repeats/
point, run at the corrected 25600Hz ODR. Full log:
`docs/performance/raw/accel_rig_sweep_20260809.log`; CSV: `docs/performance/
raw/accel_rig_sweep_results_20260809.csv`.

| Freq (Hz) | Locks | Reliable | Notes |
|---|---|---|---|
| 15 | 3/3 | yes | |
| 20 | 3/3 | yes | |
| 30 | 3/3 | yes | |
| 40 | 3/3 | yes | |
| 50 | 3/3 | yes | |
| 60 | 3/3 | yes | |
| 70 | 3/3 | yes | |
| 80 | 3/3 | yes | |
| 90 | 3/3 | yes | last fully clean point |
| **100** | **1/3** | **no** | sharp drop, no transition zone |
| 110 | 1/3 | no | |
| 120 | 1/3 | no | |
| 130 | 1/3 | no | |
| 140 | 0/3 | no | worst point in the sweep |
| 150 | 2/3 | yes | non-monotonic — better than 100-140Hz |
| 175 | 1/3 | no | |
| 200 | 1/3 | no | |
| 250 | 1/3 | no | |

**Below 100Hz, the accel wire bin width (100Hz, per ADR-020) exceeds the
requested frequency**, so `is_locked()` uses its documented low-frequency
branch (checks for elevated energy in the lowest 1-2 bins, not exact bin
location — see `capture_and_compare.py`'s own docstring). The 15-90Hz
"locks" therefore certify *real, distinguishable low-frequency vibration
energy reaching the sensor with ≥6dB SNR* (SNR ranged 6.1-13.1dB across
all 27 attempts in this range, 27/27 locked), not sub-bin frequency
accuracy — that finer claim isn't meaningful at this wire resolution
regardless of rig quality.

At 100Hz and above, `is_locked()` switches to a strict peak-bin-vs-target
comparison. The transition is a **step, not a gradual rolloff**: 90Hz is
27/27... 3/3 clean, 100Hz drops to 1/3 immediately, with no intermediate
partial-degradation frequency found. Just as informative as the lock/fail
count: on every *failed* attempt from 100-250Hz, the peak bin lands far
from the requested frequency and scattered across a wide, inconsistent
range (350-2050Hz, no repeated pattern) — not a low-amplitude version of
the true tone decaying gracefully into the noise floor. That is the
signature of the coupling becoming nonlinear/resonance-dominated above
~90-100Hz (broadband or harmonic content overwhelming the fundamental),
matching this document's earlier informal finding and the framing this
characterization set out to confirm — **this is a property of the
laptop-speaker-through-chassis stimulus rig, not a claim about the KX134
sensor's own frequency response.** The sensor's real ODR/Nyquist is now
25600Hz/12800Hz (§1); nothing in this section tests or challenges that —
only this particular bench rig's ability to deliver a clean single-frequency
stimulus above ~90Hz. The occasional stray lock in the 100-250Hz range
(e.g. 150Hz's 2/3, several single lucky locks elsewhere) is consistent with
the ±100Hz tolerance window being wide relative to the 100Hz bin spacing,
not a second clean band re-emerging.

**Conclusion: rig's clean, reliable range is ~15-90Hz**, matching and
sharpening the ~100Hz informal estimate this characterization was scoped to
verify.

### 4. Accel capture fidelity at 25600Hz ODR — zero drops across the sweep

Diagnostics snapshot taken immediately after the full 18-point/54-attempt
sweep above (`docs/performance/raw/accel_sweep_post_diag_20260809.log`):

```text
mqtt: connects=1 disconnects=0 publishes=2420 publish_failures=0
imu: epochs=2671 read_errors=0 reinit_attempts=0 reinit_successes=0
accel: reads_ok=8013 read_errors=0 fifo_max_hits=2671
net: frames_built=2421 build_failures=0 publish_failures=0
mic i2s: overflow_count=26 (cumulative, unchanged from ADR-037's baseline)
```

Zero accel read errors or reinits across 2671 epochs (the entire sweep plus
idle time either side), zero MQTT disconnects, zero frame-build/publish
failures. The 25600Hz ODR sustains a real, sweep-length driven-vibration
session on real hardware without degradation — this is the step-3
confirmation the ODR bump from §1/ADR-037 needed beyond the original 150s
at-rest validation run.

### 5. Combined mic+accel simultaneous stimulus test

One 80Hz tone (chosen from inside the clean-locking accel range found in
§3), played once through the laptop speaker — the same physical stimulus
reaches both the onboard mic (air/structure-borne) and the chassis-coupled
accel (mechanical) at the same time, exercising `mic_task` and `imu_task`
concurrently under one real, shared-CPU/DMA/task-priority load. Both
channels decoded from the *same* captured frame set (not two separate
runs). Full output: `docs/performance/raw/combined_stimulus_20260809.log`.

| Channel  | Locked | SNR (dB) | Peak bin (Hz) |
| -------- | ------ | -------- | ------------- |
| mic      | True   | 23.63    | 93.8          |
| accel_z  | True   | 10.46    | 150.0         |

39/39 captured frames carried both channels — no frame dropped either
channel's data during the concurrent-load window. Note `accel_z`'s peak
landed in the (100,200] bin rather than the (0,100] bin 80Hz numerically
falls in; still within the ±100Hz tolerance window (same coarse-tolerance
effect noted in §3), reported as-is rather than smoothed over.

Diagnostics immediately after
(`docs/performance/raw/combined_stimulus_post_diag_20260809.log`), compared
against the pre-combined-test baseline in §4:

```text
mqtt: connects=1 disconnects=0 publishes=2994 publish_failures=0
mic: blocks_ok=29667 capture_failures=0 rb_drops=0
mic i2s: overflow_count=26 (still unchanged — zero new overflows)
imu: epochs=3298 read_errors=0 reinit_attempts=0
accel: reads_ok=9894 read_errors=0
net: frames_built=2995 build_failures=0 publish_failures=0
```

No counter moved in a way attributable to the combined load: mic overflow
count is identical to the pre-existing baseline, zero new read errors or
reinits on either path, zero MQTT disconnects. **Confirms the shared CPU/
DMA/task-priority budget handles real concurrent mic+accel stimulus without
either path dropping samples or degrading.**

### Overall conclusion (2026-08-09 addendum)

- Accel-side audit found and fixed a real bug (KX134 ODR half of what
  `IMU_FS_HZ` assumed) — worse than ADR-017 had characterized it, now
  resolved and hardware-validated at the corrected 25600Hz rate (ADR-037).
- The bench rig's clean, reliable stimulus range is ~15-90Hz; above that the
  laptop-speaker-through-chassis coupling degrades sharply (not gradually)
  into nonlinear/resonance-dominated behavior. This is a rig limitation,
  explicitly not evidence about the KX134's own (far higher, now
  25600Hz-ODR) capability.
- Within the rig's clean range and at the new ODR, the accel capture path
  showed zero dropped samples/read errors/reinits across a full real sweep.
- Combined mic+accel simultaneous stimulus produced zero cross-path
  degradation — both channels locked concurrently from the same frames,
  with diagnostics counters unchanged from their pre-combined-test
  baseline.

### Follow-up (2026-08-09 addendum, not done this session)

- Harden `pio.ps1`'s broker-host injection against silent bypass (§2) — a
  raw `platformio.exe`/`pio` invocation should fail loudly or warn, not
  silently fall back to a nonexistent compiled default.
- A clamped/bolted vibration transducer (already flagged in the 2026-08-08
  section above) would likely push the rig's clean range past 90Hz and
  narrow whether 100-140Hz's total lock failure is coupling-amplitude or
  genuine chassis-resonance limited — not distinguished in this session.
