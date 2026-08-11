---
id: ADR-018
title: Kurtosis convention reversed to excess/Fisher — supersedes ADR-014
status: accepted
date: 2026-08-04
deciders: Abhinav Krishna N
supersedes: ADR-014
---

## Context

ADR-014 chose RAW/Pearson kurtosis (Gaussian ≈ 3.0) to match our own
firmware's existing behavior (`mic_task.c`, `net_task.c`'s synthetic placeholder,
the passing host test), while explicitly flagging that the reference repo's
excess/Fisher convention was based on an unverified draft — "this decision
should be re-confirmed against
`base-station/python/common/raw_features.py`... once that repo is available again."

That file was later fetched live (`raw_features.py`, from the reference base station's repository, main branch):

```python
def kurtosis(x: np.ndarray) -> float:
    std = x.std()
    if std <= 0:
        return 0.0
    return float(np.mean(((x - x.mean()) / std) ** 4) - 3.0)  # excess kurtosis
```

Explicit, commented, unambiguous: the reference subtracts 3.0. This module's own
docstring states it shares the exact math `fuser.cpp` (the actual on-device MCU
scalar computation) uses — this is not a display-only or offline-analysis-only
convention, it's the wire convention his firmware actually produces.

## Decision

Reverse ADR-014. The wire/firmware kurtosis convention is **excess/Fisher**
(Gaussian ≈ 0.0), matching the reference repo's actual convention, not
ADR-014's RAW/Pearson choice.

This ADR does not itself change code (this was a planning-only pass, no
firmware/gateway edits). Required follow-up, for whichever future work next
touches scalar computation:

- `src/threads/mic_task.c`: subtract `3.0f` from the current raw kurtosis
  computation.
- `tests/host/test_scalar_stats.c`: flip `gaussian_kurtosis_raw_convention`'s
  expectation from ≈3.0 to ≈0.0 (and rename to drop "raw_convention" from its name).
- `src/threads/net_task.c`'s synthetic-frame placeholder (currently hardcodes
  `3.0f` as a "plausible healthy kurtosis" stand-in) needs its fallback constant
  updated to `0.0f` to stay self-consistent once the real computation changes —
  a synthetic frame using the old convention while the real path uses the new one
  would silently reintroduce the exact mismatch this ADR closes.

## Consequences

- Not superseded/deleted — ADR-014 stays in the tree, marked
  superseded by this ADR, as a record of the intermediate (incorrect) decision
  and why it was made in good faith at the time (no reference source available).
- Downstream consumers of kurtosis values (bearing-fault scoring, anomaly
  thresholds, any trained model) will see a step change once the code fix lands —
  same category of consequence ADR-012 (Hann coherent-gain fix) already
  documented for its own metric, expected and not a regression.
- No urgency to land the code fix immediately — no real kurtosis is on the wire
  yet (satellite still publishes synthetic frames; real DSP output starts
  flowing once the fuser/encoder path is wired up). Recommend folding this fix
  into that same future work rather than a standalone emergency fix.
