#!/usr/bin/env python3
"""
Fault classifier verification (pipeline/classifier.py,
docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md S4 T3): FaultClassifier's
request/response shape against a hand-rolled fake TFLite interpreter, same
"duck-typed fake, no mock library" convention as ei_client_test.py's
FakeResponse -- ai-edge-litert isn't installed in this dev environment, so
the interpreter_factory injection point is exercised directly instead of
the real ai_edge_litert.interpreter import. Also covers ClassifierRegistry's
device_type -> cached-classifier lookup and mtime-triggered hot-reload.

Run with PYTHONPATH covering base-station/python/pipeline:
    PYTHONPATH=base-station/python/pipeline python3 base-station/tests/classifier_test.py
"""
import json
import os
import sys
import tempfile

import numpy as np

from classifier import ClassifierRegistry, FaultClassifier


class FakeInterpreter:
    """Stands in for ai_edge_litert.interpreter.Interpreter -- just enough
    surface for FaultClassifier.classify() (get_*_details/set_tensor/
    invoke/get_tensor), with the raw output values fixed at construction."""

    def __init__(self, output_values, input_dtype=np.float32, output_dtype=np.float32,
                 input_quant=(1.0, 0), output_quant=(1.0, 0)):
        self._output = np.array(output_values, dtype=output_dtype)
        self._input_detail = {"index": 0, "dtype": input_dtype, "quantization": input_quant}
        self._output_detail = {"index": 1, "dtype": output_dtype, "quantization": output_quant}
        self.last_input = None

    def get_input_details(self):
        return [self._input_detail]

    def get_output_details(self):
        return [self._output_detail]

    def set_tensor(self, index, value):
        assert index == self._input_detail["index"]
        self.last_input = value

    def invoke(self):
        pass

    def get_tensor(self, index):
        assert index == self._output_detail["index"]
        return np.array([self._output])


def factory_for(interpreter, backend="cpu"):
    return lambda model_path: (interpreter, backend)


def test_classify_returns_label_probability_dict_float32():
    interpreter = FakeInterpreter([0.1, 0.7, 0.2])
    clf = FaultClassifier("fake.tflite", ["bearing", "healthy", "wear"],
                           interpreter_factory=factory_for(interpreter))
    result = clf.classify(tuple(0.0 for _ in range(4)))
    assert result.keys() == {"bearing", "healthy", "wear"}, result
    assert abs(result["bearing"] - 0.1) < 1e-6, result
    assert abs(result["healthy"] - 0.7) < 1e-6, result
    assert abs(result["wear"] - 0.2) < 1e-6, result
    assert clf.backend == "cpu", clf.backend
    print("classify() maps a float32 model's output onto the label list in order: PASS")


def test_classify_dequantizes_int8_output():
    # int8 [10, 100, 20] with scale=0.01, zero_point=0 -> [0.1, 1.0, 0.2]
    interpreter = FakeInterpreter([10, 100, 20], output_dtype=np.int8, output_quant=(0.01, 0))
    clf = FaultClassifier("fake.tflite", ["a", "b", "c"], interpreter_factory=factory_for(interpreter))
    result = clf.classify(tuple(0.0 for _ in range(4)))
    assert abs(result["a"] - 0.1) < 1e-6, result
    assert abs(result["b"] - 1.0) < 1e-6, result
    assert abs(result["c"] - 0.2) < 1e-6, result
    print("classify() dequantizes an int8 model's output using its own scale/zero_point: PASS")


def test_classify_quantizes_int8_input():
    interpreter = FakeInterpreter([1.0], input_dtype=np.int8, input_quant=(0.5, 2))
    clf = FaultClassifier("fake.tflite", ["only"], interpreter_factory=factory_for(interpreter))
    clf.classify((1.0, 2.0))
    # x/scale + zero_point, rounded: 1.0/0.5+2=4, 2.0/0.5+2=6
    assert interpreter.last_input.dtype == np.int8, interpreter.last_input.dtype
    assert list(interpreter.last_input[0]) == [4, 6], interpreter.last_input
    print("classify() quantizes float input into an int8 model's own scale/zero_point: PASS")


def test_classify_raises_on_label_count_mismatch():
    interpreter = FakeInterpreter([0.5, 0.5])
    clf = FaultClassifier("fake.tflite", ["only-one"], interpreter_factory=factory_for(interpreter))
    try:
        clf.classify((0.0,))
        assert False, "expected ValueError for a label list that doesn't match model output width"
    except ValueError as e:
        assert "2 class scores" in str(e), e
        assert "1 labels" in str(e), e
    print("classify() raises when the label list doesn't match the model's output width: PASS")


