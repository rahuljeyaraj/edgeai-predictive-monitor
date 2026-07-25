"""Fault classifier -- loads the Edge Impulse TFLite model fetched by
api/ei_controller.py's fetch_model() and scores a feature vector, per
docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md S4 (T3). Mirrors
pipeline/autoencoder.py's load-once/score-per-call shape, but backed by a
TFLite interpreter instead of PyTorch -- this is inference-only (EI trains
and builds the model cloud-side), so none of autoencoder.py's "needs a real
on-device training story" reasoning for picking PyTorch applies here.

CPU (XNNPACK) only, deliberately -- final, not a stopgap. GPU and NPU were
both spiked live against the real board (2026-07-25, starting from
docs/DEV_PERF_PAGE_PLAN.md S5b's "worth a quick spike before betting the
demo story on it") and both ruled out, not left unverified. Full writeup,
exact commands/output: docs/GPU_NPU_ACCELERATION_FEASIBILITY.md. Short
version:

- `/dev/dri/card0` and `/dev/dri/renderD128` (this board's Adreno GPU) ARE
  visible AND actually open() successfully from inside the app's Docker
  container -- unlike SPI's precedent (provision-spi.sh), the container's
  device-cgroup allowlist does NOT block the DRM major (226) here. Container
  access was never the blocker.
- What IS a hard blocker: `ai-edge-litert`'s official PyPI wheels (both
  published versions, 2.1.5 and 2.1.6, aarch64) ship their GPU accelerator
  (`libLiteRtGpuAccelerator.so`/`libLiteRtWebGpuAccelerator.so`, a
  WebGPU/OpenCL backend -- this version has no classic
  `libtensorflowlite_gpu_delegate.so`/`load_delegate()` path at all) compiled
  requiring ARMv8.1+ LSE atomic instructions. This board's CPU (Qualcomm
  Kryo 260 -- confirmed via `/proc/cpuinfo`'s Features line, no `atomics`
  flag) doesn't have LSE, so loading either library is a `SIGILL` that kills
  the *entire process* immediately (confirmed live: exit code 132, "This
  binary was compiled with lse enabled, but this feature is not available on
  this processor"). This is NOT a catchable Python exception -- there is no
  safe try/fall-back-to-CPU around it, unlike a normal delegate-unavailable
  error. That's why this module doesn't attempt it at all, rather than
  wrapping the attempt in a try/except the way an ordinary "optional
  accelerator" would be handled.
- NPU: confirmed dead end, not just untried. This board (QRB2210/QCM2290)
  only exposes `/dev/fastrpc-adsp` -- no `/dev/fastrpc-cdsp`, the compute
  domain Qualcomm's QNN/SNPE NPU delegate (the `ai-edge-litert[npu-sdk]`
  extra) would dispatch to. Qualcomm's own product brief confirms the
  Hexagon core here is the LPASS *audio* DSP, not a tensor accelerator, and
  states this board's sanctioned AI path is "CPU and GPU" -- there's no NPU
  silicon to target.
- GPU (the real Adreno 702 hardware, separate from `ai-edge-litert`'s dead
  binary above): re-verified working via `ncnn`'s Vulkan backend --
  bit-exact output vs. CPU, confirmed running on the actual hardware (not a
  software fallback). But speedup stayed flat at ~1.0x from a single vector
  up through a 256-node batch (this classifier is one shared model across
  every node, unlike the per-motor autoencoder, so batching all nodes'
  vectors through one call was tested specifically) -- this board's Adreno
  702 never has enough work per dispatch to beat CPU (NEON) on a net this
  small. Real, correct, and not worth building.

interpreter_factory is dependency-injected (default: _default_interpreter,
which imports ai-edge-litert) so tests never need the real TFLite runtime
installed -- same "duck-typed fake, no mock library" convention
api/ei_controller.py uses for ei_client (client=ei_client, defaulted but
swappable).
"""
import json
import logging
import os
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _default_interpreter(model_path: str):
    from ai_edge_litert.interpreter import Interpreter

    interpreter = Interpreter(model_path=model_path)
    interpreter.allocate_tensors()
    return interpreter, "cpu"


def _quantize(detail: dict, vector: Sequence[float]) -> np.ndarray:
    x = np.array([vector], dtype=np.float32)
    dtype = detail["dtype"]
    if dtype != np.float32:
        # EI's on-device build can be int8-quantized (build_model()'s
        # modelType defaults to whatever the Keras block was configured
        # with) -- apply the interpreter's own scale/zero_point rather than
        # assuming float32 in/out, or a quantized model silently produces
        # garbage confidences instead of an error.
        scale, zero_point = detail["quantization"]
        x = (x / scale + zero_point).round().astype(dtype)
    return x


