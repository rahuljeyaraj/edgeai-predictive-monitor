/*
 * stepper_rig.ino
 * -----------------------------------------------------------------------------
 * Two-stepper vibration source for the edgeai-predictive-monitor demo.
 *
 * Hardware:  Arduino Uno + CNC Shield V3 + 2x A4988/DRV8825 drivers (X and Y).
 * Motors are the "rotating machine under test"; the MPU vibration sensor
 * measures them. Speed (RPM) is controlled over the USB serial link so demo
 * runs are scripted, repeatable, and easy to label (baseline vs. fault).
 *
 * Requires the AccelStepper library (Arduino IDE > Library Manager).
 *
 * Serial protocol (115200 baud, newline-terminated commands):
 *   1 <rpm>     set motor 1 (X) speed in RPM,  e.g.  "1 120"
 *   2 <rpm>     set motor 2 (Y) speed in RPM,  e.g.  "2 90"
 *   b <rpm>     set BOTH motors,               e.g.  "b 90"
 *   e           enable drivers (energize)
 *   d           disable drivers (coast/quiet)
 *   s           print status
 *   h           print help
 *
 * Negative RPM reverses direction. RPM is clamped to +/-RPM_MAX, which is
 * derived from MAX_SPS and the microstep setting (~1200 RPM at full step).
 * Speed is driven through AccelStepper's own acceleration machinery: each
 * motor is aimed at a target position far away in the desired direction and
 * run() ramps up to the commanded speed at RPM_ACCEL, so it doesn't stall
 * trying to start at a high step rate. A commanded speed of 0 uses stop(),
 * which decelerates smoothly to a halt. Direction reversals ramp down
 * through zero and back up automatically (the target position flips sign).
 * -----------------------------------------------------------------------------
 */

#include <AccelStepper.h>

// ---- CNC Shield V3 pin map --------------------------------------------------
// Y_STEP/Y_DIR confirmed via continuity test on this board; differs from the
// standard Protoneer 3/6 mapping.
constexpr uint8_t EN_PIN = 8;   // shared driver enable, ACTIVE LOW (LOW = drivers ON)
constexpr uint8_t M1_STEP = 2;  // X STEP
constexpr uint8_t M1_DIR  = 5;  // X DIR
constexpr uint8_t M2_STEP = 4;  // Y STEP
constexpr uint8_t M2_DIR  = 7;  // Y DIR

// Optional potentiometer on A0 controls motor 1 when USE_POT == true.
// Leave false for pure serial/software control (recommended for demo).
constexpr bool  USE_POT  = false;
constexpr uint8_t POT_PIN = A0;

// ---- Motor / motion configuration ------------------------------------------
constexpr int   STEPS_PER_REV = 200;   // 1.8 deg motor = 200 full steps/rev
// MUST match the MS1/MS2/MS3 jumpers on the shield (A4988 driver table):
// none=1, MS1=2, MS2=4, MS1+MS2=8, all three (M0/M1/M2 jumpered)=16.
constexpr int   MICROSTEP     = 1;     // all jumpers removed -> full step

// Max pulse rate the Uno can reliably emit for two motors. AccelStepper on a
// 16 MHz Uno tops out around ~4000 steps/s per motor before it starves.
constexpr float MAX_SPS = 4000.0;

// True achievable RPM ceiling at the current microstep setting, given MAX_SPS.
constexpr float RPM_MAX     = MAX_SPS * 60.0 / (STEPS_PER_REV * MICROSTEP); // ~1200 RPM at full step
constexpr float RPM_DEFAULT = 90.0;    // startup speed for both motors

// Ramp rate (RPM/s) used to accelerate/decelerate to the commanded speed.
// Jumping straight to a high step rate stalls the motor (no torque headroom
// at that speed yet) - ramping climbs through the low-speed range first.
// Tune down if it still stalls; tune up for snappier demos.
constexpr float RPM_ACCEL = 150.0;

