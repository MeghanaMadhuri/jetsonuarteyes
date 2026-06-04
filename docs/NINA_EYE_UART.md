# Nina eye display (ESP8266 + ST7735 over USB serial)

The NodeMCU firmware accepts expression IDs **0–36** on **USB serial @ 115200** (`Serial.parseInt()` + Enter). Nina sends `"{id}\n"` from the Jetson UI and Android companion.

## Jetson connection (validated — USB only)

**Do not use the 40-pin header UART** for the eyes on this robot. Use a **USB data cable**:

```
Jetson USB port  ────  USB cable  ────  NodeMCU micro-USB
```

Linux device: **`/dev/ttyUSB0`** (confirm with `dmesg` after plug-in).

```bash
NINA_EYE_UART_ENABLE=1
NINA_EYE_UART_PORT=/dev/ttyUSB0
NINA_EYE_UART_BAUD=115200
```

In `/etc/nina-link/navigation.env` (loaded automatically by Nina; no shell `export` needed).

Nina disables DTR/RTS on open so the ESP is not reset when the port is opened.

**Do not** share the same `/dev/ttyUSB*` port as Dynamixel (`NINA_DXL_PORT`). Do not connect the ESP to a PC USB port while the Jetson owns the cable.

Full procedure: [ESP8266_EYE_DEPLOYMENT_GUIDE.md](ESP8266_EYE_DEPLOYMENT_GUIDE.md).

<!--
## NOT USED — 40-pin header UART1 (ttyTHS1)

Per NVIDIA Orin Nano DevKit, UART1 on J12 pins 8/10 was an alternate path.
This robot uses USB only; the header path was not validated.

| J12 pin | Signal | Wire to ESP NodeMCU |
|--------|--------|---------------------|
| 8 | UART1_TXD | RX |
| 10 | UART1_RXD | TX |
| 6 | GND | GND |

Device: /dev/ttyTHS1 — requires jetson-io uart1 ON.
-->

## UI

- **Sirena kiosk:** sidebar **Eyes** → grid of all expressions; tap to send.
- **Android companion:** sidebar **Eyes** → same grid over HTTP.

## HTTP (companion)

- `GET /v1/robot/eye/expressions` — list
- `GET /v1/robot/eye/status` — serial port status
- `POST /v1/robot/eye/expression` — body `{"id": 5}` (requires bearer on LAN)

## Per-action binding (record / play)

In `nina/actions/manifest.json`:

```json
"namaste": {
  "file": "recordings/namaste.json",
  "eye_expression": 15,
  "eye_offset": 0.5
}
```

- **Kiosk:** Actions → **Eyes** sub-tab
- **CLI:** `python -m nina.app.eye_cli bind namaste 15 --offset 0.5`

## ESP firmware

Flash **[firmware/nina_eye_esp8266/nina_eye_esp8266.ino](../firmware/nina_eye_esp8266/nina_eye_esp8266.ino)** (see [firmware/nina_eye_esp8266/README.md](../firmware/nina_eye_esp8266/README.md)).

## Bench test

Arduino IDE Serial Monitor: **115200**, send `15` + Enter for love.

On Jetson:

```bash
python3 -m nina.app.eye_cli send 15
```