def test_empty_labels_raises():
    try:
        FaultClassifier("fake.tflite", [], interpreter_factory=factory_for(FakeInterpreter([])))
        assert False, "expected ValueError for an empty label list"
    except ValueError:
        pass
    print("FaultClassifier() rejects an empty label list: PASS")


def test_backend_is_recorded_from_interpreter_factory():
    interpreter = FakeInterpreter([1.0])
    clf = FaultClassifier("fake.tflite", ["only"], interpreter_factory=factory_for(interpreter, backend="gpu"))
    assert clf.backend == "gpu", clf.backend
    print("FaultClassifier records whichever backend interpreter_factory actually loaded: PASS")


def write_model(models_dir, device_type, labels, tflite_bytes=b"fake-tflite-bytes"):
    with open(os.path.join(models_dir, f"{device_type}.tflite"), "wb") as f:
        f.write(tflite_bytes)
    with open(os.path.join(models_dir, f"{device_type}.labels.json"), "w") as f:
        json.dump(labels, f)


def counting_factory(interpreter):
    """Wraps factory_for() with a call counter so tests can assert
    ClassifierRegistry actually caches instead of reloading every get()."""
    calls = []

    def factory(model_path):
        calls.append(model_path)
        return interpreter, "cpu"

    factory.calls = calls
    return factory


def test_classifier_registry_returns_none_for_no_model():
    with tempfile.TemporaryDirectory() as models_dir:
        registry = ClassifierRegistry(models_dir)
        assert registry.get("motor001") is None
    print("ClassifierRegistry.get() returns None when no model has been fetched: PASS")


def test_classifier_registry_loads_and_caches():
    with tempfile.TemporaryDirectory() as models_dir:
        write_model(models_dir, "motor001", ["bearing", "healthy"])
        factory = counting_factory(FakeInterpreter([0.3, 0.7]))
        registry = ClassifierRegistry(models_dir, interpreter_factory=factory)

        first = registry.get("motor001")
        second = registry.get("motor001")

        assert first is second, "must return the cached instance, not reload every call"
        assert len(factory.calls) == 1, factory.calls
        result = first.classify((0.0,))
        assert abs(result["bearing"] - 0.3) < 1e-6 and abs(result["healthy"] - 0.7) < 1e-6, result
    print("ClassifierRegistry.get() loads once and caches the FaultClassifier instance: PASS")


def test_classifier_registry_hot_reloads_on_mtime_change():
    with tempfile.TemporaryDirectory() as models_dir:
        write_model(models_dir, "motor001", ["bearing", "healthy"])
        factory = counting_factory(FakeInterpreter([0.3, 0.7]))
        registry = ClassifierRegistry(models_dir, interpreter_factory=factory)
        first = registry.get("motor001")

        # Simulate a fresh Fetch overwriting the .tflite (ei_controller.py's
        # fetch_model() os.replace()s a new file into the same path) --
        # force mtime forward explicitly (not a sleep) so this can't flake
        # on a filesystem with coarse (e.g. 1s) timestamp granularity.
        write_model(models_dir, "motor001", ["bearing", "healthy"])
        model_path = os.path.join(models_dir, "motor001.tflite")
        future = os.path.getmtime(model_path) + 5
        os.utime(model_path, (future, future))

        second = registry.get("motor001")
        assert second is not first, "must reload once the model file's mtime changes"
        assert len(factory.calls) == 2, factory.calls
    print("ClassifierRegistry.get() hot-reloads when the model file's mtime changes: PASS")


def test_classifier_registry_soft_fails_on_missing_labels_file():
    with tempfile.TemporaryDirectory() as models_dir:
        with open(os.path.join(models_dir, "motor001.tflite"), "wb") as f:
            f.write(b"fake-tflite-bytes")  # no matching .labels.json
        registry = ClassifierRegistry(models_dir, interpreter_factory=counting_factory(FakeInterpreter([1.0])))
        assert registry.get("motor001") is None
    print("ClassifierRegistry.get() returns None (not raise) when the labels file is missing: PASS")


def main():
    test_classify_returns_label_probability_dict_float32()
    test_classify_dequantizes_int8_output()
    test_classify_quantizes_int8_input()
    test_classify_raises_on_label_count_mismatch()
    test_empty_labels_raises()
    test_backend_is_recorded_from_interpreter_factory()
    test_classifier_registry_returns_none_for_no_model()
    test_classifier_registry_loads_and_caches()
    test_classifier_registry_hot_reloads_on_mtime_change()
    test_classifier_registry_soft_fails_on_missing_labels_file()
    print("RESULT: PASS - FaultClassifier scores a vector against an injected TFLite "
          "interpreter, quantization-aware, in label order, and ClassifierRegistry "
          "loads/caches/hot-reloads per device_type")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
