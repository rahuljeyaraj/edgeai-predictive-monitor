# Appendix A: Detailed Selection Criteria — SmartElex KX134-1211

This appendix expands on the selection rationale summarized in the main report, breaking down each evaluation criterion individually. It applies to both deployment points in the EdgeAI Predictive Monitor (EPM) architecture — the ESP32S3 satellite sensor nodes and the Arduino UNO Q base station — since the KX134 is used as the primary vibration transducer on both.

---

## A.1 Bandwidth (Frequency Response)

Early-stage mechanical failure modes — micro-pitting, gear mesh wear, incipient bearing race damage — generate energy in the 2–10 kHz band, well above the range most low-cost accelerometers can resolve. Bandwidth was treated as a hard filter before any other criterion was considered, because a sensor that cannot physically see the frequency band of interest cannot be compensated for downstream in software, regardless of how good the FFT pipeline is.

| Sensor class | Max ODR | Usable Nyquist ceiling |
|---|---|---|
| Hobby (ADXL335 / MPU-6050) | ~500 Hz–1 kHz | 250–500 Hz |
| Consumer (ADXL345) | 3.2 kHz | 1.6 kHz |
| **KX134 (selected)** | **25.6 kHz** | **12.8 kHz** |
| Industrial analog (ADXL1002) | Continuous | ~11 kHz (after external ADC) |

The KX134's 12.8 kHz Nyquist ceiling comfortably covers the target diagnostic band with margin, which matters because real motor vibration spectra are not clean — harmonics and sidebands from gear meshing and bearing defect frequencies extend above the fundamental fault frequency, and clipping the spectrum too close to the signal of interest loses diagnostic content.

## A.2 Dynamic Range (Max g)

Industrial motors and pumps don't just vibrate steadily — they experience startup torque transients, load-step shocks, and (in fault conditions) impact events from spalled bearing surfaces. A fixed low dynamic range risks clipping exactly the high-amplitude transient events that are often the most diagnostically useful.

The KX134 offers a software-selectable range of ±8g / ±16g / ±32g / ±64g. This was a deciding factor over fixed-range sensors because it allows the same physical sensor to be reconfigured per deployment without a hardware swap — a quiet bearing test rig can run at ±8g for resolution, while a production motor with startup transients can run at ±32g or ±64g without clipping. This flexibility also reduces the SKU/inventory burden of standardizing across heterogeneous machine types.

## A.3 Noise Density

Noise floor sets the practical detection limit for early-stage faults, which by definition produce small-amplitude signals before they grow into something audible or visible. A noisy sensor effectively raises the μ+3σ anomaly threshold used by the autoencoder pipeline, making the system blind to the very early-warning signals it exists to catch.

| Sensor | Noise density |
|---|---|
| Hobby / Consumer grade | ~300 µg/√Hz |
| **KX134 (selected)** | **~130 µg/√Hz** |
| Industrial (ADXL356B / ADXL1002) | 25–80 µg/√Hz |

The KX134 sits roughly 2.3× better than consumer-grade parts, which meaningfully tightens the achievable anomaly threshold without paying the cost premium of true industrial-grade analog sensors. It is a deliberate middle point: noise performance good enough that incipient-fault signals aren't buried, without the cost and ADC-interfacing complexity of the top-tier parts (see A.6).

## A.4 SPI Burst-Read Speed

Because the autoencoder pipeline runs FFT windows in near-real-time on constrained MCU/MPU resources, the *getting data off the sensor* step has to be cheap, or it eats into the compute budget available for signal processing.

The KX134 communicates over SPI at up to 10 MHz, with all digitization done on-chip via an integrated 16-bit ADC. This matters for two reasons in this architecture specifically:

- **On the ESP32S3 node**, SPI burst transfers free up CPU cycles that would otherwise be spent managing analog sampling timing, leaving more headroom for any local pre-processing before BLE transmission to the UNO Q base station.
- **On the UNO Q base station**, the same interface pattern applies directly to the STM32U585 (Zephyr RTOS side), keeping the sensor acquisition path consistent across both connection points in the system — one driver model, one timing profile, reused on two different MCUs.

