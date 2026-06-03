/*
 * Nina / NodeMCU (ESP-12E) + 1.8" ST7735 - Nino neutral + expressions 0..33
 *
 * WIRING: VCC 3V3, GND, CS D8, RST D0, DC D4, MOSI D7, SCK D5, LED 3V3
 * TFT_eSPI: User_Setup_ST7735_Nina.h - rotation 1 = 160x128 landscape
 *
 * SERIAL 115200: type the expression NUMBER (0-33) then Enter.
 * On boot, Serial prints a full menu: ID, name, and GOOD / BAD mood hint.
 * Replies with "OK <id>" or "ERR" (disable via NINA_UART_ECHO 0 before build).
 */

#ifndef NINA_UART_ECHO
#define NINA_UART_ECHO 1
#endif

#include <TFT_eSPI.h>
#include <SPI.h>
#include <math.h>

TFT_eSPI tft = TFT_eSPI();
TFT_eSprite eye(&tft);

int SCREEN_W, SCREEN_H;
int cx, cy;
const int maxRadius = 40;

int currentExpression = 0;
int animStep = 0;

const int NINO_LOOK_DIST = 35;

const uint32_t NEU_HOLD_OPEN_MS = 1600;
const uint32_t NEU_SHUTTER_STEP_MS = 9;
const uint32_t NEU_WHITE_MS = 480;
const uint32_t NEU_DIAMETER_STEP_MS = 11;

#ifndef TFT_PINK
#define TFT_PINK 0xF81F
#endif

#define EXPR_LAST 33

bool isAnimated(int e);
void drawCurrentExpression();
void neutralNinoReset();
void updateNeutralNino();
void printExpressionMenu();

static float smoothstep(float t) {
  if (t <= 0.0f) return 0.0f;
  if (t >= 1.0f) return 1.0f;
  return t * t * (3.0f - 2.0f * t);
}

static float easeOutCubic(float t) {
  float u = 1.0f - t;
  return 1.0f - u * u * u;
}

void renderShutterClose(int shutterY, int lookX) {
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx + lookX, cy, maxRadius, maxRadius, TFT_BLACK);
  eye.fillRect(0, cy + maxRadius - shutterY, SCREEN_W, SCREEN_H, TFT_WHITE);
  eye.pushSprite(0, 0);
}

void renderDiameterOpen(int openDist, int lookX) {
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx + lookX, cy, maxRadius, maxRadius, TFT_BLACK);
  eye.fillRect(0, 0, SCREEN_W, cy - openDist, TFT_WHITE);
  eye.fillRect(0, cy + openDist, SCREEN_W, SCREEN_H, TFT_WHITE);
  eye.pushSprite(0, 0);
}

void ninoMagicMove(int currentX, int nextX) {
  renderDiameterOpen(maxRadius, currentX);
  delay(1400);

  for (int s = 0; s <= maxRadius * 2; s += 4) {
    renderShutterClose(s, currentX);
    delay(9);
  }

  eye.fillSprite(TFT_WHITE);
  eye.pushSprite(0, 0);
  delay(480);

  for (int d = 0; d <= maxRadius; d += 1) {
    float t = (float)d / (float)maxRadius;
    int dd = (int)(easeOutCubic(t) * maxRadius);
    renderDiameterOpen(dd, nextX);
    delay(10);
  }
}

enum NeutralPhase : uint8_t {
  NEU_HOLD_DIAMETER = 0,
  NEU_SHUTTER,
  NEU_TELEPORT_WHITE,
  NEU_DIAMETER_OPEN,
};

static NeutralPhase neuPhase = NEU_HOLD_DIAMETER;
static uint32_t neuPhaseMark = 0;
static int neuSeg = 0;
static int neuFromX = 0;
static int neuToX = 0;
static int neuShutterY = 0;
static int neuDiameterD = 0;

