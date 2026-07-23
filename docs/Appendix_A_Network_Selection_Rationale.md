# Appendix A: Network Selection Rationale — WiFi over BLE

## Context

The EdgeAI Predictive Monitor (EPM) architecture requires real-time, bidirectional
communication between the ESP32 satellite sensor nodes and the Arduino UNO Q base station
(QRB2210 MPU). This appendix documents the evaluation that led to selecting **WiFi**
(UNO Q-hosted access point + MQTT) over **Bluetooth Low Energy (BLE)** as the
node-to-base station transport, and the reasoning behind that decision.

## Requirement

The data link between ESP32 nodes and the base station was defined as non-negotiable on two points:

1. **Real-time streaming** of FFT spectrum data from each node, continuously, not in
   periodic bursts.
2. **Bidirectional communication** — the base station must be able to send commands
   (commissioning triggers, configuration changes) back to each node, not just receive data.

## Options considered

### BLE Advertise-Only (Beacon Pattern)

The original architectural plan used a connectionless BLE pattern: ESP32 nodes
broadcast advertising packets continuously; the base station passively scans and ingests
whatever it hears, with no connection state per node.

- **Why it was attractive initially**: scales to many nodes (20+) without per-connection
  management overhead, very low power for intermittent beaconing, no network
  infrastructure required.
- **Why it was rejected for this requirement**: advertise-only is inherently **one-way**.
  There is no return channel from the base station to a specific node without switching to a
  connection-based mode. Once bidirectional control became non-negotiable, this option
  was eliminated outright — no amount of protocol cleverness fixes a fundamentally
  one-directional transport.

### BLE GATT (Connection-Based)

ESP32 as GATT peripheral/server (streaming FFT via Notify characteristics), UNO Q as
GATT central/client (sending commands via Write characteristics), with the base station
maintaining concurrent connections to multiple nodes.

- **Why it was attractive**: native bidirectional support (Notify + Write), tighter
  connection intervals can support reasonably real-time streaming, stays within the
  BLE ecosystem originally planned.
- **Why it was rejected**: investigation surfaced multiple documented BlueZ issues
  with concurrent GATT connections to multiple peripherals from a single central —
  including cases where GATT service resolution fails or hangs on the second
  concurrently-connected device, reproduced across several BlueZ versions. For a
  non-negotiable real-time link, this represented an unacceptable reliability risk,
  particularly discovered late in the build cycle with a fixed competition deadline.
  Additionally, the demo's two-node scale removed BLE's main structural advantage
  (scaling past practical multi-connection limits that WiFi/MQTT handles natively via
  pub-sub), while continuous streaming (rather than intermittent beaconing) erodes
  BLE's primary power advantage over WiFi.

### WiFi (UNO Q-hosted Access Point + MQTT)

The UNO Q's onboard WCBN3536A wireless module was confirmed (via `iw list` on-device)
to support AP (access point) mode in addition to station mode. This allows the UNO Q
to host its own WiFi network, with ESP32 nodes (and the dashboard device) joining as
clients — removing any dependency on existing site infrastructure (e.g., venue WiFi,
which cannot be trusted at a competition).

- **Why it was selected**:
  - **Reliability**: WiFi/TCP sockets and MQTT pub-sub are mature, well-understood,
    free of the concurrency issues found in BLE GATT for this use case.
  - **Throughput**: WiFi provides Mbps-class bandwidth with no MTU-driven payload
    chunking, allowing full-resolution spectrum data to be streamed without the
    aggressive size constraints BLE would impose (BLE advertising payloads are
    capped around 31 bytes; even GATT MTU-negotiated payloads are far smaller than
    typical WiFi packet sizes).
  - **Infrastructure reuse**: the project's existing MQTT broker, FastAPI ingestion
    backend, and SQLite storage pipeline could be reused directly, rather than
    building a parallel BLE-specific ingestion path.
  - **No external dependency**: hosting the AP on the UNO Q itself means no router,
    access point, or existing network is required at deployment or demo time —
    arguably a *stronger* "self-contained edge base station" story than BLE would have provided.
- **Trade-off accepted**: WiFi's continuous radio use is not inherently lower-power
  than BLE for a sustained streaming workload — but since the requirement
  (continuous real-time streaming) already negates BLE's main power advantage,
  this trade-off was assessed as a wash rather than a net loss.

## Decision

**WiFi was selected** as the ESP32-to-base station transport. The UNO Q (QRB2210) hosts a
2.4 GHz WiFi access point; ESP32 nodes and the dashboard device join this network as
clients. Communication is mediated by an MQTT broker (Mosquitto) running on the
UNO Q, with ESP32 nodes publishing sensor data and the base station publishing commands —
satisfying both the real-time and bidirectional requirements without the reliability
risk identified in the BLE GATT path.

## Note on future direction

BLE advertise-only beaconing remains a credible **production-scale** pattern for
deployments with many (20+) low-power sensor nodes where continuous real-time
streaming is not required (e.g., periodic health summaries rather than live FFT
streaming). This is noted as a potential future architecture direction, separate
from the demo and current implementation, which prioritizes streaming fidelity and
reliability over node-count scalability and power efficiency.
