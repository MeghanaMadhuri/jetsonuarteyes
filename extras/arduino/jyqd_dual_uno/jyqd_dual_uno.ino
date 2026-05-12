/*
 * Dual BLDC hub motors (JYQD_V7.3E2) + Arduino Uno
 *
 * No pull-up required in software; optional ~4.7k hardware tweak only if optos
 * need stronger HIGH — fix marginal LOW at the screw with wiring/buffer first.
 *
 * Z/F (see JYQD_LEGACY_ZF_POLARITY below):
 *   LEGACY=1 (default): forward L LOW R HIGH; backward L HIGH R LOW;
 *                       pivot L: both HIGH (L back, R fwd); pivot R: both LOW.
 *   LEGACY=0 (Nina):    forward L HIGH R LOW; backward L LOW R HIGH; pivot both LOW / both HIGH.
 *
 * If forward is reliable on one side only and backward swaps which side is good,
 * the failing net is often **not reaching a solid LOW** at the JYQD. This sketch
 * **parks** (EL off, VR=0), re-asserts pinMode(OUTPUT), writes **HIGH Z/F before LOW**
 * when levels are complementary, then long Z/F settle before EL+PWM.
 *
 * SERIAL 115200 / Newline. F B L R -> speed 0-255 -> S stop. +/-
 *
 * Stiction: opposite blip + **EL edge kick** (`navigation_bldc.kick_and_set`). Tune
 * kJyqd*, kKick*; disable edge kick with JYQD_USE_EL_EDGE_KICK 0.
 *
 * WIRING: L: GND EL D8 ZF D4 VR D10 | R: GND EL D9 ZF D5 VR D11 | 24 V drivers | common GND.
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

/* 1 = complementary L/R table (Uno bench default). 0 = Nina Jetson mirror. */
#ifndef JYQD_LEGACY_ZF_POLARITY
#define JYQD_LEGACY_ZF_POLARITY 1
#endif

/** 0 = disabled. Else ms: brief opposite ZF + PWM blip after park (Nina-style preload). */
#ifndef JYQD_OPPOSITE_BLIP_MS
#define JYQD_OPPOSITE_BLIP_MS 280U
#endif
#ifndef JYQD_OPPOSITE_BLIP_PWM
#define JYQD_OPPOSITE_BLIP_PWM 95U
#endif
/** ms pause after blip before commanding target direction. */
#ifndef JYQD_POST_BLIP_SETTLE_MS
#define JYQD_POST_BLIP_SETTLE_MS 100U
#endif

/** ms between complementary Z/F half-steps (HIGH net, then LOW net). */
static const uint8_t kZfStaggerMs = 12U;
/** Full park: EL off, VR 0, before every start (lets JYQD re-sample DIR). */
static const uint8_t kParkMs = 48U;
/** After complementary Z/F writes, let optos settle before EL. */
static const uint8_t kZfSettleMs = 48U;
/** Second Z/F rewrite hold (re-latch weak lines). */
static const uint8_t kZfReassertMs = 24U;
/** After Z/F valid, wait before asserting EL. */
static const uint8_t kElAfterZfMs = 32U;
/** VR stays 0 with EL on (JYQD path like Nina). */
static const uint8_t kVrZeroMs = 14U;
/** Breakaway pulse floor (0-255); increase if hubs still need a shove from rest. */
static const uint8_t kKickMinPwm = 175U;
/** Hold breakaway before dropping to commanded duty. */
static const uint16_t kKickHoldMs = 1150U;
/** After final PWM, pause then repeat analogWrite (clears stuck low duty). */
static const uint8_t kPwmReassertGapMs = 95U;
/** ms after VR=0 in stop before parking Z/F. */
static const uint8_t kStopVrZeroMs = 22U;

/**
 * Pi `navigation_bldc.kick_and_set` EL/PWM edge sequence. JYQD_V7.3E2 often
 * will not commutate from rest without: warm PWM while EL high, EL falling with
 * PWM->0, EL rising with 0->N on PWM. Set JYQD_USE_EL_EDGE_KICK 0 to compare.
 */