static void neuLoadSegment(int seg) {
  static const int8_t kFrom[4] = {0, NINO_LOOK_DIST, 0, -NINO_LOOK_DIST};
  static const int8_t kTo[4] = {NINO_LOOK_DIST, 0, -NINO_LOOK_DIST, 0};
  seg &= 3;
  neuFromX = kFrom[seg];
  neuToX = kTo[seg];
}

void neutralNinoReset() {
  neuSeg = 0;
  neuLoadSegment(0);
  neuPhase = NEU_HOLD_DIAMETER;
  neuPhaseMark = millis();
  neuShutterY = 0;
  neuDiameterD = 0;
}

void updateNeutralNino() {
  const uint32_t now = millis();

  switch (neuPhase) {
    case NEU_HOLD_DIAMETER:
      renderDiameterOpen(maxRadius, neuFromX);
      if (now - neuPhaseMark >= NEU_HOLD_OPEN_MS) {
        neuPhase = NEU_SHUTTER;
        neuShutterY = 0;
        neuPhaseMark = now;
      }
      break;

    case NEU_SHUTTER:
      if (now - neuPhaseMark >= NEU_SHUTTER_STEP_MS) {
        neuPhaseMark = now;
        renderShutterClose(neuShutterY, neuFromX);
        neuShutterY += 4;
        if (neuShutterY > maxRadius * 2) {
          eye.fillSprite(TFT_WHITE);
          eye.pushSprite(0, 0);
          neuPhase = NEU_TELEPORT_WHITE;
          neuPhaseMark = now;
        }
      }
      break;

    case NEU_TELEPORT_WHITE:
      if (now - neuPhaseMark >= NEU_WHITE_MS) {
        neuPhase = NEU_DIAMETER_OPEN;
        neuDiameterD = 0;
        neuPhaseMark = now;
      }
      break;

    case NEU_DIAMETER_OPEN:
      if (now - neuPhaseMark >= NEU_DIAMETER_STEP_MS) {
        neuPhaseMark = now;
        float t = min(1.0f, (float)neuDiameterD / (float)maxRadius);
        int dd = (int)(easeOutCubic(t) * maxRadius);
        renderDiameterOpen(dd, neuToX);
        neuDiameterD += 2;
        if (neuDiameterD > maxRadius) {
          neuSeg = (neuSeg + 1) & 3;
          neuLoadSegment(neuSeg);
          neuPhase = NEU_HOLD_DIAMETER;
          neuPhaseMark = now;
        }
      }
      break;
  }
}

int gazeXForExpression(int e) {
  switch (e) {
    case 16: return -18;
    case 17: return 18;
    case 23: return -14;
    case 30: return -6;
    case 31: return -5;
    default: return 0;
  }
}

int gazeYForExpression(int e) {
  switch (e) {
    case 13: return 14;
    case 14: return -14;
    case 30: return 4;
    case 31: return 6;
    default: return 0;
  }
}

void teleportVerticalGaze(int targetY) {
  eye.fillSprite(TFT_WHITE);
  eye.pushSprite(0, 0);
  delay(420);
  for (int d = 0; d <= maxRadius; d += 2) {
    float t = (float)d / (float)maxRadius;
    int dd = (int)(easeOutCubic(t) * maxRadius);
    eye.fillSprite(TFT_WHITE);
    eye.fillEllipse(cx, cy + targetY, maxRadius, maxRadius, TFT_BLACK);
    eye.fillRect(0, 0, SCREEN_W, cy - dd + targetY, TFT_WHITE);
    eye.fillRect(0, cy + dd + targetY, SCREEN_W, SCREEN_H, TFT_WHITE);
    eye.pushSprite(0, 0);
    delay(10);
  }
}

void smoothExpressionCrossfade() {
  for (int s = 0; s <= maxRadius + 10; s += 6) {
    renderShutterClose(min(s, maxRadius * 2), 0);
    delay(8);
  }
  eye.fillSprite(TFT_WHITE);
  eye.pushSprite(0, 0);
  delay(120);
}

