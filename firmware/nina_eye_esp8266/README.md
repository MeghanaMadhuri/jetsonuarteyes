# ESP8266 / NodeMCU eye firmware (ST7735)

## Flash this sketch (Arduino IDE)

**Main file:** [`nina_eye_esp8266.ino`](nina_eye_esp8266.ino) — your full Nina face firmware (expressions **0–33**, UART `Serial.parseInt()` @ **115200**).

### 1. Install libraries

- **TFT_eSPI** (Bodmer) — Library Manager
- Board: **NodeMCU 1.0 (ESP-12E Module)** or **LOLIN(WEMOS) D1 R2**

### 2. TFT_eSPI pin setup

Copy [`User_Setup_ST7735_Nina.h`](User_Setup_ST7735_Nina.h) into:

`Documents/Arduino/libraries/TFT_eSPI/User_Setups/User_Setup_ST7735_Nina.h`

Edit `TFT_eSPI/User_Setup_Select.h` — comment out other `#include` lines and add only:

```cpp
#include <User_Setups/User_Setup_ST7735_Nina.h>
```

Pins (must match your wiring):

| TFT | NodeMCU |
|-----|---------|
| CS | D8 |
| RST | D0 |
| DC | D4 |
| MOSI | D7 |
| SCK | D5 |
| VCC | 3.3 V |
| GND | GND |

### 3. Upload

1. Open `nina_eye_esp8266.ino` in Arduino IDE (same folder as this README).
2. Board **NodeMCU 1.0 (ESP-12E)**; **Upload Speed 115200**.
3. **Tools → Port** — pick the COM port that appears when USB is plugged in (not a stale COM8).
4. Before upload: **disconnect the TFT** (CS on D8 / GPIO15 blocks the ESP8266 bootloader), **disconnect Jetson TX/RX**, close Serial Monitor.
5. If upload stalls at `Connecting...`: hold **FLASH**, tap **RST**, release **FLASH**, then Upload.
6. After success, reconnect TFT; Serial Monitor **115200** — menu lists 0–33; type `15` + Enter for **love**.

**Compile succeeded but upload fails?** That is normal when wiring holds boot pins wrong — not a bug in the sketch. A successful build (37% RAM, ~30% flash) means TFT_eSPI and the firmware path are correct.

### Architecture (this repo)

| Layer | Role |
|-------|------|
| `nina_eye_esp8266.ino` | Draws eyes on ST7735; listens on USB UART @ 115200 |
| `nina/eye/expressions.py` | Same IDs 0–33 as firmware menu |
| `nina/controllers/eye_expression_uart.py` | Jetson sends `"{id}\n"` on `/dev/ttyTHS1` |
| Kiosk / manifest | `eye_expression` binds actions to an ID |

You do **not** need a different chip or library stack for Nina — ESP8266 + TFT_eSPI + serial commands is the intended design.

### 4. Jetson connection

**Validated on robot:** CP210x USB–serial adapter on the Jetson USB port → ESP **RX / TX / GND** (device `/dev/ttyUSB0`). See [docs/ESP8266_EYE_DEPLOYMENT_GUIDE.md](../../docs/ESP8266_EYE_DEPLOYMENT_GUIDE.md).

```bash
NINA_EYE_UART_ENABLE=1
NINA_EYE_UART_PORT=/dev/ttyUSB0
NINA_EYE_UART_BAUD=115200
```

Do not connect the ESP to a PC USB port while the Jetson owns the link.

**Alternate:** 40-pin J12 pins **8 → RX**, **10 → TX**, **6 → GND** (`/dev/ttyTHS1`). Requires `jetson-io` → uart1 ON. Not all carriers route UART1 to pins 8/10 without pinmux.

Nina sends `"{id}\n"` (e.g. `15\n`) — same as Serial Monitor. Firmware replies `OK <id>` when `NINA_UART_ECHO` is enabled.

## Expression IDs (must match Jetson UI)

| ID | Name | ID | Name |
|----|------|----|------|
| 0 | neutral | 17 | look_right |
| 1 | happy | 18 | double_blink |
| 2 | excited | 19 | squint |
| 3 | sad | 20 | confused |
| 4 | sleepy | 21 | sleep |
| 5 | angry | 22 | curious |
| 6 | shocked | 23 | side_eye |
| 7 | wink_right | 24 | listening |
| 8 | focus | 25 | thinking |
| 9 | loading | 26 | acknowledging |
| 10 | scan | 27 | unsure |
| 11 | pulse | 28 | relief |
| 12 | glitch | 29 | tracking |
| 13 | look_down | 30 | shy |
| 14 | look_up | 31 | concerned |
| 15 | love | 32 | wink_left |
| 16 | look_left | 33 | wink_both |

Catalog in repo: `nina/eye/expressions.py`.

## Nina (after flash)

```bash
python -m nina.app.eye_cli send 15
python -m nina.app.eye_cli bind namaste 15 --offset 0.5
```

Manifest example:

```json
"namaste": {
  "file": "recordings/namaste.json",
  "eye_expression": 15,
  "eye_offset": 0.5
}
```

See [docs/NINA_EYE_UART.md](../../docs/NINA_EYE_UART.md).