#ifndef JYQD_USE_EL_EDGE_KICK
#define JYQD_USE_EL_EDGE_KICK 1
#endif
/** Step-1 "warm" duty (~Pi KICK_PWM_PERCENT 15%); edge prep, not cruise torque. */
static const uint8_t kJyqdWarmPwm = 48U;
/** ms each for warm dwell and EL-low dwell (Pi ~100ms). */
static const uint16_t kJyqdEdgeDwellMs = 140U;
/** After EL rising, before first power PWM. */
static const uint8_t kJyqdElRiseSettleMs = 14U;

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

static void refreshMotorPins() {
  pinMode(PIN_LEFT_EL, OUTPUT);
  pinMode(PIN_LEFT_ZF, OUTPUT);
  pinMode(PIN_LEFT_VR, OUTPUT);
  pinMode(PIN_RIGHT_EL, OUTPUT);
  pinMode(PIN_RIGHT_ZF, OUTPUT);
  pinMode(PIN_RIGHT_VR, OUTPUT);
}

/** EL low, VR 0 both — call before changing Z/F from rest. */
static void parkDriversFull() {
  digitalWrite(PIN_LEFT_EL, LOW);
  digitalWrite(PIN_RIGHT_EL, LOW);
  analogWrite(PIN_LEFT_VR, 0);
  analogWrite(PIN_RIGHT_VR, 0);
  delay(kParkMs);
}

/**
 * When Z/F are complementary, write the HIGH side first so the LOW net can
 * settle with a solid sink (helps flaky "must be LOW" channels on forward L / back R).
 */
static void writeZfHardened(bool leftZfHigh, bool rightZfHigh) {
  if (leftZfHigh != rightZfHigh) {
    if (leftZfHigh) {
      digitalWrite(PIN_LEFT_ZF, HIGH);
      delay(kZfStaggerMs);
      digitalWrite(PIN_RIGHT_ZF, LOW);
    } else {
      digitalWrite(PIN_RIGHT_ZF, HIGH);
      delay(kZfStaggerMs);
      digitalWrite(PIN_LEFT_ZF, LOW);
    }
  } else {
    digitalWrite(PIN_LEFT_ZF, leftZfHigh ? HIGH : LOW);
    digitalWrite(PIN_RIGHT_ZF, rightZfHigh ? HIGH : LOW);
  }
  delay(kZfSettleMs);
  digitalWrite(PIN_LEFT_ZF, leftZfHigh ? HIGH : LOW);
  digitalWrite(PIN_RIGHT_ZF, rightZfHigh ? HIGH : LOW);
  delay(kZfReassertMs);
}

/**
 * JYQD reliable start: warm PWM @ EL high -> EL low + PWM 0 -> EL high +
 * PWM 0->kick->cruise (see navigation_bldc.kick_and_set).
 */
static void jyqdElEdgeKickThenCruise(uint8_t finalSpd) {
  const uint8_t cruiseKick = finalSpd < kKickMinPwm ? kKickMinPwm : finalSpd;
  digitalWrite(PIN_LEFT_EL, HIGH);
  digitalWrite(PIN_RIGHT_EL, HIGH);
  analogWrite(PIN_LEFT_VR, 0);
  analogWrite(PIN_RIGHT_VR, 0);
  delay(kVrZeroMs);
  analogWrite(PIN_LEFT_VR, kJyqdWarmPwm);
  analogWrite(PIN_RIGHT_VR, kJyqdWarmPwm);
  delay(kJyqdEdgeDwellMs);
  analogWrite(PIN_LEFT_VR, 0);
  analogWrite(PIN_RIGHT_VR, 0);
  digitalWrite(PIN_LEFT_EL, LOW);
  digitalWrite(PIN_RIGHT_EL, LOW);
  delay(kJyqdEdgeDwellMs);
  digitalWrite(PIN_LEFT_EL, HIGH);
  digitalWrite(PIN_RIGHT_EL, HIGH);
  delay(kJyqdElRiseSettleMs);
  analogWrite(PIN_LEFT_VR, cruiseKick);
  analogWrite(PIN_RIGHT_VR, cruiseKick);
  delay(kKickHoldMs);
  analogWrite(PIN_LEFT_VR, finalSpd);
  analogWrite(PIN_RIGHT_VR, finalSpd);
  delay(kPwmReassertGapMs);
  analogWrite(PIN_LEFT_VR, finalSpd);
  analogWrite(PIN_RIGHT_VR, finalSpd);
  delay(kPwmReassertGapMs);
  analogWrite(PIN_LEFT_VR, finalSpd);
  analogWrite(PIN_RIGHT_VR, finalSpd);
}

