#!/usr/bin/env python3
"""
Ported from edgeai-predictive-monitor-unoq/mpu/tests/pipeline_manager_test.py.

Pure-logic test (no hardware) for PipelineManager: feed frames from 2+
distinct node_ids -- one for this device's own SPI-connected sensors
(node_id="base_station", ingestion/spi_reader.py's BASE_STATION_NODE_ID),
one a synthetic satellite-style MQTT frame (node_id="sat-1") -- and confirm
each routes to its own MotorPipeline instance, and that the registry gains
a persisted entry for both, auto-created on first sight.

Also covers gate/features/autoencoder/inference wiring into
PipelineManager ("Motor Pipeline -- fully self-contained per motor:
gate -> features -> model -> score"): a node with no trained model still
just counts frames, and a commissioned node gets scored on every gated
frame with the result landing in HistoryStore.

Run with PYTHONPATH covering base-station/python/{ingestion,registry,pipeline,history}:
    PYTHONPATH=base-station/python/ingestion:base-station/python/registry:\\
base-station/python/pipeline:base-station/python/history \\
        python3 base-station/tests/pipeline_manager_test.py
"""
import json
import os
import sys
import tempfile

import numpy as np

from sensor_frame import BASE_STATION_NODE_ID, FrameSource, SensorFrame
from registry import NodeNotFoundError, Registry, SensorChannel
from features import build_feature_vector
from autoencoder import build_autoencoder, save_model, train_autoencoder
from classifier import ClassifierRegistry
from ei_scaling import save_scaling
from gate import MotorStateGate
from store import HistoryStore
from manager import PipelineManager


def default_gate_factory(node_id: str = "n") -> MotorStateGate:
    # Absolute-threshold mode (no energy_ref_provider): these tests assert the
    # gate's own debounce/threshold behaviour, not per-node calibration.
    return MotorStateGate(threshold=0.05, debounce_frames=3)


def base_station_frame() -> SensorFrame:
    """A frame shaped exactly like ingestion/spi_reader.py's on_frame
    output (mic + per-axis accel bins, node_id/source fixed) -- the SPI
    wire framing itself (magic/CRC/chunking) is a hardware-transport
    detail already covered by tests/spi_link_test.py on real hardware,
    not something this pure-logic test needs to re-simulate.

    128 bins per channel to match the registry's per-channel spectral dim
    (registry._DIM_BY_CHANNEL) -- manager.py's ingest-time frame-length
    check rejects any other count."""
    return SensorFrame(
        node_id=BASE_STATION_NODE_ID,
        source=FrameSource.SPI,
        timestamp=100.0,
        bins={
            "mic": tuple(float(i % 5 + 1) for i in range(128)),
            "accel_x": tuple(float(i % 7 + 1) for i in range(128)),
            "accel_y": tuple(float(i % 6 + 1) for i in range(128)),
            "accel_z": tuple(float(i % 4 + 1) for i in range(128)),
        },
    )


def satellite_frame() -> SensorFrame:
    """Hand-built to stand in for "a second, distinct node_id" fed over
    MQTT. mic-only: satellite firmware doesn't yet send per-axis accel
    spectra or scalars (base-station-only for now, see
    docs/SENSOR_TELEMETRY_FRAME_PLAN.md) and the old combined `accel`
    channel it used to report is no longer a SensorChannel (superseded by
    per-axis on the base station side) -- mic is the only channel a
    satellite node can currently commit to the model with."""
    return SensorFrame(
        node_id="sat-1",
        source=FrameSource.MQTT,
        timestamp=200.0,
        bins={"mic": tuple(float(i % 9 + 1) for i in range(128))},
    )