void applyExpressionChange(int newE) {
  if (newE < 0 || newE > EXPR_LAST) return;
  int oldE = currentExpression;
  if (oldE == newE) return;

  int x0 = gazeXForExpression(oldE);
  int x1 = gazeXForExpression(newE);
  int y0 = gazeYForExpression(oldE);
  int y1 = gazeYForExpression(newE);

  if (x0 != x1) {
    ninoMagicMove(x0, x1);
  } else if (y0 != y1) {
    teleportVerticalGaze(y1);
  } else {
    smoothExpressionCrossfade();
  }

  currentExpression = newE;
  animStep = 0;
  if (newE == 0) neutralNinoReset();
  if (!isAnimated(currentExpression)) drawCurrentExpression();
}

void drawBaseEye(int r, int offsetX, int offsetY) {
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx + offsetX, cy + offsetY, r, r, TFT_BLACK);
  eye.pushSprite(0, 0);
}

void drawBaseEyeRxRy(int rx, int ry, int offsetX, int offsetY) {
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx + offsetX, cy + offsetY, rx, ry, TFT_BLACK);
  eye.pushSprite(0, 0);
}

bool isAnimated(int e) {
  return (e >= 0 && e <= EXPR_LAST);
}

/** ID 1 - bold thick smile curve (upward parabola, reads like a natural grin). */
void exprHappy() {
  eye.fillSprite(TFT_WHITE);
  float breath = sinf(millis() / 480.0f) * 0.12f + 1.0f;
  const int halfW = 38;
  int depth = (int)(11.0f * breath);
  const int thick = 6;
  for (int layer = 0; layer < thick; layer++) {
    int d = depth - layer;
    if (d < 3) d = 3;
    for (int i = -halfW; i <= halfW; i++) {
      float u = (float)i / (float)halfW;
      float q = 1.0f - u * u;
      if (q < 0) continue;
      int y = cy + 5 + (int)(d * q);
      eye.drawPixel(cx + i, y, TFT_BLACK);
      eye.drawPixel(cx + i + 1, y, TFT_BLACK);
    }
  }
  eye.pushSprite(0, 0);
}

void exprExcited() {
  float bob = sinf(millis() / 260.0f) * 2.5f;
  int by = (int)bob;
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx, cy + by, maxRadius + 5, maxRadius + 5, TFT_BLACK);
  eye.fillCircle(cx + 10, cy - 10 + by, 5, TFT_WHITE);
  eye.fillCircle(cx - 8, cy - 8 + by, 3, TFT_WHITE);
  eye.pushSprite(0, 0);
}

void exprSad() {
  float drift = sinf(millis() / 1600.0f) * 4.0f;
  drawBaseEyeRxRy(maxRadius, maxRadius - 13, (int)drift, 5);
}

void exprSleepy() {
  float bob = sinf(millis() / 900.0f) * 2.0f;
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx, cy + (int)bob, maxRadius, maxRadius, TFT_BLACK);
  int lid = (int)(18 + sinf(millis() / 500.0f) * 4);
  eye.fillRect(0, cy - 10 + (int)bob, SCREEN_W, lid, TFT_WHITE);
  eye.pushSprite(0, 0);
}

void exprAngry() {
  float shake = sinf(millis() / 80.0f) * 1.5f;
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx + (int)shake, cy, maxRadius, maxRadius, TFT_BLACK);
  eye.fillTriangle(cx - 40, cy - 38, cx + 40, cy - 38, cx, cy - 2, TFT_WHITE);
  eye.pushSprite(0, 0);
}

void exprShocked() {
  float pul = 0.92f + 0.08f * sinf(millis() / 400.0f);
  int r = (int)((maxRadius + 10) * pul);
  drawBaseEye(r, 0, 0);
}

