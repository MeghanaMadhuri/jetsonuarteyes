/*
 * Dual BLDC hub motors (JYQD_V7.3E2) + Arduino Uno
 *
 * This sketch does **not** require any pull-up resistor on Z/F — only the
 * Arduino pins drive the lines. A ~4.7k to V+ is an optional *hardware*
 * tweak for weak opto inputs; it is not assumed here.
 *
 * Default Z/F matches Nina / NavigationManager:
 *   forward    : left HIGH,  right LOW
 *   backward   : left LOW,   right HIGH
 *   turn left  : both LOW   (pivot)
 *   turn right : both HIGH  (pivot)
 *
 * If hubs do not spin with default polarity, set at compile time:
 *   #define JYQD_LEGACY_ZF_POLARITY 1
 * before the #ifndef block below — this uses the older bench table
 * (forward = left LOW, right HIGH). Motion also uses a short Z/F settle +
 * breakaway PWM kick (similar to Nina).
 *
 * SERIAL (115200, line ending Newline or CR+LF)
 *   F/B/L/R -> enter speed 0-255 -> runs until S.
 *   +/- nudge PWM; ? help.
 *
 * WIRING
 *   Left:  GND, EL D8, ZF D4, VR D10 PWM, Signal NC, 5V per manual
 *   Right: GND, EL D9, ZF D5, VR D11 PWM, Signal NC
 *   24 V to drivers only; common GND with Uno.
 */

#include <Arduino.h>
#include <stdlib.h>  // strtol (speed parsing)

#define PIN_LEFT_EL   8
#define PIN_LEFT_ZF   4
#define PIN_LEFT_VR   10

#define PIN_RIGHT_EL  9
#define PIN_RIGHT_ZF  5
#define PIN_RIGHT_VR  11

#define SERIAL_BAUD   115200

/* 1 = forward left LOW / right HIGH (older sketch / some harnesses). */
#ifndef JYQD_LEGACY_ZF_POLARITY
#define JYQD_LEGACY_ZF_POLARITY 0
#endif

/** ms: Z/F stable before EL + PWM (JYQD is level-sensitive; allow opto slew). */
static const uint8_t kZfSettleMs = 8;
/** If commanded PWM is lower, use at least this for a breakaway pulse (0-255). */
static const uint8_t kKickMinPwm = 48;
/** ms to hold breakaway duty before final speed. */
static const uint16_t kKickHoldMs = 280;

enum MotionMode : uint8_t {
  MODE_STOPPED = 0,
  MODE_FORWARD,
  MODE_BACKWARD,
  MODE_TURN_LEFT,
  MODE_TURN_RIGHT,
};

enum UiState : uint8_t {
  UI_IDLE = 0,
  UI_WAIT_SPEED,
};

enum PendingMove : uint8_t {
  PEND_NONE = 0,
  PEND_FORWARD,
  PEND_BACKWARD,
  PEND_TURN_LEFT,
  PEND_TURN_RIGHT,
};

static uint8_t g_speedPwm = 0;
static MotionMode g_runningMode = MODE_STOPPED;
static UiState g_ui = UI_IDLE;
static PendingMove g_pending = PEND_NONE;
static char g_speedBuf[8];
static uint8_t g_speedLen = 0;

static void logLine(const __FlashStringHelper *msg) {
  Serial.println(msg);
}

static void logFmt2(const __FlashStringHelper *a, int v) {
  Serial.print(F("[JYQD] "));
  Serial.print(a);
  Serial.println(v);
}

static void applyMotor(uint8_t pinEl, uint8_t pinZf, uint8_t pinVr,
                       bool enable, bool zfHigh, uint8_t speedPwm) {
  digitalWrite(pinZf, zfHigh ? HIGH : LOW);
  if (enable) {
    digitalWrite(pinEl, HIGH);
    analogWrite(pinVr, speedPwm);
  } else {
    digitalWrite(pinEl, LOW);
    analogWrite(pinVr, 0);
  }
}

/** Z/F first, settle, EL on, VR 0, then breakaway kick then commanded speed. */
static void enableBothMotorsWithKick(bool leftZfHigh, bool rightZfHigh, uint8_t spd) {
  digitalWrite(PIN_LEFT_ZF, leftZfHigh ? HIGH : LOW);
  digitalWrite(PIN_RIGHT_ZF, rightZfHigh ? HIGH : LOW);
  delay(kZfSettleMs);
  digitalWrite(PIN_LEFT_EL, HIGH);
  digitalWrite(PIN_RIGHT_EL, HIGH);
  analogWrite(PIN_LEFT_VR, 0);
  analogWrite(PIN_RIGHT_VR, 0);
  delay(2);
  const uint8_t kick = spd < kKickMinPwm ? kKickMinPwm : spd;
  analogWrite(PIN_LEFT_VR, kick);
  analogWrite(PIN_RIGHT_VR, kick);
  delay(kKickHoldMs);
  analogWrite(PIN_LEFT_VR, spd);
  analogWrite(PIN_RIGHT_VR, spd);
}