def main():
    tmp_dir = tempfile.mkdtemp(prefix="pipeline_manager_test_")
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    manager = PipelineManager(registry, default_gate_factory)

    base_station = base_station_frame()
    sat = satellite_frame()

    manager.route(base_station)
    manager.route(sat)
    manager.route(base_station)  # second frame from the same node -- must reuse the pipeline
    print("routed 3 frames across 2 node_ids: PASS")

    pipelines = manager.pipelines()
    assert set(pipelines.keys()) == {BASE_STATION_NODE_ID, "sat-1"}, pipelines.keys()
    assert pipelines[BASE_STATION_NODE_ID] is manager.pipelines()[BASE_STATION_NODE_ID]
    assert pipelines[BASE_STATION_NODE_ID].frame_count == 2, pipelines[BASE_STATION_NODE_ID].frame_count
    assert pipelines["sat-1"].frame_count == 1, pipelines["sat-1"].frame_count
    print("each node_id routed to its own pipeline instance, counts correct: PASS")

    entries = registry.list()
    assert set(entries.keys()) == {BASE_STATION_NODE_ID, "sat-1"}, entries.keys()

    base_station_entry = entries[BASE_STATION_NODE_ID]
    assert base_station_entry.sensor_config == frozenset(
        {SensorChannel.MIC, SensorChannel.ACCEL_X, SensorChannel.ACCEL_Y, SensorChannel.ACCEL_Z}
    ), base_station_entry.sensor_config
    assert base_station_entry.input_dim == 536, base_station_entry.input_dim
    assert base_station_entry.last_seen == 100.0, base_station_entry.last_seen

    sat_entry = entries["sat-1"]
    assert sat_entry.sensor_config == frozenset({SensorChannel.MIC}), sat_entry.sensor_config
    assert sat_entry.input_dim == 134, sat_entry.input_dim
    assert sat_entry.last_seen == 200.0, sat_entry.last_seen
    print("registry auto-gained entries for both nodes with correct sensor_config: PASS")

    print("RESULT: PASS - frames routed per-node_id, registry entries auto-created")


NODE_ID = "commissioned-node"
DIM = 128  # SensorChannel.ACCEL_X's spectral bin count (registry._DIM_BY_CHANNEL)
INPUT_DIM = 134  # + 6-value scalar tail
HEALTHY_BINS = tuple(1.0 for _ in range(DIM))
FAULT_BINS = tuple(4.0 if i % 2 == 0 else 1.0 for i in range(DIM))

# Fixed, identical on every synthetic frame below -- these tests are about
# manager.py's routing/inference/history wiring, not the scalar tail's own
# signal, so every frame's anomaly-relevant signal comes from accel_x's bins
# alone.
#
# accel_x rather than mic as the generic single channel here (unlike the
# routing tests above, which model a real mic-only satellite): mic is muted
# by default (features.MUTED_CHANNELS), so a mic-only fixture would score
# an all-zero vector and FAULT_BINS could never raise reconstruction error.
ACCEL_X_SCALARS = {"rms_x": 1.0, "kurtosis_x": 1.0, "std_x": 1.0,
                   "peak_x": 1.0, "crest_factor_x": 1.0, "skewness_x": 1.0}


def scored_frame(bins, timestamp) -> SensorFrame:
    return SensorFrame(node_id=NODE_ID, source=FrameSource.SPI, timestamp=timestamp,
                        bins={"accel_x": bins}, scalars=ACCEL_X_SCALARS)