/** ID 7 - right-side wink (covers right half of pupil). */
void exprWinkRight() {
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx, cy, maxRadius, maxRadius, TFT_BLACK);
  eye.fillRect(cx, cy - maxRadius - 2, maxRadius + 4, 2 * maxRadius + 6, TFT_WHITE);
  eye.pushSprite(0, 0);
}

/** ID 32 - left-side wink (covers left half). */
void exprWinkLeft() {
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx, cy, maxRadius, maxRadius, TFT_BLACK);
  eye.fillRect(cx - maxRadius - 2, cy - maxRadius - 2, maxRadius + 2, 2 * maxRadius + 6, TFT_WHITE);
  eye.pushSprite(0, 0);
}

/** ID 33 - full lid line blink rhythm. */
void exprWinkBoth() {
  static uint32_t t0 = 0;
  if (t0 == 0) t0 = millis();
  uint32_t n = millis();
  uint32_t c = (n - t0) % 900;
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx, cy, maxRadius, maxRadius, TFT_BLACK);
  if (c < 120 || (c > 300 && c < 420)) {
    eye.drawFastHLine(cx - maxRadius, cy, 2 * maxRadius + 4, TFT_WHITE);
    eye.drawFastHLine(cx - maxRadius, cy + 1, 2 * maxRadius + 4, TFT_WHITE);
  }
  eye.pushSprite(0, 0);
}

void exprFocus() {
  float t = millis() / 700.0f;
  int rx = maxRadius + 8 + (int)(sinf(t) * 2);
  int ry = maxRadius - 14 + (int)(cosf(t * 1.3f) * 2);
  drawBaseEyeRxRy(rx, ry, 0, 0);
}

/**
 * ID 9 - phone-style bold spinner: grey ring track + thick black arc (radial strokes).
 */
void exprLoading() {
  eye.fillSprite(TFT_WHITE);
  int ro = maxRadius - 2;
  int ri = ro - 13;
  float spin = animStep * 0.11f;
  eye.drawCircle(cx, cy, ro + 1, TFT_LIGHTGREY);
  const int n = 38;
  const float arcLen = 4.65f;
  for (int i = 0; i < n; i++) {
    float a = spin + (float)i * (arcLen / (float)n);
    int x0 = cx + (int)(ri * cosf(a));
    int y0 = cy + (int)(ri * sinf(a));
    int x1 = cx + (int)(ro * cosf(a));
    int y1 = cy + (int)(ro * sinf(a));
    eye.drawLine(x0, y0, x1, y1, TFT_BLACK);
    eye.drawLine(x0 + 1, y0, x1 + 1, y1, TFT_BLACK);
  }
  int nt = max(8, n / 4);
  for (int i = 0; i < nt; i++) {
    float a = spin + arcLen + (float)i * 0.09f;
    int x0 = cx + (int)((ri + 2) * cosf(a));
    int y0 = cy + (int)((ri + 2) * sinf(a));
    int x1 = cx + (int)((ro - 2) * cosf(a));
    int y1 = cy + (int)((ro - 2) * sinf(a));
    eye.drawLine(x0, y0, x1, y1, TFT_DARKGREY);
  }
  eye.pushSprite(0, 0);
  animStep += 2;
}

void exprScan() {
  float wave = sinf(millis() / 400.0f) * 4.0f;
  int y = (animStep + (int)wave) % SCREEN_H;
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx, cy, maxRadius, maxRadius, TFT_BLACK);
  eye.drawFastHLine(0, y, SCREEN_W, TFT_WHITE);
  eye.pushSprite(0, 0);
  animStep += 3;
}

void exprPulse() {
  float t = millis() / 280.0f;
  int r = maxRadius + (int)(6.0f * sinf(t));
  r = max(10, min(r, maxRadius + 12));
  drawBaseEye(r, 0, 0);
}