static void forwardApply() {
  logFmt2(F("RUN forward  VR PWM = "), g_speedPwm);
#if JYQD_LEGACY_ZF_POLARITY
  enableBothMotorsWithKick(false, true, g_speedPwm);
#else
  enableBothMotorsWithKick(true, false, g_speedPwm);
#endif
}

static void backwardApply() {
  logFmt2(F("RUN backward VR PWM = "), g_speedPwm);
#if JYQD_LEGACY_ZF_POLARITY
  enableBothMotorsWithKick(true, false, g_speedPwm);
#else
  enableBothMotorsWithKick(false, true, g_speedPwm);
#endif
}

static void turnRightApply() {
  logFmt2(F("RUN turnRight VR PWM = "), g_speedPwm);
#if JYQD_LEGACY_ZF_POLARITY
  enableBothMotorsWithKick(true, false, g_speedPwm);
#else
  enableBothMotorsWithKick(true, true, g_speedPwm);
#endif
}

static void turnLeftApply() {
  logFmt2(F("RUN turnLeft  VR PWM = "), g_speedPwm);
#if JYQD_LEGACY_ZF_POLARITY
  enableBothMotorsWithKick(false, true, g_speedPwm);
#else
  enableBothMotorsWithKick(false, false, g_speedPwm);
#endif
}

static void stopMotors() {
#if JYQD_LEGACY_ZF_POLARITY
  logLine(F("[JYQD] STOP (EL off; ZF park legacy FWD: L=L R=H)"));
  applyMotor(PIN_LEFT_EL, PIN_LEFT_ZF, PIN_LEFT_VR, false, false, 0);
  applyMotor(PIN_RIGHT_EL, PIN_RIGHT_ZF, PIN_RIGHT_VR, false, true, 0);
#else
  logLine(F("[JYQD] STOP (EL off; ZF park Nina FWD: L=H R=L)"));
  applyMotor(PIN_LEFT_EL, PIN_LEFT_ZF, PIN_LEFT_VR, false, true, 0);
  applyMotor(PIN_RIGHT_EL, PIN_RIGHT_ZF, PIN_RIGHT_VR, false, false, 0);
#endif
  g_runningMode = MODE_STOPPED;
}

/** Only VR duty changes (+/-); EL/ZF already correct for current mode. */
static void updateVrPwmOnly() {
  if (g_runningMode == MODE_STOPPED) {
    return;
  }
  analogWrite(PIN_LEFT_VR, g_speedPwm);
  analogWrite(PIN_RIGHT_VR, g_speedPwm);
}

static void clearSpeedPromptState() {
  g_ui = UI_IDLE;
  g_speedLen = 0;
  g_pending = PEND_NONE;
}

static void runPendingWithParsedSpeed() {
  g_speedBuf[g_speedLen] = '\0';
  long v = strtol(g_speedBuf, nullptr, 10);
  if (v < 0) {
    v = 0;
  }
  if (v > 255) {
    v = 255;
  }
  g_speedPwm = static_cast<uint8_t>(v);
  g_speedLen = 0;
  g_ui = UI_IDLE;

  if (g_speedPwm == 0) {
    logLine(F("[JYQD] Speed 0 -> not starting. Pick F/B/L/R again."));
    g_pending = PEND_NONE;
    return;
  }

  switch (g_pending) {
    case PEND_FORWARD:
      g_runningMode = MODE_FORWARD;
      forwardApply();
      break;
    case PEND_BACKWARD:
      g_runningMode = MODE_BACKWARD;
      backwardApply();
      break;
    case PEND_TURN_LEFT:
      g_runningMode = MODE_TURN_LEFT;
      turnLeftApply();
      break;
    case PEND_TURN_RIGHT:
      g_runningMode = MODE_TURN_RIGHT;
      turnRightApply();
      break;
    default:
      logLine(F("[JYQD] Internal: no pending move."));
      break;
  }
  g_pending = PEND_NONE;
}

static void promptForSpeed(PendingMove p, const __FlashStringHelper *name) {
  g_pending = p;
  g_ui = UI_WAIT_SPEED;
  g_speedLen = 0;
  Serial.print(F("[JYQD] Chosen: "));
  Serial.println(name);
  logLine(F("[JYQD] Enter speed 0-255 (VR PWM duty), then press Enter:"));
}

