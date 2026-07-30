/*
 * main.cpp — motor-driver firmware
 * -----------------------------------------------------------------------------
 * Three-stepper "rotating machine under test" for the edgeai-predictive-monitor
 * demo. The Uno only knows "spin this motor at this RPM, right now" — it does
 * NOT ramp. Any acceleration/deceleration profile (so a motor doesn't stall
 * jumping straight to a high speed) is the host's job: dashboard.html or
 * run_demo.py send a fast stream of intermediate RPM commands. Keeping the
 * firmware this dumb means new ramp/speed behavior is a dashboard/script edit,
 * not a re-flash.
 *
 * Requires the AccelStepper library, used here only for step-pulse generation
 * (setSpeed()/runSpeed()), not its acceleration machinery.
 *
 * Serial protocol (115200 baud, newline-terminated commands):
 *   1 <rpm>     set motor 1 (X) speed in RPM,  e.g.  "1 120"
 *   2 <rpm>     set motor 2 (Y) speed in RPM,  e.g.  "2 90"
 *   3 <rpm>     set motor 3 (Z) speed in RPM,  e.g.  "3 60"
 *   b <rpm>     set ALL THREE motors,          e.g.  "b 90"
 *   e           enable drivers (energize ALL THREE — one shared line)
 *   d           disable drivers (coast ALL THREE)
 *   s           print status
 *   h           print help
 *
 * Speed commands (1/2/3/b) reply with nothing — see handleCommand() for why.
 * Only e/d/s/h print.
 *
 * There is no per-motor enable: the shield wires one ~ENABLE to all three
 * drivers. To idle a single motor, command it "N 0" and leave the others
 * running. Note this means `e` re-energizes every driver at whatever speed it
 * was last told, so a host that wants motors stopped must send "N 0" for each
 * one — not just `d`.
 *
 * Negative RPM reverses direction. RPM is clamped to +/-RPM_MAX (~1200 RPM at
 * full step). A commanded speed applies IMMEDIATELY — no on-device ramp — so
 * a large jump at high RPM can stall the motor; ramp from the host instead.
 * -----------------------------------------------------------------------------
 */

#include <AccelStepper.h>

// ---- CNC Shield V3 pin map --------------------------------------------------
// Standard Protoneer/GRBL mapping. Note there is exactly ONE ~ENABLE line
// shared by all three driver sockets — the hardware has no per-motor enable,
// so "stopping" one motor means commanding it to 0 RPM, and `d` cuts all three.
constexpr uint8_t EN_PIN = 8;   // shared driver enable, ACTIVE LOW (LOW = drivers ON)
constexpr uint8_t M1_STEP = 2;  // X STEP
constexpr uint8_t M1_DIR  = 5;  // X DIR
constexpr uint8_t M2_STEP = 3;  // Y STEP
constexpr uint8_t M2_DIR  = 6;  // Y DIR
constexpr uint8_t M3_STEP = 4;  // Z STEP
constexpr uint8_t M3_DIR  = 7;  // Z DIR

// ---- Motor / motion configuration ------------------------------------------
constexpr int STEPS_PER_REV = 200;   // 1.8 deg motor = 200 full steps/rev
// MUST match the MS1/MS2/MS3 jumpers on the shield (A4988 driver table):
// none=1, MS1=2, MS2=4, MS1+MS2=8, all three (M0/M1/M2 jumpered)=16.
constexpr int MICROSTEP = 1;         // all jumpers removed -> full step

// Max pulse rate the Uno can reliably emit per motor. Bench-verified at
// 4000 sps for 2 motors; not yet re-verified with a 3rd motor sharing the
// loop (runSpeed() is cheap per call, so this should hold — confirm on real
// hardware before trusting it at the very top of the range).
constexpr float MAX_SPS = 4000.0;

// True achievable RPM ceiling at the current microstep setting, given MAX_SPS.
constexpr float RPM_MAX     = MAX_SPS * 60.0 / (STEPS_PER_REV * MICROSTEP); // ~1200 RPM at full step
constexpr float RPM_DEFAULT = 0.0;   // startup speed for all motors — host ramps up from here

AccelStepper m1(AccelStepper::DRIVER, M1_STEP, M1_DIR);
AccelStepper m2(AccelStepper::DRIVER, M2_STEP, M2_DIR);
AccelStepper m3(AccelStepper::DRIVER, M3_STEP, M3_DIR);

float rpm1 = 0.0, rpm2 = 0.0, rpm3 = 0.0;
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

// Commands motor m to the given RPM immediately (no ramp) via setSpeed(),
// which runSpeed() in loop() turns into evenly-spaced step pulses.
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
  Serial.print(F(" RPM  m3="));
  Serial.print(rpm3, 1);
  Serial.print(F(" RPM  microstep=1/"));
  Serial.println(MICROSTEP);
}

void printHelp() {
  Serial.println(F("commands: '1 <rpm>' '2 <rpm>' '3 <rpm>' 'b <rpm>' 'e' 'd' 's' 'h'"));
  Serial.print(F("  negative rpm reverses; rpm clamped to +/-"));
  Serial.println(RPM_MAX, 0);
  Serial.println(F("  no on-device ramp: speed changes apply immediately"));
}

void setup() {
  Serial.begin(115200);

  pinMode(EN_PIN, OUTPUT);
  setEnabled(true);            // energize on boot

  m1.setMaxSpeed(MAX_SPS);
  m2.setMaxSpeed(MAX_SPS);
  m3.setMaxSpeed(MAX_SPS);
  applyRpm(m1, rpm1, RPM_DEFAULT);
  applyRpm(m2, rpm2, RPM_DEFAULT);
  applyRpm(m3, rpm3, RPM_DEFAULT);

  Serial.println(F("=== motor-driver ready ==="));
  printHelp();
  printStatus();
}

// Parse one newline-terminated command line.
void handleCommand(const String &line) {
  String s = line;
  s.trim();
  if (s.length() == 0) return;

  char cmd = s.charAt(0);
  float arg = s.substring(1).toFloat();

  switch (cmd) {
    // Speed commands answer with SILENCE, deliberately. A status line is ~72
    // chars, which overflows the 64-byte serial TX buffer and makes
    // Serial.print() block until it drains — and that stalls loop(), starving
    // runSpeed() for ALL THREE motors. During a host ramp that's ~30
    // speed commands/second, so echoing here made every motor stutter audibly
    // whenever any one motor changed speed. Use 's' to read state instead.
    case '1':
      applyRpm(m1, rpm1, arg);
      break;
    case '2':
      applyRpm(m2, rpm2, arg);
      break;
    case '3':
      applyRpm(m3, rpm3, arg);
      break;
    case 'b':
    case 'B':
      applyRpm(m1, rpm1, arg);
      applyRpm(m2, rpm2, arg);
      applyRpm(m3, rpm3, arg);
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

  // --- Emit step pulses at each motor's current commanded speed -----------
  // runSpeed() is fixed-speed (no acceleration); call it as often as
  // possible for smooth, evenly-spaced pulses.
  m1.runSpeed();
  m2.runSpeed();
  m3.runSpeed();
}