def _dequantize(detail: dict, values: np.ndarray) -> np.ndarray:
    dtype = detail["dtype"]
    if dtype != np.float32:
        scale, zero_point = detail["quantization"]
        return (values.astype(np.float32) - zero_point) * scale
    return values


class FaultClassifier:
    def __init__(self, model_path: str, labels: List[str],
                 interpreter_factory: Callable[[str], Tuple[object, str]] = _default_interpreter):
        if not labels:
            raise ValueError("FaultClassifier requires at least one label")
        self._labels = labels
        self._interpreter, self.backend = interpreter_factory(model_path)
        logger.info("FaultClassifier loaded %s on %s backend", model_path, self.backend)
        self._input_detail = self._interpreter.get_input_details()[0]
        self._output_detail = self._interpreter.get_output_details()[0]

    def classify(self, vector: Sequence[float]) -> Dict[str, float]:
        """feature vector -> {label: probability}. Raises ValueError if the
        model's output width doesn't match the label list this instance was
        built with -- a stale label list for this model, not a scenario to
        silently truncate/pad around."""
        x = _quantize(self._input_detail, vector)
        self._interpreter.set_tensor(self._input_detail["index"], x)
        self._interpreter.invoke()
        raw = self._interpreter.get_tensor(self._output_detail["index"])[0]
        output = _dequantize(self._output_detail, raw)
        if len(output) != len(self._labels):
            raise ValueError(
                f"model produced {len(output)} class scores but {len(self._labels)} labels "
                f"were given -- label list is stale for this model")
        return {label: float(p) for label, p in zip(self._labels, output)}


class ClassifierRegistry:
    """device_type -> lazily-loaded, cached FaultClassifier, per
    docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md S4 (T3/T5) -- one
    instance per device_type (not per-node, unlike the autoencoder's
    per-node model_path: a fetched EI model already applies to every node
    of that type, api/ei_controller.py's own _model_path()/labels_for()).

    Reads directly from <models_dir>/<device_type>.tflite +
    <device_type>.labels.json (same filenames api/ei_controller.py's
    fetch_model() writes) rather than depending on EIController itself --
    pipeline/ never imports from api/ in this codebase (the dependency
    runs the other way), so this stays a sibling of that convention, not a
    consumer of the class that owns it.

    Hot-reloads on mtime change so a fresh Fetch while this process is
    already running gets picked up on the next get() -- same "rebuild
    when the on-disk model changed under us" pattern
    MotorPipeline.handle_frame already uses to rebuild a stale
    InferencePipeline after re-commissioning (pipeline/manager.py)."""

    def __init__(self, models_dir: str,
                 interpreter_factory: Callable[[str], Tuple[object, str]] = _default_interpreter):
        self._models_dir = models_dir
        self._interpreter_factory = interpreter_factory
        # device_type -> (FaultClassifier, model mtime at load time)
        self._loaded: Dict[str, Tuple[FaultClassifier, float]] = {}

    def _model_path(self, device_type: str) -> str:
        return os.path.join(self._models_dir, f"{device_type}.tflite")

    def _labels_path(self, device_type: str) -> str:
        return os.path.join(self._models_dir, f"{device_type}.labels.json")

    def get(self, device_type: str) -> Optional[FaultClassifier]:
        """None if device_type has no fetched model (or its labels file is
        missing/stale enough to have been removed independently -- fails
        soft, the same "no model, no classification attempted" contract
        as a node with no device_type at all)."""
        model_path = self._model_path(device_type)
        if not os.path.isfile(model_path):
            self._loaded.pop(device_type, None)
            return None

        mtime = os.path.getmtime(model_path)
        cached = self._loaded.get(device_type)
        if cached is not None and cached[1] == mtime:
            return cached[0]

        labels_path = self._labels_path(device_type)
        if not os.path.isfile(labels_path):
            logger.warning(
                "model exists for device_type %r but its labels file is missing (%s) -- "
                "skipping classification until the next successful Fetch", device_type, labels_path)
            return None
        with open(labels_path) as f:
            labels = json.load(f)

        classifier = FaultClassifier(model_path, labels, interpreter_factory=self._interpreter_factory)
        self._loaded[device_type] = (classifier, mtime)
        return classifier
