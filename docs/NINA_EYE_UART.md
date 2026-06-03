# Nina eye expressions (ESP8266 + ST7735 over UART)

The NodeMCU firmware accepts expression IDs **0–33** on **Serial @ 115200** (`Serial.parseInt()` + Enter). Nina sends `"{id}\n"` from the Jetson UI and Android companion.

## Wiring (3.3 V logic)

| Jetson | NodeMCU (ESP-12E) |
|--------|-------------------|
| **TX** | **RX** |
| **RX** | **TX** |
| **GND** | **GND** |

Power the NodeMCU from **3.3 V** (not 5 V on Jetson GPIO). USB programming uses the on-board USB port; runtime UART to the Jetson uses the **TX/RX** pins labeled on the board.

## Jetson UART — use 40-pin header UART1 (recommended)

Per NVIDIA Orin Nano DevKit carrier spec (J12 **Table 3-3**), use **UART1** on the **40-pin expansion header**, not the button-header **UART2 (DEBUG)** TXD/RXD.

| J12 pin | Signal | Wire to ESP NodeMCU |
|--------|--------|---------------------|
| **8** | UART1_TXD | **RX** |
| **10** | UART1_RXD | **TX** |
| **6** | GND | **GND** |

Typical Linux device: **`/dev/ttyTHS1`** (set `NINA_EYE_UART_PORT` if your board maps UART1 elsewhere).

Do **not** use header pins **3/5** (I²C). Avoid button-header UART2 for the eyes (debug port, often `ttyTHS2`).

| Connection | Typical device |
|------------|----------------|
| 40-pin UART1 (pins 8, 10) | `/dev/ttyTHS1` |
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

On Jetson (firmware replies `OK <id>` on the same UART — read pin 10 / ESP TX):

```bash
python3 -c "
import serial, time
s=serial.Serial('/dev/ttyTHS1', 115200, timeout=1)
s.reset_input_buffer()
s.write(b'15\n'); s.flush()
time.sleep(0.2)
print(s.readline())   # b'OK 15\n' if ESP received (ack is immediate)
s.close()
"
```

Build with `NINA_UART_ECHO 0` in the sketch if you want no ack lines.