void exprGlitch() {
  float t = millis() / 100.0f;
  int jx = (int)(sinf(t * 3.7f) * 4 + sinf(t * 11.0f) * 2);
  int jy = (int)(cosf(t * 2.9f) * 2);
  eye.fillSprite(TFT_WHITE);
  if (random(0, 4) == 0) {
    eye.drawRect(0, random(0, SCREEN_H - 8), SCREEN_W, 6, TFT_BLACK);
  }
  eye.fillEllipse(cx + jx, cy + jy, maxRadius, maxRadius, TFT_BLACK);
  eye.pushSprite(0, 0);
}

void exprLookDown() {
  float easeY = 12.0f + 3.0f * sinf(millis() / 600.0f);
  drawBaseEye(maxRadius, 0, (int)easeY);
}

void exprLookUp() {
  float easeY = -12.0f + 2.0f * sinf(millis() / 650.0f);
  drawBaseEye(maxRadius, 0, (int)easeY);
}

/** ID 15 - filled red heart (two lobes + point); size driven by exprLove heartbeat. */
static void drawHeartShape(int hx, int hy, int size, uint16_t col) {
  int hs = (size * 7) / 10;
  if (hs < 6) hs = 6;
  int r = hs / 2;
  eye.fillCircle(hx - hs / 2, hy - hs / 3, r, col);
  eye.fillCircle(hx + hs / 2, hy - hs / 3, r, col);
  eye.fillTriangle(hx - hs, hy - hs / 5, hx + hs, hy - hs / 5, hx, hy + hs, col);
}

/** ID 15 - red heart with “lub-dub” heartbeat (two quick scale bumps, then rest). */
void exprLove() {
  const uint32_t beatPeriodMs = 880;
  uint32_t t = millis() % beatPeriodMs;
  const int sBase = 46;
  float bumpPx = 0.f;
  if (t < 95) {
    float u = (float)t / 95.f;
    bumpPx = 13.0f * sinf(u * 3.14159265f);
  } else if (t >= 130 && t < 215) {
    float u = (float)(t - 130) / 85.f;
    bumpPx = 8.5f * sinf(u * 3.14159265f);
  }
  int s = sBase + (int)(bumpPx + 0.5f);
  if (s < sBase) s = sBase;
  int beatLift = (int)(bumpPx * 0.22f);
  eye.fillSprite(TFT_WHITE);
  drawHeartShape(cx, cy - 2 - beatLift, s, TFT_RED);
  int glintR = max(4, s / 9);
  eye.fillCircle(cx - s / 5, cy - s / 4 - beatLift, glintR, TFT_WHITE);
  eye.pushSprite(0, 0);
}

void exprCurious() {
  int rx = maxRadius - 2;
  int ry = maxRadius + 4;
  eye.fillSprite(TFT_WHITE);
  int ox = (int)(sinf(millis() / 800.0f) * 4);
  eye.fillEllipse(cx + ox, cy, rx, ry, TFT_BLACK);
  eye.fillCircle(cx + ox - 10, cy - 14, 3, TFT_WHITE);
  eye.pushSprite(0, 0);
}

void exprConfused() {
  float spin = millis() / 700.0f;
  int ox = (int)(sinf(spin) * 6);
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx + ox, cy, 18, 16, TFT_BLACK);
  eye.drawChar(cx - 4, 14, '?', TFT_BLACK, TFT_WHITE, 2);
  eye.pushSprite(0, 0);
}

void exprThinking() {
  float gx = sinf(millis() / 900.0f) * 5.0f;
  float gy = -5.0f + sinf(millis() / 700.0f) * 3.0f;
  drawBaseEye(maxRadius - 2, (int)gx, (int)gy);
}

void exprLookLeft() {
  float w = sinf(millis() / 520.0f) * 2.5f;
  drawBaseEye(maxRadius, -16 + (int)w, 0);
}

void exprLookRight() {
  float w = sinf(millis() / 520.0f) * 2.5f;
  drawBaseEye(maxRadius, 16 + (int)w, 0);
}