def test_commissioned_node_routes_through_inference_and_writes_history():
    tmp_dir = tempfile.mkdtemp(prefix="pipeline_manager_test_")
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    registry.add(NODE_ID, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    history = HistoryStore(os.path.join(tmp_dir, "history.db"))

    # Commission for real via the gate/features/autoencoder modules
    # directly (no HTTP layer needed here -- this test is about
    # manager.py's wiring, not the REST commissioning endpoints already
    # covered by api_test.py). Trained on the real build_feature_vector()
    # output (spectral + scalar tail), matching what InferencePipeline
    # will actually score frames with below.
    model = build_autoencoder(INPUT_DIM)
    healthy_vector, _ = build_feature_vector(
        scored_frame(HEALTHY_BINS, 0.0), frozenset({SensorChannel.ACCEL_X}), INPUT_DIM)
    train_autoencoder(model, [healthy_vector] * 5, epochs=500)
    model_path = os.path.join(tmp_dir, f"{NODE_ID}.pt")
    save_model(model, model_path)
    registry.start_commissioning(NODE_ID)
    registry.stop_collecting(NODE_ID)
    registry.complete_commissioning(NODE_ID, model_path,
                                     warning_threshold=0.05, fault_threshold=0.2)

    manager = PipelineManager(
        registry, lambda node_id: MotorStateGate(threshold=0.5, debounce_frames=1),
        history_store=history, status_debounce_frames=1)

    # Before this frame the node has no per-motor pipeline yet -- route()
    # must lazily build one that already knows about the commissioned
    # model (registry.add() above happened before any route() call).
    manager.route(scored_frame(HEALTHY_BINS, timestamp=0.0))
    manager.route(scored_frame(HEALTHY_BINS, timestamp=1.0))
    manager.route(scored_frame(FAULT_BINS, timestamp=2.0))

    records = history.query(NODE_ID)
    assert len(records) == 3, records
    assert records[0].status_at_time.value == "healthy", records[0]
    assert records[2].status_at_time.value == "fault", records[2]
    assert records[2].anomaly_score > records[0].anomaly_score, records
    print("commissioned node is scored via real inference and history is recorded: PASS")

    assert registry.get(NODE_ID).status.value == "fault", registry.get(NODE_ID)
    print("confirmed status transitions are pushed back to the registry: PASS")


def test_recommissioning_rebuilds_stale_inference_pipeline():
    """Regression test: re-commissioning a node whose MotorPipeline already
    has a cached InferencePipeline must rebuild it. Before this fix,
    self._inference was built once (route()'s first commissioned frame)
    and never invalidated -- a later registry.complete_commissioning() call
    (an operator re-commissioning the same node while this process kept
    running) updated the registry's model_path/thresholds, which the
    dashboard reads straight from the registry and displays as current, but
    live scoring kept silently running against the *old*
    model/thresholds/standardization underneath. That's how a live score
    could sit 50x+ over the fault_threshold shown on screen while status
    stayed healthy: the displayed number wasn't the number set_status() was
    actually comparing against.

    Both commissionings keep the node's confirmed status at HEALTHY right
    up to the probe frame at the end (loose-then-tight thresholds, rather
    than tight-then-loose): the *cached* InferencePipeline's own private
    status tracking never re-syncs from the registry either, so if the
    first commissioning's thresholds ever confirmed a non-HEALTHY status,
    that stale in-memory belief -- not just stale thresholds -- would
    muddy which bug this test is actually pinning down."""
    tmp_dir = tempfile.mkdtemp(prefix="pipeline_manager_test_")
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    registry.add(NODE_ID, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    history = HistoryStore(os.path.join(tmp_dir, "history.db"))

    model = build_autoencoder(INPUT_DIM)
    healthy_vector, _ = build_feature_vector(
        scored_frame(HEALTHY_BINS, 0.0), frozenset({SensorChannel.ACCEL_X}), INPUT_DIM)
    train_autoencoder(model, [healthy_vector] * 5, epochs=500)
    model_path = os.path.join(tmp_dir, f"{NODE_ID}.pt")
    save_model(model, model_path)
    registry.start_commissioning(NODE_ID)
    registry.stop_collecting(NODE_ID)
    registry.complete_commissioning(NODE_ID, model_path, timestamp=1.0,
                                     warning_threshold=1e8, fault_threshold=1e9)

    manager = PipelineManager(
        registry, lambda node_id: MotorStateGate(threshold=0.5, debounce_frames=1),
        history_store=history, status_debounce_frames=1)

    # Builds and caches the pipeline against the first commissioning's
    # absurdly loose thresholds -- everything reads healthy, so the cached
    # pipeline's internal status tracking stays in lockstep with the
    # registry's (both HEALTHY) going into the recommission below.
    manager.route(scored_frame(FAULT_BINS, timestamp=0.0))
    assert registry.get(NODE_ID).status.value == "healthy", registry.get(NODE_ID)

    # Re-commission the same node with impossibly *tight* thresholds -- any
    # real reconstruction error should now read fault.
    registry.start_commissioning(NODE_ID)
    registry.stop_collecting(NODE_ID)
    registry.complete_commissioning(NODE_ID, model_path, timestamp=2.0,
                                     warning_threshold=1e-7, fault_threshold=1e-6)

    manager.route(scored_frame(FAULT_BINS, timestamp=2.0))
    entry = registry.get(NODE_ID)
    assert entry.status.value == "fault", entry.status
    print("recommissioning rebuilds the cached inference pipeline: PASS")


def test_paused_node_is_not_scored():
    """Regression test: pausing a node must stop inference from running at
    all, not just stop the confirmed status from changing. Before this fix,
    InferencePipeline kept computing/recording a fresh reconstruction error
    every frame while paused -- registry.set_status() rejected the write
    (InvalidTransitionError, swallowed in inference.py), but last_score,
    registry.last_anomaly_score, and history.record() had already happened,
    so the dashboard's anomaly score kept moving on a "paused" node."""
    tmp_dir = tempfile.mkdtemp(prefix="pipeline_manager_test_")
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    registry.add(NODE_ID, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    history = HistoryStore(os.path.join(tmp_dir, "history.db"))

    model = build_autoencoder(INPUT_DIM)
    healthy_vector, _ = build_feature_vector(
        scored_frame(HEALTHY_BINS, 0.0), frozenset({SensorChannel.ACCEL_X}), INPUT_DIM)
    train_autoencoder(model, [healthy_vector] * 5, epochs=500)
    model_path = os.path.join(tmp_dir, f"{NODE_ID}.pt")
    save_model(model, model_path)
    registry.start_commissioning(NODE_ID)
    registry.stop_collecting(NODE_ID)
    registry.complete_commissioning(NODE_ID, model_path,
                                     warning_threshold=0.05, fault_threshold=0.2)

    manager = PipelineManager(
        registry, lambda node_id: MotorStateGate(threshold=0.5, debounce_frames=1),
        history_store=history, status_debounce_frames=1)

    # Score once while running so there's a baseline last_anomaly_score to
    # confirm stays frozen after pause.
    manager.route(scored_frame(HEALTHY_BINS, timestamp=0.0))
    baseline_score = registry.get(NODE_ID).last_anomaly_score
    assert baseline_score is not None
    assert len(history.query(NODE_ID)) == 1

    registry.pause(NODE_ID)

    manager.route(scored_frame(FAULT_BINS, timestamp=1.0))
    manager.route(scored_frame(FAULT_BINS, timestamp=2.0))

    entry = registry.get(NODE_ID)
    assert entry.status.value == "paused", entry.status
    assert entry.last_anomaly_score == baseline_score, entry.last_anomaly_score
    assert len(history.query(NODE_ID)) == 1, history.query(NODE_ID)
    assert manager.pipelines()[NODE_ID].frame_count == 3
    print("paused node's frames are counted but not scored/recorded: PASS")


def test_resume_resyncs_stale_confirmed_status():
    """Regression test: resuming a paused node must re-sync the cached
    InferencePipeline's own confirmed-status tracking, not just the
    registry's. registry.resume() always forces entry.status to HEALTHY
    (registry.py's _NodeStateMachine.resume), trusting inference to
    re-diagnose within a few frames -- but before this fix, the cached
    InferencePipeline's private ._status was left holding whatever it was
    pre-pause (FAULT here, set up below). A post-resume frame that also
    scores FAULT then looks like "no change" to handle_frame's
    `raw_status == self._status` check, so registry.set_status() is never
    called and the registry stays wrongly stuck at HEALTHY forever -- even
    though the score (and the chart, which colors points from this same
    pipeline's .status) is plainly back in fault range. This is the bug
    behind a user-observed repro: pause on a confirmed fault, switch to a
    different fault signal, unpause -- graph stays in the red zone but the
    dashboard/sim status reports healthy."""
    tmp_dir = tempfile.mkdtemp(prefix="pipeline_manager_test_")
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    registry.add(NODE_ID, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    history = HistoryStore(os.path.join(tmp_dir, "history.db"))

    model = build_autoencoder(INPUT_DIM)
    healthy_vector, _ = build_feature_vector(
        scored_frame(HEALTHY_BINS, 0.0), frozenset({SensorChannel.ACCEL_X}), INPUT_DIM)
    train_autoencoder(model, [healthy_vector] * 5, epochs=500)
    model_path = os.path.join(tmp_dir, f"{NODE_ID}.pt")
    save_model(model, model_path)
    registry.start_commissioning(NODE_ID)
    registry.stop_collecting(NODE_ID)
    registry.complete_commissioning(NODE_ID, model_path,
                                     warning_threshold=0.05, fault_threshold=0.2)

    manager = PipelineManager(
        registry, lambda node_id: MotorStateGate(threshold=0.5, debounce_frames=1),
        history_store=history, status_debounce_frames=1)

    # Drive the node to a confirmed FAULT, caching an InferencePipeline
    # whose own ._status is FAULT.
    manager.route(scored_frame(FAULT_BINS, timestamp=0.0))
    assert registry.get(NODE_ID).status.value == "fault", registry.get(NODE_ID)

    registry.pause(NODE_ID)
    manager.route(scored_frame(FAULT_BINS, timestamp=1.0))  # frozen, not scored

    registry.resume(NODE_ID)
    assert registry.get(NODE_ID).status.value == "healthy", registry.get(NODE_ID)

    # Still fault-range data post-resume (standing in for the repro's
    # "switch to a different fault, unpause") -- without the fix this reads
    # as "no change from the stale cached FAULT" and never calls
    # set_status(), leaving the registry wrongly stuck at HEALTHY.
    manager.route(scored_frame(FAULT_BINS, timestamp=2.0))
    entry = registry.get(NODE_ID)
    assert entry.status.value == "fault", entry.status
    print("resume re-syncs the cached inference pipeline's stale confirmed status: PASS")


def test_frame_bin_count_mismatch_raises():
    """A frame whose bin count doesn't match the node's committed
    sensor_config (e.g. firmware fft_size drift) must raise loudly at
    routing time, not silently corrupt training/inference data."""
    tmp_dir = tempfile.mkdtemp(prefix="pipeline_manager_test_")
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    registry.add("mismatched-node", sensor_config=frozenset({SensorChannel.MIC}))
    manager = PipelineManager(registry, default_gate_factory)

    bad_frame = SensorFrame(node_id="mismatched-node", source=FrameSource.MQTT,
                             timestamp=0.0, bins={"mic": (1.0, 2.0, 3.0)})
    try:
        manager.route(bad_frame)
        assert False, "expected ValueError for bin-count/sensor_config mismatch"
    except ValueError as e:
        assert "mismatched-node" in str(e), e
        assert "expected 134 bins" in str(e), e
    print("frame bin count mismatch against registered sensor_config raises: PASS")


def test_uncommissioned_node_only_counts_frames():
    tmp_dir = tempfile.mkdtemp(prefix="pipeline_manager_test_")
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    history = HistoryStore(os.path.join(tmp_dir, "history.db"))
    manager = PipelineManager(registry, default_gate_factory, history_store=history)

    manager.route(scored_frame(HEALTHY_BINS, timestamp=0.0))
    manager.route(scored_frame(HEALTHY_BINS, timestamp=1.0))

    assert manager.pipelines()[NODE_ID].frame_count == 2
    assert history.query(NODE_ID) == []
    print("uncommissioned node is routed without error and produces no history: PASS")


def test_decommission_removes_mid_commissioning_node():
    """PipelineManager.decommission() is removable from any status (the
    dashboard's bin icon is always enabled, including for a node still
    mid-commissioning) -- confirms a TRAINING-status node's pipeline and
    history are actually evicted, and that an unknown node_id still
    raises NodeNotFoundError rather than silently no-op'ing."""
    tmp_dir = tempfile.mkdtemp(prefix="pipeline_manager_test_")
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    history = HistoryStore(os.path.join(tmp_dir, "history.db"))
    manager = PipelineManager(registry, default_gate_factory, history_store=history)

    registry.add(NODE_ID, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.start_commissioning(NODE_ID)
    manager.route(scored_frame(HEALTHY_BINS, timestamp=0.0))
    history.record(NODE_ID, 0.0, 0.01, registry.get(NODE_ID).status)

    manager.decommission(NODE_ID)

    assert NODE_ID not in manager.pipelines(), manager.pipelines()
    assert history.query(NODE_ID) == [], history.query(NODE_ID)
    try:
        registry.get(NODE_ID)
        assert False, "expected NodeNotFoundError"
    except NodeNotFoundError:
        pass
    print("decommission removes a mid-commissioning node's pipeline + history: PASS")

    try:
        manager.decommission("no-such-node")
        assert False, "expected NodeNotFoundError for an unknown node"
    except NodeNotFoundError:
        pass
    print("decommission refused for an unknown node raises NodeNotFoundError: PASS")


def test_non_sensor_channel_bin_key_is_ignored_not_raised():
    """Regression test: a frame.bins key that isn't a SensorChannel (e.g. a
    hypothetical regression in the ingestion-layer bins/display_bins split,
    docs/CHART_CLUTTER_PLAN.md S1) must not crash sensor_config inference --
    this exact shape (an "accel" key alongside the per-axis accel_x/y/z
    ones) is what a base station sends today: fuser.cpp keeps writing the
    old combined `accel` channel at full resolution alongside the per-axis
    ones (for whatever else might still read it), but it's no longer a
    SensorChannel (superseded by per-axis for the model), the same
    "some wire channels aren't model inputs" shape that took down the whole
    SPI ingestion thread the first time the per-axis accel channels were
    tried against real hardware, before the bins/display_bins split existed."""
    tmp_dir = tempfile.mkdtemp(prefix="pipeline_manager_test_")
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    manager = PipelineManager(registry, default_gate_factory)

    frame = SensorFrame(
        node_id="regression-node",
        source=FrameSource.SPI,
        timestamp=0.0,
        bins={
            "mic": tuple(1.0 for _ in range(128)),
            "accel_x": tuple(1.0 for _ in range(128)),
            "accel_y": tuple(1.0 for _ in range(128)),
            "accel_z": tuple(1.0 for _ in range(128)),
            "accel": tuple(1.0 for _ in range(512)),  # not a SensorChannel anymore
        },
    )
    manager.route(frame)  # must not raise

    entry = registry.get("regression-node")
    assert entry.sensor_config == frozenset(
        {SensorChannel.MIC, SensorChannel.ACCEL_X, SensorChannel.ACCEL_Y, SensorChannel.ACCEL_Z}
    ), entry.sensor_config
    print("non-SensorChannel bins key is skipped, not raised, during sensor_config inference: PASS")


def test_dynamic_input_dim_from_first_frame():
    """A node's committed input_dim comes from whatever bin count its own
    first frame actually sent, not a fixed per-channel table -- not every
    node uses the same FFT bin count (e.g. a satellite sim node configured
    for a smaller/larger accel spectrum than the base station's own 128).
    A later frame matching that same non-standard commitment must route
    cleanly; one that doesn't must still raise."""
    tmp_dir = tempfile.mkdtemp(prefix="pipeline_manager_test_")
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    manager = PipelineManager(registry, default_gate_factory)

    first = SensorFrame(node_id="odd-bins-node", source=FrameSource.MQTT, timestamp=0.0,
                         bins={"accel_x": tuple(1.0 for _ in range(64))})
    manager.route(first)

    entry = registry.get("odd-bins-node")
    assert entry.sensor_config == frozenset({SensorChannel.ACCEL_X}), entry.sensor_config
    assert entry.input_dim == 70, entry.input_dim  # 64 spectral + 6-value scalar tail

    matching = SensorFrame(node_id="odd-bins-node", source=FrameSource.MQTT, timestamp=1.0,
                            bins={"accel_x": tuple(2.0 for _ in range(64))})
    manager.route(matching)  # must not raise -- matches the node's own committed 64

    mismatched = SensorFrame(node_id="odd-bins-node", source=FrameSource.MQTT, timestamp=2.0,
                              bins={"accel_x": tuple(3.0 for _ in range(128))})
    try:
        manager.route(mismatched)
        assert False, "expected ValueError: 128 bins doesn't match this node's committed 64"
    except ValueError as e:
        assert "expected 70 bins" in str(e), e
    print("node's own first-frame bin count (not a fixed table) is what later frames are validated against: PASS")


def fake_classify_interpreter(scores_by_label):
    """interpreter_factory that always reports the given fixed
    {label: probability} dict regardless of input -- classify()'s own
    quantization plumbing is already covered by classifier_test.py, this
    only needs to exercise manager.py's wiring into it."""
    values = list(scores_by_label.values())

    class FakeInterpreter:
        def get_input_details(self):
            return [{"index": 0, "dtype": np.float32, "quantization": (0.0, 0)}]

        def get_output_details(self):
            return [{"index": 1, "dtype": np.float32, "quantization": (0.0, 0)}]

        def set_tensor(self, index, value):
            pass

        def invoke(self):
            pass

        def get_tensor(self, index):
            return np.array([values], dtype=np.float32)

    return lambda model_path: (FakeInterpreter(), "cpu")


def classifier_env(labels_and_scores):
    """A registry + classifier setup sharing NODE_ID/DIM's shape above --
    device_type "motor001", a fetched model reporting labels_and_scores on
    every classify() call, and a matching ei_scaling.json baseline (upload()
    always fits+saves one before a model is fetchable in the real flow, see
    manager.py's _maybe_classify() docstring)."""
    tmp_dir = tempfile.mkdtemp(prefix="pipeline_manager_test_")
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    registry.add(NODE_ID, sensor_config=frozenset({SensorChannel.ACCEL_X}))
    registry.set_device_type(NODE_ID, "motor001")

    models_dir = os.path.join(tmp_dir, "ei_models")
    os.makedirs(models_dir)
    with open(os.path.join(models_dir, "motor001.tflite"), "wb") as f:
        f.write(b"fake-tflite-bytes")
    with open(os.path.join(models_dir, "motor001.labels.json"), "w") as f:
        json.dump(list(labels_and_scores.keys()), f)

    scaling_path = os.path.join(tmp_dir, "ei_scaling.json")
    save_scaling(scaling_path, "motor001", spectral_dim=DIM,
                 mu=tuple(0.0 for _ in range(6)), sigma=tuple(1.0 for _ in range(6)))

    classifier_registry = ClassifierRegistry(
        models_dir, interpreter_factory=fake_classify_interpreter(labels_and_scores))
    return registry, classifier_registry, scaling_path


def test_classification_runs_without_commissioning():
    """The classifier must not require this node to ever have been
    commissioned -- docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S6:
    "Classifier runs every frame, in parallel with the autoencoder -- both
    always-on independently whenever a model exists for that node's device
    type." Regression risk this pins down: gating classification behind the
    same `not entry.model_path` early-return the autoencoder uses (an easy
    thing to do by accident, since that's where the existing per-frame
    scoring hook already lives) would make classification wrongly depend on
    the unrelated autoencoder being commissioned."""
    registry, classifier_registry, scaling_path = classifier_env({"bearing": 0.2, "healthy": 0.8})
    events = []
    manager = PipelineManager(
        registry, lambda node_id: MotorStateGate(threshold=0.5, debounce_frames=1),
        classifier_registry=classifier_registry, scaling_path=scaling_path,
        on_classification=lambda node_id, ts, result: events.append((node_id, ts, result)))

    manager.route(scored_frame(HEALTHY_BINS, timestamp=0.0))

    entry = registry.get(NODE_ID)
    assert entry.model_path is None, "this node must stay uncommissioned for the test to prove anything"
    assert entry.last_anomaly_score is None, entry.last_anomaly_score
    result = entry.last_classification
    assert result["label"] == "healthy", result
    assert abs(result["confidence"] - 0.8) < 1e-6, result
    assert abs(result["scores"]["bearing"] - 0.2) < 1e-6, result
    assert abs(result["scores"]["healthy"] - 0.8) < 1e-6, result
    assert result["ts"] == 0.0, result
    assert events == [(NODE_ID, 0.0, result)], events
    print("classification runs on gated frames for a never-commissioned node, independent of the autoencoder: PASS")


def test_classification_skipped_with_no_device_type():
    registry, classifier_registry, scaling_path = classifier_env({"bearing": 0.2, "healthy": 0.8})
    registry.set_device_type(NODE_ID, None)
    manager = PipelineManager(
        registry, lambda node_id: MotorStateGate(threshold=0.5, debounce_frames=1),
        classifier_registry=classifier_registry, scaling_path=scaling_path)

    manager.route(scored_frame(HEALTHY_BINS, timestamp=0.0))

    assert registry.get(NODE_ID).last_classification is None
    print("classification is skipped for a node with no device_type: PASS")


def test_classification_skipped_with_no_fetched_model():
    registry, classifier_registry, scaling_path = classifier_env({"bearing": 0.2, "healthy": 0.8})
    registry.set_device_type(NODE_ID, "some-other-type")  # linked but no model fetched for THIS type
    manager = PipelineManager(
        registry, lambda node_id: MotorStateGate(threshold=0.5, debounce_frames=1),
        classifier_registry=classifier_registry, scaling_path=scaling_path)

    manager.route(scored_frame(HEALTHY_BINS, timestamp=0.0))

    assert registry.get(NODE_ID).last_classification is None
    print("classification is skipped for a device_type with no fetched model: PASS")


def test_classification_frozen_while_paused():
    """pause() only allows a healthy/warning/fault node (registry.py's
    state machine), so this node needs a real (trivial) commissioning to
    reach a pausable status first -- unlike
    test_classification_runs_without_commissioning above, which
    deliberately stays uncommissioned to prove the opposite point."""
    registry, classifier_registry, scaling_path = classifier_env({"bearing": 0.2, "healthy": 0.8})

    model = build_autoencoder(INPUT_DIM)
    healthy_vector, _ = build_feature_vector(
        scored_frame(HEALTHY_BINS, 0.0), frozenset({SensorChannel.ACCEL_X}), INPUT_DIM)
    train_autoencoder(model, [healthy_vector] * 5, epochs=50)
    model_path = os.path.join(tempfile.mkdtemp(prefix="pipeline_manager_test_"), f"{NODE_ID}.pt")
    save_model(model, model_path)
    registry.start_commissioning(NODE_ID)
    registry.stop_collecting(NODE_ID)
    registry.complete_commissioning(NODE_ID, model_path, warning_threshold=1e8, fault_threshold=1e9)

    manager = PipelineManager(
        registry, lambda node_id: MotorStateGate(threshold=0.5, debounce_frames=1),
        classifier_registry=classifier_registry, scaling_path=scaling_path)

    manager.route(scored_frame(HEALTHY_BINS, timestamp=0.0))
    entry = registry.get(NODE_ID)
    assert entry.last_classification is not None
    assert entry.last_anomaly_score is not None
    baseline_classification = entry.last_classification
    baseline_score = entry.last_anomaly_score

    registry.pause(NODE_ID)
    manager.route(scored_frame(HEALTHY_BINS, timestamp=1.0))

    entry = registry.get(NODE_ID)
    assert entry.last_classification == baseline_classification, entry.last_classification
    assert entry.last_anomaly_score == baseline_score, entry.last_anomaly_score
    print("classification is frozen on a paused node, same as the autoencoder score: PASS")


if __name__ == "__main__":
    try:
        main()
        test_commissioned_node_routes_through_inference_and_writes_history()
        test_recommissioning_rebuilds_stale_inference_pipeline()
        test_paused_node_is_not_scored()
        test_resume_resyncs_stale_confirmed_status()
        test_frame_bin_count_mismatch_raises()
        test_uncommissioned_node_only_counts_frames()
        test_non_sensor_channel_bin_key_is_ignored_not_raised()
        test_decommission_removes_mid_commissioning_node()
        test_dynamic_input_dim_from_first_frame()
        test_classification_runs_without_commissioning()
        test_classification_skipped_with_no_device_type()
        test_classification_skipped_with_no_fetched_model()
        test_classification_frozen_while_paused()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