/** Z/F hardened, optional opposite blip, then EL-edge kick + cruise. */
static void enableBothMotorsWithKick(bool leftZfHigh, bool rightZfHigh, uint8_t spd) {
  refreshMotorPins();
  parkDriversFull();
#if JYQD_OPPOSITE_BLIP_MS > 0
  {
    const bool oL = !leftZfHigh;
    const bool oR = !rightZfHigh;
    writeZfHardened(oL, oR);
    delay(kElAfterZfMs);
    digitalWrite(PIN_LEFT_EL, HIGH);
    digitalWrite(PIN_RIGHT_EL, HIGH);
    analogWrite(PIN_LEFT_VR, 0);
    analogWrite(PIN_RIGHT_VR, 0);
    delay(kVrZeroMs);
    const uint8_t bpwm = static_cast<uint8_t>(
        (JYQD_OPPOSITE_BLIP_PWM > 255) ? 255 : JYQD_OPPOSITE_BLIP_PWM);
    analogWrite(PIN_LEFT_VR, bpwm);
    analogWrite(PIN_RIGHT_VR, bpwm);
    delay(JYQD_OPPOSITE_BLIP_MS);
    analogWrite(PIN_LEFT_VR, 0);
    analogWrite(PIN_RIGHT_VR, 0);
    digitalWrite(PIN_LEFT_EL, LOW);
    digitalWrite(PIN_RIGHT_EL, LOW);
    delay(JYQD_POST_BLIP_SETTLE_MS);
  }
#endif
  writeZfHardened(leftZfHigh, rightZfHigh);
  delay(kElAfterZfMs);
#if JYQD_USE_EL_EDGE_KICK
  jyqdElEdgeKickThenCruise(spd);
#else
  digitalWrite(PIN_LEFT_EL, HIGH);
  digitalWrite(PIN_RIGHT_EL, HIGH);
  analogWrite(PIN_LEFT_VR, 0);
  analogWrite(PIN_RIGHT_VR, 0);
  delay(kVrZeroMs);
  const uint8_t kick = spd < kKickMinPwm ? kKickMinPwm : spd;
  analogWrite(PIN_LEFT_VR, kick);
  analogWrite(PIN_RIGHT_VR, kick);
  delay(kKickHoldMs);
  analogWrite(PIN_LEFT_VR, spd);
  analogWrite(PIN_RIGHT_VR, spd);
  delay(kPwmReassertGapMs);
  analogWrite(PIN_LEFT_VR, spd);
  analogWrite(PIN_RIGHT_VR, spd);
  delay(kPwmReassertGapMs);
  analogWrite(PIN_LEFT_VR, spd);
  analogWrite(PIN_RIGHT_VR, spd);
#endif
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
  /* Pivot right: L forward (LOW), R backward (LOW) — not straight-back pair. */
  enableBothMotorsWithKick(false, false, g_speedPwm);
#else
  enableBothMotorsWithKick(true, true, g_speedPwm);
#endif
}

static void turnLeftApply() {
  logFmt2(F("RUN turnLeft  VR PWM = "), g_speedPwm);
#if JYQD_LEGACY_ZF_POLARITY
  /* Pivot left: L backward (HIGH), R forward (HIGH) — not straight-fwd pair. */
  enableBothMotorsWithKick(true, true, g_speedPwm);
#else
  enableBothMotorsWithKick(false, false, g_speedPwm);
#endif
}

static void stopMotors() {
  refreshMotorPins();
  digitalWrite(PIN_LEFT_EL, LOW);
  digitalWrite(PIN_RIGHT_EL, LOW);
  analogWrite(PIN_LEFT_VR, 0);
  analogWrite(PIN_RIGHT_VR, 0);
  delay(kStopVrZeroMs);
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
  logLine(F("  Try speed >= 100 for tests; raise kKickMinPwm/kKickHoldMs if stiction."));
  logLine(F("  EL edge kick: JYQD_USE_EL_EDGE_KICK (Pi kick_and_set); kJyqdEdgeDwellMs."));
#if JYQD_LEGACY_ZF_POLARITY
  logLine(F("  Build: LEGACY Z/F (F=L low R high); set 0 for Nina table."));
#else
  logLine(F("  Build: Nina Z/F (F=L high R low); set LEGACY=1 for bench table."));
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
