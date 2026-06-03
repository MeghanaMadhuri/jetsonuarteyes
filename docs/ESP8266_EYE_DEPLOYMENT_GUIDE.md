# Nina ESP8266 Eye Display — Deployment Guide (Formal)

**Repository:** https://github.com/MeghanaMadhuri/jetsonuarteyes.git  
**Audience:** Field engineering / integration  
**Hardware:** NodeMCU 1.0 (ESP-12E) + 1.8" ST7735 TFT  
**Host:** NVIDIA Jetson Orin Nano (Ubuntu)

---

## Email template (copy and send)

**Subject:** Nina robot — ESP8266 eye display: firmware, TFT_eSPI setup, wiring, and Jetson USB serial configuration

Dear Team,

Please find below the end-to-end procedure to deploy the Nina face display (expressions 0–33) on the NodeMCU ESP8266 and control it from the Jetson Orin Nano. This document reflects the configuration validated on the robot: a **single USB data cable** from the Jetson to the NodeMCU micro-USB port (`/dev/ttyUSB0`), with no separate RX/TX jumper wires.

**Attachments (from the repository `firmware/nina_eye_esp8266/` folder):**

| # | File | Purpose |
|---|------|---------|
| 1 | `User_Setup_ST7735_Nina.h` | TFT_eSPI pin and driver configuration for the 1.8" ST7735 |
| 2 | `User_Setup_Select_Nina.patch.txt` | Instructions for editing `User_Setup_Select.h` |
| 3 | `nina_eye_esp8266.ino` | Production firmware (UART commands, expressions 0–33) |
| 4 | `README.md` | Flash and bench-test checklist |

Clone or download from:  
`https://github.com/MeghanaMadhuri/jetsonuarteyes.git` → path `firmware/nina_eye_esp8266/`

---

### 1. Bill of materials

| Item | Notes |
|------|--------|
| NodeMCU 1.0 (ESP-12E Module) | Or compatible ESP8266 dev board with USB serial |
| 1.8" ST7735 TFT (128×160) | SPI wiring per table in Section 3 |
| USB cable | Micro-USB (or board-appropriate) for programming |
| Jetson Orin Nano | Ubuntu, Nina software stack installed |
| USB data cable (Jetson USB ↔ ESP micro-USB) | **Validated production path** on the robot — no extra RX/TX jumper wires |
| Optional: CP210x USB–TTL dongle + 3 wires | Jetson USB → dongle → ESP **RX / TX / GND** (same `ttyUSB0` idea) |
| Optional: 40-pin UART wires | J12 pins 8, 10, 6 — `/dev/ttyTHS1`; requires `jetson-io` uart1 |

---

### 2. Software on the programming PC (Windows)

1. Install **Arduino IDE** (1.8.x or 2.x).
2. **Board package:** ESP8266 by ESP8266 Community (Boards Manager).
3. **Library:** **TFT_eSPI** by Bodmer (Library Manager).
4. Copy attachment **`User_Setup_ST7735_Nina.h`** to:
   ```
   Documents/Arduino/libraries/TFT_eSPI/User_Setups/User_Setup_ST7735_Nina.h
   ```
5. Edit **`Documents/Arduino/libraries/TFT_eSPI/User_Setup_Select.h`** per attachment **`User_Setup_Select_Nina.patch.txt`**:
   - Comment out `#include <User_Setup.h>`
   - Add only: `#include <User_Setups/User_Setup_ST7735_Nina.h>`
6. Open **`nina_eye_esp8266.ino`** from the repository.
7. **Tools → Board:** `NodeMCU 1.0 (ESP-12E Module)`  
8. **Tools → Upload Speed:** `115200`  
9. **Tools → Port:** COM port that appears when the board is plugged in.

**Before upload:** disconnect the TFT (CS on D8 blocks bootloader), disconnect any Jetson UART wires, close Serial Monitor.  
**If upload stalls at “Connecting…”:** hold **FLASH**, tap **RST**, release **FLASH**, then Upload.

**After upload:** reconnect TFT; open Serial Monitor at **115200**; type `15` + Enter → display shows **love** and serial prints **`OK 15`**.

---

### 3. TFT wiring (NodeMCU ↔ ST7735)

| TFT pin | NodeMCU pin | GPIO |
|---------|-------------|------|
| VCC | 3.3 V | — |
| GND | GND | — |
| CS | D8 | GPIO15 |
| RST | D0 | GPIO16 |
| DC | D4 | GPIO2 |
| MOSI | D7 | GPIO13 |
| SCK | D5 | GPIO14 |
| LED | 3.3 V (or as per module) | — |

Do **not** use 5 V on the TFT from the Jetson header.

---

### 4. Jetson connection (validated: direct USB cable)

On the robot, the bench-tested setup is a **single USB cable** from the **Jetson USB host port** to the **NodeMCU micro-USB port**. Power and serial both go through that cable via the ESP’s **on-board USB–serial chip** (Linux often reports `cp210x` or `ch341` in `dmesg` as **`/dev/ttyUSB0`**).

```
Jetson USB port  ──── USB data cable ────  NodeMCU micro-USB
                                              └── built-in USB–serial → ESP UART0
```

**You do not need** separate jumper wires on ESP **RX / TX** for this setup. The TFT still uses SPI on D4/D5/D7/D8 only.