void exprDoubleBlink() {
  uint32_t n = millis();
  uint32_t ph = n % 1600;
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx, cy, maxRadius, maxRadius, TFT_BLACK);
  if ((ph > 180 && ph < 300) || (ph > 520 && ph < 640)) {
    eye.drawFastHLine(cx - maxRadius, cy, 2 * maxRadius + 4, TFT_WHITE);
    eye.drawFastHLine(cx - maxRadius, cy + 1, 2 * maxRadius + 4, TFT_WHITE);
  }
  eye.pushSprite(0, 0);
}

void exprSquint() {
  float t = millis() / 380.0f;
  int ry = 7 + (int)(sinf(t) * 2);
  drawBaseEyeRxRy(maxRadius - 2, ry, 0, 3);
}

void exprSleepDeep() {
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx, cy + 9, maxRadius, maxRadius - 10, TFT_BLACK);
  eye.fillRect(0, cy - 6, SCREEN_W, 26, TFT_WHITE);
  eye.pushSprite(0, 0);
}

void exprSideEye() {
  float t = sinf(millis() / 700.0f) * 2.0f;
  drawBaseEyeRxRy(maxRadius - 3, maxRadius - 8, -12 + (int)t, -3);
}

void exprListening() {
  float p = sinf(millis() / 450.0f) * 0.04f + 1.0f;
  int rx = (int)(maxRadius * p);
  drawBaseEyeRxRy(rx, maxRadius - 8, 0, -3);
}

void exprAcknowledging() {
  float nod = sinf(millis() / 260.0f) * 4.0f;
  drawBaseEye(maxRadius - 1, 0, (int)nod);
}

void exprUnsure() {
  float ox = sinf(millis() / 420.0f) * 6.0f;
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx + (int)ox, cy, 17, 15, TFT_BLACK);
  eye.drawChar(cx - 3, 12, '?', TFT_BLACK, TFT_WHITE, 1);
  eye.pushSprite(0, 0);
}

void exprRelief() {
  float u = smoothstep((millis() % 4000) / 4000.0f);
  int ry = (int)(10 + easeOutCubic(u) * (maxRadius - 10));
  drawBaseEyeRxRy(maxRadius, ry, 0, -3);
}

void exprTracking() {
  float t = millis() / 360.0f;
  int ox = (int)(sinf(t) * 20);
  int oy = (int)(cosf(t * 0.65f) * 7);
  drawBaseEye(maxRadius - 3, ox, oy);
}

void exprShy() {
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx - 5, cy + 6, maxRadius - 7, maxRadius - 9, TFT_BLACK);
  eye.fillCircle(cx + 26, cy + 14, 4, TFT_PINK);
  eye.pushSprite(0, 0);
}

void exprConcerned() {
  eye.fillSprite(TFT_WHITE);
  eye.fillEllipse(cx - 3, cy + 5, maxRadius - 2, maxRadius - 7, TFT_BLACK);
  eye.drawLine(cx - 22, cy - 26, cx - 7, cy - 12, TFT_BLACK);
  eye.drawLine(cx - 22, cy - 25, cx - 7, cy - 11, TFT_BLACK);
  eye.pushSprite(0, 0);
}

