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
 * Speed changes ramp in/out at RPM_ACCEL (accelerating AND decelerating)
 * rather than jumping instantly, so the motor doesn't stall trying to
 * start - or stop - at a high step rate. This is a hand-rolled ramp (see
 * curSps/targetSps below), not AccelStepper's own accel/moveTo machinery:
 * that machinery only decelerates when it thinks it's approaching a target
 * position, which never happens for continuous jogging, so lowering speed
 * (including down to 0) would otherwise clamp instantly instead of ramping.
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

// Ramp rate (RPM/s) used to accelerate/decelerate to the commanded speed,
// symmetric in both directions. Jumping straight to a high step rate stalls
// the motor (no torque headroom at that speed yet) - ramping climbs through
// the low-speed range first. Tune down if it still stalls; tune up for
// snappier demos.
constexpr float RPM_ACCEL = 50.0;

AccelStepper m1(AccelStepper::DRIVER, M1_STEP, M1_DIR);
AccelStepper m2(AccelStepper::DRIVER, M2_STEP, M2_DIR);

float rpm1 = 0.0;
float rpm2 = 0.0;
bool  enabled = false;

// Commanded (target) and actual (ramping) speed in steps/s, signed by
// direction. loop() nudges cur toward target by at most accelSps*dt per
// tick, so both speeding up and slowing down go through the ramp.
float targetSps1 = 0.0, targetSps2 = 0.0;
float curSps1    = 0.0, curSps2    = 0.0;
unsigned long lastRampUs = 0;

// RPM -> steps per second for the current microstep setting.
float rpmToSps(float rpm) {
  return rpm / 60.0 * STEPS_PER_REV * MICROSTEP;
}

// RPM/s -> steps/s^2 for the current microstep setting.
float rpmPerSecToSpsPerSec(float rpmPerSec) {
  return rpmPerSec / 60.0 * STEPS_PER_REV * MICROSTEP;
}

// Moves cur toward target by at most maxDelta, without overshooting.
float stepToward(float cur, float target, float maxDelta) {
  if (cur < target) return min(cur + maxDelta, target);
  if (cur > target) return max(cur - maxDelta, target);
  return cur;
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

// Sets the target speed; loop() ramps curSps toward it (see stepToward()).
void applyRpm(float &store, float &targetSps, float rpm) {
  store = clampRpm(rpm);
  targetSps = rpmToSps(store);
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

  // setSpeed() constrains to +/-maxSpeed internally (default 1 step/s), so
  // this must be raised before any real speed is ever set.
  m1.setMaxSpeed(MAX_SPS);
  m2.setMaxSpeed(MAX_SPS);

  applyRpm(rpm1, targetSps1, RPM_DEFAULT);
  applyRpm(rpm2, targetSps2, RPM_DEFAULT);
  lastRampUs = micros();

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
      applyRpm(rpm1, targetSps1, arg);
      printStatus();
      break;
    case '2':
      applyRpm(rpm2, targetSps2, arg);
      printStatus();
      break;
    case 'b':
    case 'B':
      applyRpm(rpm1, targetSps1, arg);
      applyRpm(rpm2, targetSps2, arg);
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
  if (USE_POT) {
    float rpm = analogRead(POT_PIN) / 1023.0 * RPM_MAX;
    applyRpm(rpm1, targetSps1, rpm);
  }

  // --- Ramp actual speed toward target, then emit step pulses (must be
  // called as often as possible) -------------------------------------------
  unsigned long now = micros();
  float dt = (now - lastRampUs) / 1000000.0;
  lastRampUs = now;
  float maxDelta = rpmPerSecToSpsPerSec(RPM_ACCEL) * dt;

  curSps1 = stepToward(curSps1, targetSps1, maxDelta);
  curSps2 = stepToward(curSps2, targetSps2, maxDelta);
  m1.setSpeed(curSps1);
  m2.setSpeed(curSps2);
  m1.runSpeed();
  m2.runSpeed();
}
