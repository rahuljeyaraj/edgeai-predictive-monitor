# GPU/NPU acceleration feasibility — Edge Impulse fault classifier

Status: **Revised 2026-07-25 (later same day).** The original spike below
(§1-§4) ruled out GPU acceleration **via Google's prebuilt `ai-edge-litert`
binary specifically** — that verdict stands, that exact binary is dead on
this board. But a follow-up round of digging (§5-§6) found that verdict had
been over-generalized: the crash was a defect in one vendor's prebuilt
binary (an ARMv8.1 CPU-feature assumption), not proof the Adreno GPU itself
or its driver stack can't do compute. **The Adreno 702 GPU's OpenCL 3.0 /
Vulkan 1.4 compute stack was independently verified working, live, on this
exact board** (§6), and a real ncnn model was run on it with bit-exact
correctness (§7 update) — GPU compute here is real, not just theoretical.
NPU was also chased down further and is now a **confirmed dead end**, not
just untested (§5) — Qualcomm's own product brief for this chip settles it.
**Final verdict (§7): GPU acceleration works correctly but gives ~1.0x
speedup at this project's model sizes — even batched across many nodes at
once (§7's later update) — not worth the engineering cost for latency.**
**Decision: staying on CPU (XNNPACK).** `pipeline/classifier.py` correctly
stays CPU-only as a result; this is closed, not a gap to revisit later.

Related: `docs/DEV_PERF_PAGE_PLAN.md` §5b (the GPU busy% tile that first
flagged this as "worth a quick spike before betting the demo story on it"),
`docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md` §4 (the classifier this was
spiked for).

---

## 0. Why this was worth checking

The autoencoder (`pipeline/autoencoder.py`) is locked to CPU-only PyTorch —
that was a deliberate M6 decision (`docs/MPU_Software_Architecture.md`) made
because it needs a real on-device *training* story, which TFLite/GPU
delegates don't offer. The EI fault classifier is different: it's
inference-only (EI trains and builds the model cloud-side), so it was free to
pick a different runtime — and `docs/DEV_PERF_PAGE_PLAN.md` §5b had already
flagged that nothing in this app currently drives the GPU at all
(`monitoring/gpu_perf.py` only *reads* a busy% counter; nothing makes it
move). A TFLite classifier with a working GPU delegate would have been the
first real GPU workload in this codebase, and a genuine demo lever.

## 1. What's actually confirmed to work: container → GPU device access

Before touching any TFLite/LiteRT code, the standing question from
`DEV_PERF_PAGE_PLAN.md` §5b was whether the app's Docker container (managed
by `arduino-app-cli`) can even reach the GPU device at all — SPI hit exactly
this wall before it (`provision-spi.sh`'s whole reason to exist): the device
node was bind-mounted and file-permission-correct, but the container's
compiled-in device-cgroup allowlist still `EPERM`'d every open() at the
kernel level, and only a root-owned host-side bridge daemon worked around it.

Checked directly on the real board (`d3583952e4e1`,
`edgeai-predictive-monitor-base-station-main-1`):

```
$ adb shell ls -la /dev/dri
crw-rw----+ 1 root video  226,   0 card0
crw-rw----+ 1 root render 226, 128 renderD128
```

Both nodes are visible **and open() successfully with O_RDWR from inside the
container**:

```python
>>> os.open('/dev/dri/card0', os.O_RDWR)      # OK, fd=3
>>> os.open('/dev/dri/renderD128', os.O_RDWR) # OK, fd=3
```

The container's own `arduino` user (uid 1000) is a member of both `video`
(gid 44) and the render group (gid 991, matching `renderD128`'s owning
group) — and, unlike SPI's major 153, **DRM's major (226) is already in the
container's compiled-in cgroup allowlist**
(`[c 226:* rmw, c 250:* rmw, c 504:* rmw, c 81:* rmw, c 116:* rmw]`, per
`docs/progress2.md`'s device-cgroup notes). No bridge daemon, no
provisioning step, nothing needed here — **container-level GPU access was
never the blocker.** This part of the spike came back fully positive.

## 2. What actually failed: the GPU accelerator binary itself

### 2.1 Environment

- Board: Arduino UNO Q, Qualcomm QRB2210 MPU.
- CPU: Kryo 260 (`CPU part : 0x801` in `/proc/cpuinfo`).
- `/proc/cpuinfo` `Features` line: `fp asimd evtstrm aes pmull sha1 sha2 crc32
  cpuid` — **no `atomics`**. That flag is how Linux reports ARMv8.1's Large
  System Extensions (LSE); its absence means this CPU is ARMv8.0-A baseline
  for atomic instructions, whatever marketing tier "Kryo 260" implies
  otherwise.
- Container Python: `/app/.cache/.venv/bin/python3`, **3.13.14, aarch64**
  (`uv`-managed venv, no `pip` binary — installs go through `python3 -m pip`
  or `uv pip`).
- **No internet access from inside the container, or from the board's host
  Linux at all** (`urllib.request` DNS resolution fails both places). Every
  wheel below had to be downloaded on a dev machine and sideloaded via
  `adb push` + `docker cp` + `pip install --no-index --target <scratch dir>`
  — a throwaway location, not the app's real venv, so this spike left no
  trace on the app's actual dependency set.

### 2.2 What got installed

`ai-edge-litert` (the actively-maintained rebrand of `tflite-runtime`,
already named as the fallback option in
`docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md` §4.1) has exactly two
published versions on PyPI, **2.1.5 and 2.1.6**, both with a matching
`cp313-cp313-manylinux_2_27_aarch64` wheel — a direct hit for this board's
exact Python/arch. Installed (scratch target, `--no-deps`) along with the
handful of pure-Python deps missing from the venv (`flatbuffers`,
`backports.strenum`, `ml_dtypes` — `numpy`/`tqdm`/`typing_extensions`/
`protobuf` were already present, pulled in by `torch`/other requirements).

The package ships **two separate inference APIs**:

- The classic `tf.lite`-compatible `Interpreter` (`ai_edge_litert.interpreter`)
  — this is what `pipeline/classifier.py` actually uses (CPU/XNNPACK only).
  It has a `Delegate`/`load_delegate()` path too (ctypes-loaded external
  delegate `.so`), but **this wheel bundles no
  `libtensorflowlite_gpu_delegate.so`** — the classic GPU delegate library
  simply isn't shipped in this version.
- A newer `CompiledModel`/`Environment`/`HardwareAccelerator` API
  (`ai_edge_litert.compiled_model`, `.hardware_accelerator`), Google's
  current-generation interface: `CompiledModel.from_file(path,
  hardware_accel=HardwareAccelerator.GPU | HardwareAccelerator.CPU)`. Its own
  docstring: *"GPU: Use GPU for inference with WebGPU/OpenCL/Metal backend."*
  This is the one that actually has a GPU path to test — backed by
  `libLiteRtGpuAccelerator.so` / `libLiteRtWebGpuAccelerator.so`, both
  bundled in the wheel.

### 2.3 The test

CPU baseline first, to confirm the runtime itself works on this board at
all — using a tiny (544-byte) known-good `.tflite` fixture (`add.bin` from
TensorFlow's own test suite: one `ADD` op, float32, `[1,8,8,3]` input):

```
INFO: Created TensorFlow Lite XNNPACK delegate for CPU.
CPU OK
[{'name': 'input', 'index': 1, 'shape': array([1, 8, 8, 3]), 'dtype': float32, ...}]
```

Clean. Then the GPU path, both `GPU | CPU` (fallback-enabled) and `GPU` alone:

```
INFO: [environment.cc:30] Creating LiteRT environment with options
WARNING: [npu_registry.cc:34] NPU accelerator could not be loaded and registered: kLiteRtStatusErrorInvalidArgument.
INFO: [gpu_registry.cc:101] Attempting to load GPU accelerator(.../libLiteRtGpuAccelerator.so).
INFO: [gpu_registry.cc:101] Attempting to load GPU accelerator(.../libLiteRtWebGpuAccelerator.so).
FATAL ERROR: This binary was compiled with lse enabled, but this feature is not available on this processor (go/sigill-fail-fast).
```

Process exit code **132** (128 + `SIGILL`). Reproduced identically on **both**
2.1.5 and 2.1.6 — not a fluke of one release.

### 2.4 Why this specific failure matters for the code, not just the hardware

This is not a "delegate unavailable, fall back gracefully" situation. It's an
illegal-instruction crash **inside the accelerator library's own
initialization code**, before any Python-catchable exception is raised — the
whole process dies. A naive "try the GPU accelerator, except-and-fall-back-
to-CPU" implementation (the obvious first instinct, and what an earlier draft
of `pipeline/classifier.py` actually did before this spike) would have taken
down the entire base station process the instant a device_type with a
fetched model saw its first gated frame. `pipeline/classifier.py` deliberately
never attempts the GPU path at all, rather than wrapping it in a try/except —
see its module docstring.

One more detail worth keeping: the `WARNING: [npu_registry.cc:34] NPU
accelerator could not be loaded` line above happened on **every** run,
GPU-path or not — LiteRT always probes for an NPU vendor dispatch library at
`Environment` construction time, and *this* failure mode is a soft warning,
not a crash, when none is installed (which nothing was, in this spike). That
asymmetry — NPU absence degrades gracefully, GPU absence (on this specific
prebuilt binary) does not — is part of why §4 below is worth pursuing over
retrying the GPU path with a different LiteRT version.

## 3. Why this isn't a "just pick a different version/package" fix

- **Only two `ai-edge-litert` releases exist on PyPI at all** (2.1.5, 2.1.6)
  — both tested, both built the same way. There's no older/alternate
  aarch64 build to fall back to from this package.
- **`tflite-runtime`** (the predecessor package, before Google's LiteRT
  rebrand) is effectively abandoned — last releases predate Python 3.12/3.13
  entirely, so there's no compatible wheel for this board's Python 3.13.14
  to even try.
- **Building Google's GPU accelerator from source, targeting an ARMv8.0
  baseline (no LSE codegen)**, is the only way to actually fix *this specific
  binary*. That's a real option in principle (Apache 2.0, source available)
  but a much bigger lift than a "quick spike": the full Bazel-based
  LiteRT/TensorFlow build toolchain, a cross-compilation target this project
  has no existing setup for, likely hours-to-days of build-system work — and
  even after a successful build, whether this Adreno GPU's actual OpenCL/
  WebGPU driver stack on this board's OS image works at all is a **second,
  still-completely-unverified question** this spike never got to test (the
  crash happened before any real GPU/driver interaction). Building from
  source removes the LSE blocker; it doesn't guarantee anything downstream
  of it.

Given that, GPU acceleration via TFLite/LiteRT is being parked, not
actively pursued further.

## 4. Proposed next step: Qualcomm's own NPU dispatch SDK

`ai-edge-litert`'s wheel metadata declares an optional extra:

```
Provides-Extra: npu-sdk
Requires-Dist: ai-edge-litert-sdk-qualcomm~=0.2.0; extra == "npu-sdk"
Requires-Dist: ai-edge-litert-sdk-mediatek~=0.2.0; extra == "npu-sdk"
```

`ai-edge-litert-sdk-qualcomm` is a **separate, Qualcomm-published** package —
their own compiled NPU dispatch library for LiteRT's `HardwareAccelerator.NPU`
path, distinct from Google's own GPU/WebGPU binary that just failed. This
matters for two reasons:

1. **Different vendor, different build target.** Qualcomm ships this
   specifically for their own SoC family, so it's far more likely to be
   compiled against the real ARM baseline their own low/mid-tier chips (like
   QRB2210) actually run — the exact class of mismatch that broke Google's
   generic GPU binary here.
2. **Different silicon.** This targets QRB2210's Hexagon DSP/NPU path, *not*
   the Adreno GPU `monitoring/gpu_perf.py`'s busy% tile already reads. If
   this works, it lights up a different accelerator than the one currently
   wired into the Dev/perf page — worth knowing going in, since "GPU" and
   "NPU" aren't interchangeable for that dashboard tile's specific number,
   even though both are real hardware-acceleration wins for inference
   latency/CPU offload.

### What a follow-up spike would need

- Install the `npu-sdk` extra (`pip install 'ai-edge-litert[npu-sdk]'`) —
  same sideload-via-adb dance as this spike, given the board has no direct
  internet access.
- Confirm QRB2210's Hexagon variant actually exposes an NPU/HVX-capable DSP
  vendor dispatch target Qualcomm's SDK supports — **not yet confirmed
  either way**. QRB2210 is a lower/mid-tier robotics-focused Snapdragon
  derivative; its Hexagon DSP is documented in this codebase so far only in
  an audio-processing context, never as an AI accelerator target. This is
  the single biggest open unknown for this path, bigger than the software
  packaging question above.
- Same device-access question as §1, but for whatever `/dev` node(s) the
  Qualcomm NPU dispatch library needs (likely a different device than
  `/dev/dri/*` — probably something under `/dev/dsp*`, `/dev/adsprpc*`, or
  similar Hexagon RPC device nodes, unconfirmed) — needs its own cgroup-
  allowlist check the same way §1 checked DRM's major, since there's no
  guarantee that device is in the same allowlist that happened to already
  cover GPU.
- Rerun this doc's §2.3-style smoke test (`HardwareAccelerator.NPU | CPU`)
  against the same `add.tflite` fixture, then against a real fetched EI
  model once one exists.

Not started — flagging as the concrete next step if hardware-accelerated
inference is still wanted, rather than leaving "GPU didn't work" as a dead
end.

**Superseded by §5-§6 below** — §5 answers the "does this chip's Hexagon
variant even support an NPU dispatch target" question this section left
open, definitively: no.

---

## 5. NPU, revisited: confirmed dead end (not just untested)

Chased down the §4 "biggest open unknown" (whether QRB2210's Hexagon variant
exposes an NPU-capable compute target at all) two ways, both agreeing:

**On-device evidence.** `adb shell ls /dev | grep fastrpc` shows only
`fastrpc-adsp`. Higher-tier Snapdragons that support Qualcomm's QNN/SNPE NPU
delegate expose a *separate* `fastrpc-cdsp` node (the Compute DSP domain
QNN's `HardwareAccelerator.NPU` path dispatches to) — this board has no such
node. `lsmod`/`dmesg` confirm the one Hexagon core that does exist is loaded
with `qcom/qcm2290/adsp.mbn` and surfaces only as audio modules
(`q6asm`/`q6afe`/`q6adm`/`q6core`) — i.e. it's wired up for the LPASS audio
pipeline, not general compute offload.

**Official confirmation.** Qualcomm's own QRB2210 product brief (Rev. D,
fetched live) lists the Hexagon block under **"Audio DSP (LPASS)"**, scoped
to *"low-power, always-on processing, audio signal processing, lightweight
AI inference tasks"* — not a tensor accelerator. The same brief states
outright: *"It's optimized for edge AI with integrated TensorFlow Lite,
enabling efficient, on-device inference via **CPU and GPU**"* — Qualcomm's
own sanctioned acceleration story for this exact board names CPU and GPU
only. There is no NPU silicon here to target — `ai-edge-litert-sdk-qualcomm`
(§4) would have nothing to dispatch to. Not pursuing further.

## 6. GPU, revisited: the driver stack works — the crash was one binary's defect

The board's identity, confirmed on-device: `qcom,qrb2210 qcom,qcm2290`,
`soc_id` 524, board name `imola`. Same Kryo CPU/no-LSE finding as §2.1 stands.

**What's now verified, live, that §3 flagged as "still-completely-unverified":**
whether this Adreno GPU's actual driver stack works at all, independent of
Google's prebuilt binary. On the board's **host** OS (Debian trixie):

```
$ clinfo
  Platform Name    rusticl
  Device Name      FD702
  Device Vendor    Qualcomm
  Device Version   OpenCL 3.0
$ vulkaninfo --summary
Vulkan Instance Version: 1.4.309
```

This is Mesa's open-source stack — **Rusticl** (OpenCL) and **Turnip**
(Vulkan), both running against the same `msm`/`kgsl` kernel driver already
loaded (confirmed via `lsmod`), talking to `/dev/dri/renderD128`. Real,
working, hardware-verified GPU compute — a completely different code path
from Google's `ai-edge-litert` GPU delegate that crashed in §2, and not
subject to that binary's LSE assumption (Mesa's own build doesn't require
it on this CPU).

**The gap: this stack is on the host, not in the app's container.** The
container (`ghcr.io/arduino/app-bricks/python-apps-base:0.11.0`) already has
`/dev/dri/*` reachable (same as §1 found for the original spike — container's
`arduino` user is in `video`/render groups, DRM major already
cgroup-allowlisted) but is missing the Mesa/LLVM userspace libraries
(`mesa-opencl-icd`, `mesa-vulkan-drivers`, and LLVM/clang libs Rusticl links
against — no `/etc/OpenCL/vendors/`, no `/usr/share/vulkan/icd.d/`). Sideloaded
these into the running container (`docker cp`, host→container, both same
Debian trixie so binary-compatible) as a live spike: got every shared-library
dependency resolved (`ldd` clean), but `clGetPlatformIDs` still returned
`CL_PLATFORM_NOT_FOUND_KHR` (-1001) — the ICD loader itself wasn't picking up
the registered driver, root cause not yet chased down (candidates: ICD-loader
implementation mismatch, a Mesa version/ABI expectation not met by a
piecemeal file copy vs. a real `apt install`). **This is a packaging problem,
not a hardware capability gap** — getting a *properly installed* Mesa build
into the container (real package install, not hand-copied files) is very
likely to resolve it, but that's not yet proven.

**Process note, in the interest of full disclosure:** mid-spike, a `docker cp`
of `libffi.so.8` (one of Rusticl's transitive deps) briefly left
`libffi.so.8.1.4` as a broken self-referential symlink inside the live
container. Caught via an HTTP health check on the dashboard (200 throughout)
and the app's real venv (`torch` import kept working) — no impact on the
running app, fixed by re-copying the real file from the host, and all
spike-added files were removed afterward to leave the container clean.

## 7. Proposed path forward (not yet built)

Given §5 and §6: NPU is closed off, but GPU is real and reachable — just not
through Google's prebuilt LiteRT. Proposed direction:

- Export both the EI classifier and the autoencoder to **ncnn or MNN**
  (lightweight C++ inference libs with mature Vulkan compute backends, built
  for exactly this class of ARM+Adreno hardware — unlike `ai-edge-litert`
  they don't bake in an LSE assumption).
- Run them as a **host-side bridge process**, mirroring the existing
  `gpu_bridge.py` pattern (`host/gpu_bridge.py`): a small daemon on the host,
  where the real Mesa/Vulkan stack already works today, exposed to the
  container over a Unix socket under `/dev` (already bind-mounted — no new
  compose/mount plumbing needed, same reasoning `gpu_bridge.py`'s docstring
  gives).
- This sidesteps both the container-packaging gap in §6 and the
  rebuild-TensorFlow-from-source dead end in §3 entirely.
- Autoencoder-specific note: only `reconstruction_error()` (the per-frame
  inference call) is a candidate for this — training must stay CPU-side
  (PyTorch, no mainstream framework trains via Adreno/Hexagon on this stack),
  which is consistent with the original M6 decision.

**Update 2026-07-25 (same day, later): tested.** Ran ncnn 1.0.20250503
(official PyPI aarch64 wheel, `cp313`) directly on the board's **host** OS
(not yet the container — see §6's packaging gap). Result:

```
[0 Turnip Adreno (TM) 702]  ...
[1 llvmpipe (LLVM 19.1.7, 128 bits)]  ...
Vulkan GPU count: 2, GPU 0 = the real Adreno, GPU 1 = software fallback
```

ncnn's own Vulkan device enumeration names the hardware directly —
`Turnip Adreno (TM) 702` — as a distinct device from the llvmpipe software
fallback, confirming it's really dispatching to the GPU, not silently
falling back. Built an `InnerProduct` (dense) layer directly via ncnn's
low-level Python API (`ParamDict` / `create_layer` / `ModelBinFromMatArray`
— no `.param`/`.bin` conversion tooling needed) at several sizes and ran it
both ways:

| shape (in→out) | CPU avg | GPU avg | speedup | output match |
|---|---|---|---|---|
| 512→64   | 27.8 us  | 27.1 us  | 1.03x | exact (0.000000 max diff) |
| 512→256  | 42.4 us  | 42.1 us  | 1.01x | exact |
| 1024→512 | 161.6 us | 158.6 us | 1.02x | exact |
| 2048→1024| 536.0 us | 527.9 us | 1.02x | exact |

**Verdict: GPU acceleration is real and correct, but not a meaningful win at
these sizes.** The Vulkan path executes correctly on the actual Adreno
hardware (bit-exact output vs. CPU, confirming no silent fallback or
precision loss) — so the earlier "GPU is fundamentally broken here" framing
from §2 is now conclusively narrowed to "Google's specific prebuilt binary
was broken here," not the hardware. But CPU (XNNPACK/NEON) is already so
fast for dense layers at the classifier's and autoencoder's actual scale
(tens to hundreds of microseconds) that GPU dispatch overhead cancels out
essentially all of the GPU's raw throughput advantage — speedup stays flat
at ~1.02-1.03x even scaled up to 2048→1024, well past this project's actual
model sizes (512-1024 input, single/few dense layers).

**Net recommendation:** hardware acceleration for the classifier/autoencoder
is technically *achievable* (§7's ncnn/MNN + host-bridge architecture would
work) but **not worth building** for latency — there's no real latency
problem to solve at this model size. The only remaining reason to pursue it
would be the demo-story motivation from §0 (making the dev/perf page's GPU
tile move), not a performance need. Parking §7 as "proven feasible, not
worth the engineering cost" rather than pursuing further, unless the
demo-lever motivation on its own is judged worth the work.

**Update 2026-07-25 (same day, later still): what about batching multiple
nodes through the one shared classifier?** Unlike the autoencoder (one model
instance per motor), the EI fault classifier is a **single shared model**
that every satellite/node's feature vector runs through. Reasonable
question: does feeding many nodes' vectors through in one batched call (where
a GPU's parallelism has more work per dispatch to amortize its fixed
overhead against) flip the earlier single-vector verdict?

Tested directly: a 2-layer dense net (512→64→4, the EI classifier's actual
shape) fed an `(N, 512)` batch in one call, `N` swept 1→256 nodes (this
project realistically runs single-digit-to-dozens of nodes, so 256 is a wide
margin past any real deployment):

| N nodes | CPU us | GPU us | speedup | batched correctly | output matches |
|---|---|---|---|---|---|
| 1   | 72.0   | 77.8   | 0.93x | yes | yes |
| 2   | 72.4   | 68.0   | 1.06x | yes | yes |
| 4   | 70.3   | 68.4   | 1.03x | yes | yes |
| 8   | 91.4   | 93.0   | 0.98x | yes | yes |
| 16  | 140.7  | 137.3  | 1.02x | yes | yes |
| 32  | 233.9  | 227.4  | 1.03x | yes | yes |
| 64  | 417.3  | 425.7  | 0.98x | yes | yes |
| 128 | 810.4  | 800.9  | 1.01x | yes | yes |
| 256 | 1616.4 | 1638.6 | 0.99x | yes | yes |

("batched correctly" confirms the output blob's row count actually equals
`N` — i.e. ncnn genuinely ran N independent rows through the shared weights
in one call, not silently flattening the whole batch into a single vector.
"output matches" confirms GPU and CPU agree per-element.)

**Answer: no, batching does not flip it.** The CPU/GPU ratio stays flat at
~1.0x across the entire 1→256 range — both CPU (NEON) and GPU (Vulkan)
scale near-linearly with batch size on this workload, so neither picks up a
relative edge as N grows. The intuition behind the question — "GPUs pull
ahead once there's more work per dispatch" — is correct in general, but the
crossover point where that kicks in is hardware- and workload-dependent, and
this board's Adreno 702 (a low/entry-tier mobile GPU, 845 MHz) never reaches
it for a dense net this small, even multiplied across every node this
project would realistically ever run. Confirms the §7 recommendation stands
even under the batched-multi-node framing: not worth building.