A 10 MHz SPI bus comfortably outpaces the sensor's own 25.6 kHz max output rate by a wide margin, so the bus is never the bottleneck — acquisition timing is governed by the sensor's internal sample clock, not by transfer speed.

## A.5 FIFO / Burst-Read Capability

The KX134's 512-byte hardware FIFO is one of the most operationally important features for this system, independent of raw bandwidth or noise specs.

Without a FIFO, the host MCU must service the sensor on every single sample — at 25.6 kHz that means an interrupt or polling event roughly every 39 microseconds, which is an aggressive real-time burden for a Zephyr task that also needs to run FFT windows and manage UART/BLE communication.

With the FIFO:

- The sensor buffers a batch of multi-axis samples internally.
- A single hardware interrupt fires once the watermark threshold is reached.
- The host performs one burst SPI read to pull the whole batch.

This converts a high-frequency, low-latency-sensitive interrupt load into a low-frequency, batched one — which is the difference between "real-time FFT processing is feasible on this hardware" and "the MCU spends all its time servicing the sensor." This was a key factor in keeping both the STM32U585 (already constrained by the UART throughput work) and the ESP32S3 satellite nodes from being saturated by sensor I/O alone.

## A.6 Cost

| Sensor class | Approx. unit cost (INR) |
|---|---|
| Hobby grade | ₹150–230 |
| Consumer grade (ADXL345) | ₹170–350 |
| **KX134 (selected)** | **~₹913** |
| Industrial grade | ₹3,800–7,200 |

Cost matters at the system level, not just per-unit: the architecture is explicitly designed to scale toward 20+ sensor nodes, and the competition rules only require one UNO Q purchase proof, not one per node. At ₹913 per sensor, scaling sensor count is a near-linear, manageable cost. At industrial-grade pricing (₹3,800–7,200), the same 20-node target becomes cost-prohibitive for a student/challenge project, and would undermine the very scalability story the architecture is built around. The KX134 was the highest-performing option still compatible with that scaling plan.

## A.7 Availability in India

Sourcing reliability was a practical, non-negotiable constraint given competition timelines. The KX134 evaluated here is integrated onto the **SmartElex breakout platform**, a board sourced through the regional Indian electronics market — avoiding the long international shipping lead times and import-duty uncertainty that come with sourcing equivalent industrial-grade boards (e.g. Grove ADXL356B) from overseas suppliers. This reduces schedule risk for both initial prototyping and any later re-orders needed if a unit is damaged during testing.

## A.8 Interfacing Simplicity (Digital vs. Analog Output)

This criterion reinforces both the cost and noise analysis above but deserves its own note: the KX134 outputs a fully digitized signal over SPI, whereas the industrial-grade alternatives (ADXL1002, ADXL356B) output raw analog voltage.

Analog output sensors push the digitization burden onto the host's ADC. On a microcontroller like the ESP32S3, the built-in SAR ADC introduces switching noise and non-linearity that can degrade signal quality unless paired with an external precision ADC — adding cost, board complexity, and another point of failure to two separate node designs (ESP32S3 and STM32U585) rather than one. By choosing a sensor with on-chip digitization, this complexity is eliminated entirely at both connection points in the architecture.

---

## A.9 Summary: Criteria Weighting

| Criterion | Why it mattered for EPM specifically |
|---|---|
| Bandwidth | Hard filter — early fault signatures live above 2 kHz; non-negotiable |
| Dynamic range | Must survive motor startup transients without reconfiguring hardware |
| Noise density | Directly sets the achievable μ+3σ anomaly threshold |
| SPI burst speed | Keeps sensor I/O from competing with on-device FFT/autoencoder compute |
| FIFO capability | Converts high-frequency sampling into manageable batched interrupts on both STM32U585 and ESP32S3 |
| Cost | Determines whether 20+ node scaling is financially realistic |
| India availability | Reduces lead-time and import risk against competition deadlines |
| Digital interface | Avoids duplicating analog front-end design across two different MCU platforms |

No single criterion justified the KX134 in isolation — hobby sensors fail on bandwidth alone, while industrial sensors fail on cost and scalability alone. The KX134 was selected because it is the first point in the market spectrum where *all* of these constraints are satisfied simultaneously.