// A target far enough away that run() keeps accelerating/cruising and never
// arrives (2e9 steps @ 4000 steps/s is ~138 hours), i.e. continuous rotation.
constexpr long FAR_TARGET = 2000000000L;

// Floor for setMaxSpeed so it never hits 0 (which would divide-by-zero the
// step interval); the true stop is handled with stop().
constexpr float MIN_SPS = 1.0;

// The decel governor eases setMaxSpeed down on this cadence (see loop()).
constexpr unsigned long GOV_INTERVAL_US = 2000;  // 2 ms -> 500 Hz

AccelStepper m1(AccelStepper::DRIVER, M1_STEP, M1_DIR);
AccelStepper m2(AccelStepper::DRIVER, M2_STEP, M2_DIR);

float rpm1 = 0.0;
float rpm2 = 0.0;
bool  enabled = false;

// Cruise-speed governor state (magnitudes, steps/s). curMaxSps is what's
// currently handed to setMaxSpeed(); tgtMaxSps is where we want it. Speed
// INCREASES are applied to curMaxSps immediately (run() accelerates via
// setAcceleration), but DECREASES are eased down by the governor so the
// spinning rotor doesn't overrun an abrupt slowdown and stall.
float curMaxSps1 = 0.0, curMaxSps2 = 0.0;
float tgtMaxSps1 = 0.0, tgtMaxSps2 = 0.0;
unsigned long lastGovUs = 0;

// RPM -> steps per second for the current microstep setting.
float rpmToSps(float rpm) {
  return rpm / 60.0 * STEPS_PER_REV * MICROSTEP;
}

// RPM/s -> steps/s^2 for the current microstep setting.
float rpmPerSecToSpsPerSec(float rpmPerSec) {
  return rpmPerSec / 60.0 * STEPS_PER_REV * MICROSTEP;
}

float clampRpm(float rpm) {
  if (rpm >  RPM_MAX) return  RPM_MAX;
  if (rpm < -RPM_MAX) return -RPM_MAX;
  return rpm;
}

void setEnabled(bool on) {
  enabled = on;
  digitalWrite(EN_PIN, on ? LOW : HIGH);  // active LOW
}

// Commands motor m to the given RPM. Aims it at a far target in the desired
// direction so run() accelerates up to the cruise speed. Speed increases take
// effect at once; decreases are left for the governor in loop() to ease down.
void applyRpm(AccelStepper &m, float &store, float &curMaxSps, float &tgtMaxSps, float rpm) {
  store = clampRpm(rpm);
  float sps = rpmToSps(store);
  float mag = fabs(sps);
  tgtMaxSps = mag;

  // Keep the current direction when stopping (mag ~0); otherwise aim per sign.
  if (mag >= MIN_SPS)
    m.moveTo(sps > 0 ? FAR_TARGET : -FAR_TARGET);

  // Increase (or first command): apply now so run() accelerates immediately.
  if (mag >= curMaxSps) {
    curMaxSps = max(mag, MIN_SPS);
    m.setMaxSpeed(curMaxSps);
  }
}

// Governor step: eases curMaxSps down toward tgtMaxSps (decreases only) by at
// most maxDelta, updating setMaxSpeed so run() slews the speed down smoothly.
// Once it has eased down to a commanded stop, stop() kills the residual crawl.
void governDown(AccelStepper &m, float &curMaxSps, float tgtMaxSps, float maxDelta) {
  if (curMaxSps <= tgtMaxSps) return;             // not decreasing - nothing to do
  curMaxSps = max(tgtMaxSps, curMaxSps - maxDelta);
  m.setMaxSpeed(max(curMaxSps, MIN_SPS));
  if (tgtMaxSps < MIN_SPS && curMaxSps <= MIN_SPS)
    m.stop();                                     // fully stop once crawled down
}

void printStatus() {
  Serial.print(F("[status] drivers="));
  Serial.print(enabled ? F("ON ") : F("OFF"));
  Serial.print(F("  m1="));
  Serial.print(rpm1, 1);
  Serial.print(F(" RPM  m2="));
  Serial.print(rpm2, 1);
  Serial.print(F(" RPM  microstep=1/"));
  Serial.println(MICROSTEP);
}