static void printHelp() {
  logLine(F("[JYQD] --- Help ---"));
  logLine(F("  F / B / L / R  then speed 0-255 + Enter"));
  logLine(F("  S = stop (only way to end continuous motion)"));
  logLine(F("  + / - = nudge PWM while running"));
  logLine(F("  ? = help. Use a line ending with Newline."));
#if JYQD_LEGACY_ZF_POLARITY
  logLine(F("  Build: JYQD_LEGACY_ZF_POLARITY=1 (try 0 if F/B seem swapped)."));
#else
  logLine(F("  Build: Nina Z/F; set JYQD_LEGACY_ZF_POLARITY=1 if hubs do not move."));
#endif
}

static void processCharWhileIdle(uint8_t c) {
  switch (c) {
    case 'F':
    case 'f':
      promptForSpeed(PEND_FORWARD, F("FORWARD"));
      break;
    case 'B':
    case 'b':
      promptForSpeed(PEND_BACKWARD, F("BACKWARD"));
      break;
    case 'L':
    case 'l':
      promptForSpeed(PEND_TURN_LEFT, F("TURN LEFT"));
      break;
    case 'R':
    case 'r':
      promptForSpeed(PEND_TURN_RIGHT, F("TURN RIGHT"));
      break;
    case 'S':
    case 's':
      stopMotors();
      clearSpeedPromptState();
      break;
    case '+':
      if (g_runningMode != MODE_STOPPED && g_speedPwm < 250) {
        g_speedPwm = static_cast<uint8_t>(g_speedPwm + 5);
        logFmt2(F("PWM -> "), g_speedPwm);
        updateVrPwmOnly();
      } else if (g_runningMode == MODE_STOPPED) {
        logLine(F("[JYQD] + ignored (stopped). Send F/B/L/R first."));
      }
      break;
    case '-':
      if (g_runningMode != MODE_STOPPED && g_speedPwm > 5) {
        g_speedPwm = static_cast<uint8_t>(g_speedPwm - 5);
        logFmt2(F("PWM -> "), g_speedPwm);
        updateVrPwmOnly();
      } else if (g_runningMode == MODE_STOPPED) {
        logLine(F("[JYQD] - ignored (stopped). Send F/B/L/R first."));
      }
      break;
    case '?':
      printHelp();
      break;
    default:
      if (c != '\r' && c != '\n' && c != ' ') {
        Serial.print(F("[JYQD] Unknown (idle): "));
        Serial.println(static_cast<char>(c));
      }
      break;
  }
}

static void processCharWhileWaitSpeed(uint8_t c) {
  if (c == 'S' || c == 's') {
    stopMotors();
    clearSpeedPromptState();
    logLine(F("[JYQD] Stopped; speed entry aborted."));
    return;
  }
  if (c == '\r') {
    return;
  }
  if (c == '\n') {
    if (g_speedLen == 0) {
      logLine(F("[JYQD] Empty speed. Enter 0-255 + Enter (or S to abort)."));
      return;
    }
    runPendingWithParsedSpeed();
    return;
  }
  if (c >= '0' && c <= '9') {
    if (g_speedLen < sizeof(g_speedBuf) - 1) {
      g_speedBuf[g_speedLen++] = static_cast<char>(c);
      return;
    }
    logLine(F("[JYQD] Too many digits; clearing. Re-type speed."));
    g_speedLen = 0;
    return;
  }
  if (c == '\b' || c == 127) {
    if (g_speedLen > 0) {
      g_speedLen--;
    }
    return;
  }
  if (c != ' ') {
    Serial.print(F("[JYQD] Invalid while entering speed: "));
    Serial.println(static_cast<char>(c));
  }
}

static void processIncomingByte(uint8_t c) {
  if (g_ui == UI_WAIT_SPEED) {
    processCharWhileWaitSpeed(c);
  } else {
    processCharWhileIdle(c);
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(300);

  pinMode(PIN_LEFT_EL, OUTPUT);
  pinMode(PIN_LEFT_ZF, OUTPUT);
  pinMode(PIN_LEFT_VR, OUTPUT);

  pinMode(PIN_RIGHT_EL, OUTPUT);
  pinMode(PIN_RIGHT_ZF, OUTPUT);
  pinMode(PIN_RIGHT_VR, OUTPUT);

  stopMotors();
  clearSpeedPromptState();

  logLine(F("[JYQD] Ready. Send ? for help."));
}

void loop() {
  while (Serial.available() > 0) {
    uint8_t c = static_cast<uint8_t>(Serial.read());
    processIncomingByte(c);
  }
}