void drawCurrentExpression() {
  switch (currentExpression) {
    case 0: updateNeutralNino(); break;
    case 1: exprHappy(); break;
    case 2: exprExcited(); break;
    case 3: exprSad(); break;
    case 4: exprSleepy(); break;
    case 5: exprAngry(); break;
    case 6: exprShocked(); break;
    case 7: exprWinkRight(); break;
    case 8: exprFocus(); break;
    case 9: exprLoading(); break;
    case 10: exprScan(); break;
    case 11: exprPulse(); break;
    case 12: exprGlitch(); break;
    case 13: exprLookDown(); break;
    case 14: exprLookUp(); break;
    case 15: exprLove(); break;
    case 16: exprLookLeft(); break;
    case 17: exprLookRight(); break;
    case 18: exprDoubleBlink(); break;
    case 19: exprSquint(); break;
    case 20: exprConfused(); break;
    case 21: exprSleepDeep(); break;
    case 22: exprCurious(); break;
    case 23: exprSideEye(); break;
    case 24: exprListening(); break;
    case 25: exprThinking(); break;
    case 26: exprAcknowledging(); break;
    case 27: exprUnsure(); break;
    case 28: exprRelief(); break;
    case 29: exprTracking(); break;
    case 30: exprShy(); break;
    case 31: exprConcerned(); break;
    case 32: exprWinkLeft(); break;
    case 33: exprWinkBoth(); break;
    default: drawBaseEye(maxRadius, 0, 0); break;
  }
}

void printExpressionMenu() {
  Serial.println();
  Serial.println(F("========== NINA EYES: type 0-33 + Enter =========="));
  Serial.println(F("MOOD KEY: [+] positive/neutral  [-] negative/stress  [~] look/utility"));
  Serial.println(F("----------------------------------------------------"));
  Serial.println(F(" 0 neutral~   1 happy+    2 excited+   3 sad-      4 sleepy~"));
  Serial.println(F(" 5 angry-    6 shocked~  7 wink_right~ 8 focus+  9 loading~"));
  Serial.println(F("10 scan~    11 pulse~    12 glitch~  13 look_down~ 14 look_up~"));
  Serial.println(F("15 love+    16 look_left~ 17 look_right~ 18 double_blink~"));
  Serial.println(F("19 squint-  20 confused~ 21 sleep-   22 curious+  23 side_eye~"));
  Serial.println(F("24 listening+ 25 thinking+ 26 acknowledging+ 27 unsure~"));
  Serial.println(F("28 relief+  29 tracking~ 30 shy+     31 concerned-"));
  Serial.println(F("32 wink_left~ 33 wink_both~"));
  Serial.println(F("----------------------------------------------------"));
  Serial.println(F("Same names as UI list: neutral,happy,...,wink_both"));
  Serial.println(F("===================================================="));
}

void setup() {
  Serial.begin(115200);
  delay(200);

  tft.init();
  tft.setRotation(1);

  SCREEN_W = tft.width();
  SCREEN_H = tft.height();
  cx = SCREEN_W / 2;
  cy = SCREEN_H / 2;

  tft.fillScreen(TFT_WHITE);
  eye.createSprite(SCREEN_W, SCREEN_H);
  randomSeed(analogRead(A0));

  currentExpression = 0;
  neutralNinoReset();
  drawCurrentExpression();

  printExpressionMenu();
}

void loop() {
  if (Serial.available()) {
    int v = Serial.parseInt();
    if (v >= 0 && v <= EXPR_LAST) {
      applyExpressionChange(v);
#if NINA_UART_ECHO
      Serial.print(F("OK "));
      Serial.println(v);
#endif
    }
#if NINA_UART_ECHO
    else {
      Serial.println(F("ERR"));
    }
#endif
    while (Serial.available()) (void)Serial.read();
  }

  if (isAnimated(currentExpression)) {
    drawCurrentExpression();
    if (currentExpression == 0)
      delay(4);
    else if (currentExpression == 9)
      delay(28);
    else if (currentExpression == 10)
      delay(22);
    else if (currentExpression == 12)
      delay(32);
    else if (currentExpression == 17)
      delay(24);
    else if (currentExpression == 20)
      delay(26);
    else if (currentExpression == 22)
      delay(18);
    else if (currentExpression == 18)
      delay(14);
    else if (currentExpression == 23 || currentExpression == 29)
      delay(18);
    else if (currentExpression == 28)
      delay(22);
    else if (currentExpression == 33)
      delay(12);
    else
      delay(16);
  }
}