void printHelp() {
  Serial.println(F("commands: '1 <rpm>' '2 <rpm>' 'b <rpm>' 'e' 'd' 's' 'h'"));
  Serial.print(F("  negative rpm reverses; rpm clamped to +/-"));
  Serial.println(RPM_MAX, 0);
}

void setup() {
  Serial.begin(115200);

  pinMode(EN_PIN, OUTPUT);
  setEnabled(true);            // energize on boot

  m1.setMaxSpeed(MAX_SPS);
  m2.setMaxSpeed(MAX_SPS);
  m1.setAcceleration(rpmPerSecToSpsPerSec(RPM_ACCEL));
  m2.setAcceleration(rpmPerSecToSpsPerSec(RPM_ACCEL));
  applyRpm(m1, rpm1, curMaxSps1, tgtMaxSps1, RPM_DEFAULT);
  applyRpm(m2, rpm2, curMaxSps2, tgtMaxSps2, RPM_DEFAULT);
  lastGovUs = micros();

  Serial.println(F("=== stepper_rig ready ==="));
  printHelp();
  printStatus();
}

// Parse one newline-terminated command line.
void handleCommand(const String &line) {
  String s = line;
  s.trim();
  if (s.length() == 0) return;

  char cmd = s.charAt(0);
  // remaining text after the command char (the argument, if any)
  float arg = s.substring(1).toFloat();

  switch (cmd) {
    case '1':
      applyRpm(m1, rpm1, curMaxSps1, tgtMaxSps1, arg);
      printStatus();
      break;
    case '2':
      applyRpm(m2, rpm2, curMaxSps2, tgtMaxSps2, arg);
      printStatus();
      break;
    case 'b':
    case 'B':
      applyRpm(m1, rpm1, curMaxSps1, tgtMaxSps1, arg);
      applyRpm(m2, rpm2, curMaxSps2, tgtMaxSps2, arg);
      printStatus();
      break;
    case 'e':
    case 'E':
      setEnabled(true);
      printStatus();
      break;
    case 'd':
    case 'D':
      setEnabled(false);
      printStatus();
      break;
    case 's':
    case 'S':
      printStatus();
      break;
    case 'h':
    case 'H':
    case '?':
      printHelp();
      break;
    default:
      Serial.print(F("[err] unknown command: "));
      Serial.println(s);
      printHelp();
      break;
  }
}

void loop() {
  // --- Non-blocking serial line reader -----------------------------------
  static String buf;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (buf.length() > 0) {
        handleCommand(buf);
        buf = "";
      }
    } else {
      buf += c;
      if (buf.length() > 32) buf = "";  // guard against runaway input
    }
  }

  // --- Optional pot overrides motor 1 speed ------------------------------
  // applyRpm() re-targets the motor, so only call it when the pot actually
  // moved - otherwise run()'s acceleration state is reset every loop.
  if (USE_POT) {
    float rpm = analogRead(POT_PIN) / 1023.0 * RPM_MAX;
    if (fabs(rpm - rpm1) > 1.0) applyRpm(m1, rpm1, curMaxSps1, tgtMaxSps1, rpm);
  }

  // --- Decel governor: ease setMaxSpeed down toward the target (decreases
  // only) on a fixed cadence so a high-speed slowdown doesn't stall ---------
  unsigned long now = micros();
  if (now - lastGovUs >= GOV_INTERVAL_US) {
    float dt = (now - lastGovUs) / 1000000.0;
    lastGovUs = now;
    float maxDelta = rpmPerSecToSpsPerSec(RPM_ACCEL) * dt;
    governDown(m1, curMaxSps1, tgtMaxSps1, maxDelta);
    governDown(m2, curMaxSps2, tgtMaxSps2, maxDelta);
  }

  // --- Let AccelStepper accelerate/decelerate toward the target and emit
  // step pulses (must be called as often as possible) ----------------------
  m1.run();
  m2.run();
}
