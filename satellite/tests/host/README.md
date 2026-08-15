# tests/host/ — native firmware regression tests

Host-buildable regression tests for the firmware's DSP scalar-stats
(`src/mic_task.c`) and window-normalisation math (`src/dsp_task.c`). Pure
native gcc/CMake — **no ESP-IDF toolchain, no target hardware, no
PlatformIO** involved in this build.

This is Phase 1 of this project's build plan: it establishes a regression
baseline *before* Phase 2 fixes anything. One test here is **expected to
fail** against current source — that failure documents a real bug for
Phase 2's task list. Do not "fix" `src/dsp_task.c`/`src/mic_task.c` to make
it pass; that belongs to a later phase.

## Prerequisites

- Any C11 compiler (confirmed with gcc 16.1.0 / MinGW-w64 on Windows) and
  CMake ≥3.16 (bundles CTest).
- **`test_hann_window` only**: `managed_components/` must be populated
  first. It's `.gitignore`'d — fetched by the ESP-IDF/PlatformIO component
  manager, not committed as source. Run `pio pkg install` (or any
  `pio run`) once from the repo root; no flashing or hardware required.
  If it's missing, `cmake` fails with an explicit message pointing back
  here rather than a cryptic missing-file error.

## Build & run

```
# Windows (explicit generator avoids an ambient MSVC generator being picked)
cmake -S tests/host -B tests/host/build -G "MinGW Makefiles"
cmake --build tests/host/build
ctest --test-dir tests/host/build --output-on-failure

# Linux / macOS
cmake -S tests/host -B tests/host/build
cmake --build tests/host/build
ctest --test-dir tests/host/build --output-on-failure
```

For full per-check detail, run the binaries directly from the repo root
(`test_frame_encode` writes to the relative path `tests/host/out/frame.bin`,
so it needs repo root as its working directory):

```
tests/host/build/test_scalar_stats
tests/host/build/test_hann_window
tests/host/build/test_frame_encode
```

## What "pass" means

Each binary prints one line per check — `[PASS]`, `[FAIL]`,
`[EXPECTED-FAIL]`, or `[UNEXPECTED-PASS]` — followed by a `RESULT:` summary.
Exit code (and `ctest`'s Passed/Failed) is 0 only when every check matched
its documented expectation:

- `[PASS]` / `[FAIL]` — a check expected to hold today. `[FAIL]` is a
  genuine regression.
- `[EXPECTED-FAIL]` — a check on a **known, currently-unfixed bug**. This
  is the expected, correct state for Phase 1 — it does not fail the build.
- `[UNEXPECTED-PASS]` — an `EXPECT_FAIL` check that started passing,
  meaning the underlying bug looks fixed upstream and this test's
  expectation is now stale and needs updating. This *does* fail the build,
  so it gets noticed.

Read the printed detail lines, not just the ctest one-liner — that's where
the actual vs. expected numbers and the known-bug explanation live.

## Test inventory

| Target | Check | Expectation | Notes |
|---|---|---|---|
| `test_scalar_stats` | `sine_crest_factor` | PASS | crest ≈ √2 |
| `test_scalar_stats` | `square_crest_factor` | PASS | crest ≈ 1.0 |
| `test_scalar_stats` | `gaussian_kurtosis_raw_convention` | PASS | kurtosis ≈ 3.0, **raw/Pearson** convention (current firmware behavior) — flags an open discrepancy against the project's internal wire-protocol doc, which documents excess/Fisher convention (≈0.0) instead. Not resolved here; Phase 2/4's job. |
| `test_scalar_stats` | `silence_fallback_defaults` | PASS | exercises the crest/kurtosis guard-branch fallbacks on an all-zero block |
| `test_hann_window` | `hann_coherent_gain_matches_theory` | PASS | real vendored `dsps_wind_hann_f32()`, coherent gain ≈ 0.5 |
| `test_hann_window` | `nf_normalization_ignores_coherent_gain` | **EXPECTED-FAIL** | documents that `src/dsp_task.c:202`'s power-normalisation constant (`nf = 2/N`) has no `/coherent_gain` term, so Hann-windowed spectra read ~6 dB low. This is the anchor bug Phase 2's "window normalisation should derive coherent gain from the actual window array" task exists to fix. |
| `test_frame_encode` | frame length / section count | PASS | pre-existing wire-framing test (`components/epm_codec/`), unrelated to DSP; just wired into this same CMake build for convenience |

## Expected runtime

All three binaries combined run in well under 1 second (pure in-memory
float arithmetic on ≤20k-element arrays, one small file write).

## Maintenance note

`test_scalar_stats.c`'s `mirror_rms`/`mirror_crest`/`mirror_kurtosis`
functions are **hand-transcribed** from `src/mic_task.c` (exact line
numbers cited in comments), because that file is ESP-IDF-coupled and can't
be `#include`d or linked directly here. If `mic_task.c`'s formulas change,
these mirrors must be manually re-synced — this is a known cost of the
approach. `test_hann_window.c` has no such drift risk: it links the real,
unmodified vendored `dsps_wind_hann_f32.c` rather than reimplementing it.

## Adding a new test

Add an `add_executable(...)` / `add_test(...)` pair to `CMakeLists.txt`.
