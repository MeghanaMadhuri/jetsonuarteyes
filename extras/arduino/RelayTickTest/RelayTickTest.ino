/*
 * RelayTickTest — bench-test a 5 V Songle-style relay module from an Arduino.
 *
 * Use this when you suspect the Jetson’s ~3.3 V GPIO is marginal for the
 * module’s IN / opto input: Arduino (Uno/Nano) drives **5 V** HIGH on a
 * digital pin, which usually matches what these boards expect.
 *
 * WIRING (typical 3-pin relay module: GND, VCC, IN)
 *   Relay GND  -> Arduino GND
 *   Relay VCC  -> Arduino 5V   (one module is often ~70–100 mA; if the board
 *                               browns out or USB resets, use a separate 5 V
 *                               supply with common GND to Arduino)
 *   Relay IN   -> Arduino D8   (change RELAY_PIN below if you use another pin)
 *
 * Open this folder in Arduino IDE: File -> Open -> select RelayTickTest.ino
 * Board: your board (e.g. Arduino Uno), then Upload.
 *
 * You should hear the relay tick each second; onboard LED on D13 is unrelated
 * unless you wire IN to D13.
 */

const int RELAY_PIN = 8;

// Many modules are **active-LOW** (IN LOW = coil on). Set to true if you need
// LOW to energise and HIGH to release — try false first, swap if there’s no click.
const bool ACTIVE_LOW = false;

void setup() {
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, ACTIVE_LOW ? HIGH : LOW);  // start: relay relaxed
  Serial.begin(115200);
  Serial.println(F("RelayTickTest: toggling every 1 s. Watch relay / listen for click."));
}

void loop() {
  // Energise coil (or “on” side — depends on module; you’ll still hear a transition)
  digitalWrite(RELAY_PIN, ACTIVE_LOW ? LOW : HIGH);
  Serial.println(F("IN -> energise path"));
  delay(1000);

  digitalWrite(RELAY_PIN, ACTIVE_LOW ? HIGH : LOW);
  Serial.println(F("IN -> release path"));
  delay(1000);
}
