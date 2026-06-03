# Nina eye expressions (ESP8266 + ST7735 over UART)

The NodeMCU firmware accepts expression IDs **0–33** on **Serial @ 115200** (`Serial.parseInt()` + Enter). Nina sends `"{id}\n"` from the Jetson UI and Android companion.

## Wiring (3.3 V logic)

| Jetson | NodeMCU (ESP-12E) |
|--------|-------------------|
| **TX** | **RX** |
| **RX** | **TX** |
| **GND** | **GND** |

Power the NodeMCU from **3.3 V** (not 5 V on Jetson GPIO). USB programming uses the on-board USB port; runtime UART to the Jetson uses the **TX/RX** pins labeled on the board.

## Jetson UART (not the 40-pin I²C header)

Wire the Jetson **dedicated UART TX/RX** to the NodeMCU (cross TX↔RX, common GND, 3.3 V). Do **not** use header pins 3/5 (I²C) for the eyes.

| Connection | Typical device |
|------------|----------------|
| Module UART (TXD/RXD) | `/dev/ttyTHS0` or `/dev/ttyTHS1` |
| USB–serial adapter | `/dev/ttyUSB0` or `/dev/ttyUSB1` |

**Do not** share the same `/dev/ttyUSB*` port as Dynamixel (`NINA_DXL_PORT`).

## Configuration

In `/etc/nina-link/navigation.env`:

```bash
NINA_EYE_UART_ENABLE=1
NINA_EYE_UART_PORT=/dev/ttyTHS1
NINA_EYE_UART_BAUD=115200
```

## UI

- **Sirena kiosk:** sidebar **Eyes** → grid of all expressions; tap to send.
- **Android companion:** sidebar **Eyes** → same grid over HTTP.

## HTTP (companion)

- `GET /v1/robot/eye/expressions` — list
- `GET /v1/robot/eye/status` — UART status
- `POST /v1/robot/eye/expression` — body `{"id": 5}` (requires bearer on LAN)

## Per-action binding (record / play)

In `nina/actions/manifest.json`, same pattern as `audio_offset`:

```json
"namaste": {
  "file": "recordings/namaste.json",
  "eye_expression": 15,
  "eye_offset": 0.5
}
```

- **Kiosk:** Actions → **Eyes** sub-tab
- **CLI:** `python -m nina.app.eye_cli bind namaste 15 --offset 0.5`
- **HTTP:** `GET/POST /v1/actions/eye/info`, `/bind`, `/offset`, `/clear`
- **Android:** Actions → **Eyes** sub-tab

## ESP firmware

Flash **[firmware/nina_eye_esp8266/nina_eye_esp8266.ino](../firmware/nina_eye_esp8266/nina_eye_esp8266.ino)** in Arduino IDE (see [firmware/nina_eye_esp8266/README.md](../firmware/nina_eye_esp8266/README.md) for TFT_eSPI setup).

## Bench test (USB serial to PC)

Arduino IDE Serial Monitor: **115200**, send `5` + Enter for angry.

On Jetson:

```bash
python3 -c "
import serial, time
s=serial.Serial('/dev/ttyTHS1', 115200, timeout=1)
s.write(b'1\n'); s.flush()
time.sleep(0.1)
s.close()
"
```
