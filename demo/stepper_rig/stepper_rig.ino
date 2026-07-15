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
 * Negative RPM reverses direction. RPM is clamped to [0, RPM_MAX].
 * -----------------------------------------------------------------------------
 */

#include <AccelStepper.h>

// ---- CNC Shield V3 pin map (GRBL layout) -----------------------------------
constexpr uint8_t EN_PIN = 8;   // shared driver enable, ACTIVE LOW
constexpr uint8_t M1_STEP = 2;  // X STEP
constexpr uint8_t M1_DIR  = 5;  // X DIR
constexpr uint8_t M2_STEP = 3;  // Y STEP
constexpr uint8_t M2_DIR  = 6;  // Y DIR

// Optional potentiometer on A0 controls motor 1 when USE_POT == true.
// Leave false for pure serial/software control (recommended for demo).
constexpr bool  USE_POT  = false;
constexpr uint8_t POT_PIN = A0;

// ---- Motor / motion configuration ------------------------------------------
constexpr int   STEPS_PER_REV = 200;   // 1.8 deg motor = 200 full steps/rev
constexpr int   MICROSTEP     = 1;     // MUST match the MSx jumpers on the shield
constexpr float RPM_DEFAULT   = 90.0;  // startup speed for both motors
constexpr float RPM_MAX       = 300.0; // safety clamp

// Max pulse rate the Uno can reliably emit for two motors. AccelStepper on a
// 16 MHz Uno tops out around ~4000 steps/s per motor before it starves.
constexpr float MAX_SPS = 4000.0;

AccelStepper m1(AccelStepper::DRIVER, M1_STEP, M1_DIR);
AccelStepper m2(AccelStepper::DRIVER, M2_STEP, M2_DIR);

float rpm1 = 0.0;
float rpm2 = 0.0;
bool  enabled = false;

// RPM -> steps per second for the current microstep setting.
float rpmToSps(float rpm) {
  return rpm / 60.0 * STEPS_PER_REV * MICROSTEP;
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

void applyRpm(AccelStepper &m, float &store, float rpm) {
  store = clampRpm(rpm);
  m.setSpeed(rpmToSps(store));
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
  Serial.println(F("  negative rpm reverses; rpm clamped to +/-300"));
}

void setup() {
  Serial.begin(115200);

  pinMode(EN_PIN, OUTPUT);
  setEnabled(true);            // energize on boot

  m1.setMaxSpeed(MAX_SPS);
  m2.setMaxSpeed(MAX_SPS);
  applyRpm(m1, rpm1, RPM_DEFAULT);
  applyRpm(m2, rpm2, RPM_DEFAULT);

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
      applyRpm(m1, rpm1, arg);
      printStatus();
      break;
    case '2':
      applyRpm(m2, rpm2, arg);
      printStatus();
      break;
    case 'b':
    case 'B':
      applyRpm(m1, rpm1, arg);
      applyRpm(m2, rpm2, arg);
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
    applyRpm(m1, rpm1, rpm);
  }

  // --- Emit step pulses (must be called as often as possible) ------------
  m1.runSpeed();
  m2.runSpeed();
}