**Important:**

- Use a **data-capable** USB cable. A **charge-only** cable (power wires only) will **not** carry commands — the display may light up but Nina cannot talk to the ESP.
- Do **not** connect the ESP to a **Windows PC** USB port while the Jetson owns the cable.
- For programming on a PC, unplug from the Jetson, flash on Windows, then plug back into the Jetson only.

**Optional setup B — USB–TTL dongle (if not using direct USB):**

```
Jetson USB → CP210x/CH340 dongle → ESP RX, TX, GND (header pins beside USB)
```

**Optional setup C — 40-pin header:** J12 pin 8 → ESP RX, pin 10 → ESP TX, pin 6 → GND, device `/dev/ttyTHS1` (requires `jetson-io` uart1). Not used on the validated robot build.

---

### 5. Jetson software configuration

**Repository on robot:**

```bash
cd ~/megha/jetsonuarteyes   # or your clone path
git pull
```

**Persistent environment** — edit `/etc/nina-link/navigation.env`:

```bash
NINA_EYE_UART_ENABLE=1
NINA_EYE_UART_PORT=/dev/ttyUSB0
NINA_EYE_UART_BAUD=115200
```

Confirm the device after plug-in:

```bash
dmesg | tail -10
# Expect: cp210x ... attached to ttyUSB0
```

**Bench test (no UI):**

```bash
python3 -c "
import serial, time
s = serial.Serial('/dev/ttyUSB0', 115200, timeout=3, dsrdtr=False, rtscts=False)
s.dtr = False
s.rts = False
time.sleep(2.0)
s.reset_input_buffer()
s.write(b'15\n')
s.flush()
time.sleep(0.5)
print('rx:', repr(s.read(64)))
s.close()
"
```

Expected: face shows **love**; `rx` may contain `OK 15` and/or boot menu text.

**CLI:**

```bash
export NINA_EYE_UART_PORT=/dev/ttyUSB0
python3 -m nina.app.eye_cli send 15
python3 -m nina.app.eye_cli list
```

**Kiosk UI:**

```bash
pip3 install -r requirements-link.txt
pip3 install -r sirena_ui/requirements.txt   # if not already installed
set -a && source /etc/nina-link/navigation.env && set +a
export NINA_EYE_UART_PORT=/dev/ttyUSB0
export DISPLAY=:0
PYTHONPATH=. python3 -m sirena_ui
```

Sidebar → **Eyes** → tap an expression.

**Note:** If using `nina-ui-kiosk.service`, ensure `NINA_EYE_UART_PORT=/dev/ttyUSB0` is not overridden to `/dev/ttyTHS1` in the systemd unit (see `desktop/nina-ui-kiosk.service`).

---

### 6. Protocol summary

| Parameter | Value |
|-----------|--------|
| Baud rate | 115200 |
| Format | 8N1, no flow control |
| Command | ASCII expression ID `0`–`33`, terminated with newline (e.g. `15\n`) |
| Firmware ack | `OK <id>` (optional; disable with `NINA_UART_ECHO 0` in sketch) |
| Nina software | Sends `"{id}\n"` via `nina/controllers/eye_expression_uart.py` |

Expression catalog matches `nina/eye/expressions.py` and the firmware boot menu.

---

### 7. Per-action binding (optional)

In `nina/actions/manifest.json`:

```json
"namaste": {
  "file": "recordings/namaste.json",
  "eye_expression": 15,
  "eye_offset": 0.5
}
```

Configure via kiosk **Actions → Eyes**, CLI `eye_cli bind`, or Android companion HTTP API.

---

### 8. Troubleshooting

| Symptom | Action |
|---------|--------|
| Upload fails / “Connecting…” | Disconnect TFT; boot pin sequence (FLASH + RST) |
| PC Serial works; Jetson does not | Use CP210x on Jetson USB; unplug PC USB; set `ttyUSB0` |
| `ModuleNotFoundError: fastapi` | `pip3 install -r requirements-link.txt` |
| UI ImportError on startup | `git pull` (latest `jetsonuarteyes` main) |
| `rx: b''` on Jetson | DTR/RTS false; 2 s delay after open; confirm `dmesg` device |
| `ttyTHS1` send OK in software, no face | Enable uart1 in jetson-io or use USB path |

---

### 9. Reference documentation in repository

- `firmware/nina_eye_esp8266/README.md` — flash checklist  
- `docs/NINA_EYE_UART.md` — UART and Nina integration  
- `docs/ESP8266_EYE_DEPLOYMENT_GUIDE.md` — this document  

Please contact the integration team if the CP210x device enumerates as a different `/dev/ttyUSB*` node or if a second USB serial device (e.g. lidar) shares the bus.

Regards,  
Nina Platform Integration

---

## Attachment file locations (for packaging)

Copy these files into your email or zip archive:

```
firmware/nina_eye_esp8266/User_Setup_ST7735_Nina.h
firmware/nina_eye_esp8266/User_Setup_Select_Nina.patch.txt
firmware/nina_eye_esp8266/nina_eye_esp8266.ino
firmware/nina_eye_esp8266/README.md
```

Optional: include `docs/NINA_EYE_UART.md` for Jetson operators.
