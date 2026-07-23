# Appendix: KX134 Accelerometer Interface — Bring-Up Issues and Resolution

*This appendix documents the issues encountered while bringing up the KX134 triple-axis accelerometer (SmartElex breakout, Kionix KX134-1211) on the EdgeAI Predictive Monitor hardware, and the sequence of changes that led to a working SPI interface.*

## A.1 Why SPI Was Required

The KX134 was selected for vibration sensing — bearing wear, micro-pitting, and cavitation detection — which depends on capturing high-frequency vibration content accurately. The KX134 supports an Output Data Rate (ODR) of up to 25.6 kHz and a digital interface speed of up to 10 MHz over SPI, versus 3.4 MHz (practical throughput considerably lower) over I2C. To resolve the frequency content needed for fault signatures at the sampling rates required, SPI was the only interface capable of sustaining the necessary data rate without becoming a bottleneck. I2C was evaluated only as a fallback, not as a long-term option.

## A.2 First SPI Attempt — Failure Symptoms

The first SPI bring-up attempt failed in two distinct ways:

- WHO_AM_I register reads returned `0x00` or `0xFF` instead of the expected `0x46`, indicating the SPI transaction itself was not completing correctly (no real communication with the device, or CS/clock/framing issues).
- On the occasions where WHO_AM_I did read back correctly, the X/Y/Z acceleration values were unstable — they jumped around even with the sensor sitting completely still, which pointed to a signal integrity problem rather than a configuration error.

These two symptoms were treated as separate problems, since a correct WHO_AM_I but noisy data ruled out the most obvious explanation (totally broken SPI wiring) and pointed toward something more subtle in the physical link.

## A.3 Root Cause of the XYZ Jitter — Wiring Noise

The jittering accelerometer data — present even with a correctly-read WHO_AM_I — was traced to electrical noise on the SPI lines between the two boards, rather than a register configuration issue. The fix was physical: replacing the long/jumper-style wiring between the controller and the breakout with short, directly soldered wires. This significantly improved signal integrity and stabilized the X/Y/Z readings.

*This is a useful distinction to flag for future bring-up work on this platform: a correct device ID does not guarantee clean data, and unstable readings on an otherwise-responding sensor are worth checking at the physical/wiring layer before assuming a firmware or register-level cause.*

## A.4 Temporary Fallback to I2C

With SPI still unreliable at the link level, the interface was temporarily switched to I2C to confirm that the sensor and the rest of the data path were functioning. Per the KX134 breakout's default configuration, the ADR jumper ships with the Center and Left pads connected, which pulls the ADR/SDO pin to 3.3 V and sets the device to I2C mode at address `0x1E`. I2C communication worked under this default configuration, confirming the sensor itself and the surrounding firmware path were sound. However, I2C throughput was confirmed to be too slow to support the vibration-sensing ODR requirement, so this was never intended as more than a diagnostic step.

## A.5 ADR Jumper Behavior (Per Manufacturer Datasheet)

The KX134 breakout's I2C and SPI interfaces share the same physical pins; the active mode is set entirely by the electrical state of the ADR/SDO pin, controlled by the 3-way ADR jumper (Center / Left / Right pads). Per the SmartElex/Kionix documentation:

| Jumper State | ADR/SDO Pin | Resulting Mode |
|---|---|---|
| Center–Left intact (default) | Pulled to 3.3 V | I2C, address `0x1E` |
| Center–Left severed, Center–Right bridged | Pulled to 0 V / GND | I2C, address `0x1F` |
| Center–Left severed, no bridge | Floating / No Connect | SPI (SDO wired to controller's SDI/CIPO) |

By this specification, switching from I2C back to SPI requires severing the Center–Left trace so the ADR/SDO pin floats. The Center–Left pad bridge is documented purely as an I2C address/mode select mechanism, with no stated role in SPI operation once the trace is cut.

## A.6 Resolution — Final Working Configuration

After the wiring noise fix (Section A.3) was applied and the interface was switched back from I2C to SPI, SPI communication came up correctly on the UNO Q — with the Center–Left ADR pad bridge still in place from the earlier I2C configuration, rather than severed as the datasheet specifies for SPI mode.

This is counterintuitive relative to the documented jumper behavior, but it is the configuration that worked reliably: the Center–Left pads bridged (the I2C/default state per the datasheet), used together with the SPI wiring and the short soldered connections from Section A.3. SPI on the UNO Q was confirmed functional in this state, while SPI on the XIAO ESP32S3 had already been working correctly with the pads cut as documented.

The practical takeaway carried forward into the build: for the KX134 on the UNO Q specifically, leave the Center–Left ADR pads bridged when running SPI. This is the configuration documented here as final and is not expected to change unless re-validated.

## A.7 Summary of Final Working State

- **Interface:** SPI (required for ODR/bandwidth; I2C confirmed insufficient).
- **Wiring:** short, directly soldered wires between controller and breakout (long/jumper wiring caused signal noise and XYZ jitter).
- **ADR jumper:** Center–Left pads bridged (the I2C-default state), retained intentionally rather than severed, for SPI operation on the UNO Q.
- **WHO_AM_I:** reads correctly as `0x46` under this configuration.
- **XYZ data:** stable at rest under this configuration.
- **Note:** the XIAO ESP32S3 satellite nodes use the datasheet-standard SPI configuration (Center–Left severed) and did not require this deviation.
