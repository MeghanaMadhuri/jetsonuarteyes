# MAX98357A I2S amplifier on Jetson

This is the Nina audio playback setup for a **MAX98357A** connected to a
Jetson I2S output.

The MAX98357A is **not a codec** in the usual Linux ASoC sense. It is a
mono digital Class-D amplifier with an I2S receiver and an internal DAC.
There is no I2C/SPI control interface, no ADC, and no software mixer. It
works as a playback-only I2S sink once the Jetson exposes an ALSA playback
PCM for the I2S controller.

## What is implemented in this repo

Nina playback already uses command-line ALSA-compatible players:

- MP3: `mpg123`
- WAV / silence warmup: `aplay`
- fallback: `ffplay`

The app can be forced to a specific ALSA PCM with:

```bash
NINA_GREET_APLAY_DEVICE=<alsa-pcm>
NINA_AUDIO_MPG123_DEVICE=<alsa-pcm>
NINA_AUDIO_MP3_VIA_APLAY=1
NINA_AUDIO_APLAY_STEREO_MODE=left
NINA_AUDIO_OUTPUT_RATE=48000
NINA_AUDIO_APE_ROUTE=1
NINA_AUDIO_APE_I2S=I2S5
NINA_AUDIO_APE_MUX=ADMAIF1
NINA_AUDIO_APE_MASTER_MODE=cbs-cfs
NINA_AUDIO_APE_BCLK_RATIO=64
NINA_AUDIO_APE_FSYNC_WIDTH=1
```

`scripts/setup-max98357a-audio.sh` writes those values into
`/etc/nina-link/navigation.env`, applies the Orin Nano APE -> I2S5 route
immediately, and plays a 1-second left-slot test tone through the selected PCM.

The script does **not** create the Jetson device-tree / pinmux overlay. That
part is Jetson module + carrier + JetPack specific. Use Jetson-IO when
available, or add the device-tree overlay for your board.

## Hardware connections

MAX98357A boards are usually labelled:

| MAX98357A pin | Connect to Jetson | Notes |
| --- | --- | --- |
| `VIN` / `VDD` | 5 V | Use 5 V for useful speaker power. 3.3 V works at lower power only. |
| `GND` | GND | Common ground with Jetson. |
| `BCLK` / `BCK` | Jetson I2S bit clock / SCLK | Jetson must be I2S master. |
| `LRC` / `LRCLK` / `WS` | Jetson I2S frame sync / LRCLK / FS | Word select. |
| `DIN` | Jetson I2S DOUT / TX | MAX98357A receives data only. |
| `SD` / `SD_MODE` | Pull high to enable, or GPIO | If low/floating wrong, amp is muted/shutdown. |
| `GAIN` / `GAIN_SLOT` | Strap per board datasheet | Fixed gain / channel slot select; no runtime control. |
| `+` / `-` speaker | Speaker | 4-8 ohm speaker. Do not connect speaker `-` to GND. |

### Common 40-pin Jetson header mapping

Many Jetson developer kits expose an I2S port on the 40-pin header:

| Header pin | Common function | Use |
| --- | --- | --- |
| Pin 12 | `I2S*_SCLK` | MAX98357A `BCLK` |
| Pin 35 | `I2S*_FS` / `LRCLK` | MAX98357A `LRC` |
| Pin 40 | `I2S*_DOUT` | MAX98357A `DIN` |
| Pin 2 or 4 | 5 V | MAX98357A `VIN` |
| Pin 6 / 9 / 14 / 20 / 25 / 30 / 34 / 39 | GND | MAX98357A `GND` |

Verify the exact pinmux for your Jetson / carrier. Do not assume the header
pins are in I2S mode just because they are physically connected.

## Enable Jetson I2S / sound card

### Preferred: Jetson-IO

On JetPack images that include Jetson-IO:

```bash
sudo /opt/nvidia/jetson-io/jetson-io.py
```

Then:

1. Choose **Configure Jetson 40pin Header**.
2. Enable an I2S function on the pins you wired.
3. Save and reboot.

After reboot:

```bash
aplay -l
aplay -L
```

Look for a playback PCM corresponding to the Jetson I2S / MAX98357A card.

### Device-tree overlay path

If Jetson-IO is unavailable, you need a board-specific overlay that:

1. Sets the chosen header pins to I2S function, not GPIO.
2. Enables the Jetson I2S controller as the CPU DAI.
3. Adds a `maxim,max98357a` codec node.
4. Connects CPU DAI to codec DAI via the NVIDIA audio graph card or a
   `simple-audio-card`, depending on the JetPack kernel and board.

Conceptual DTS shape:

```dts
max98357a: max98357a {
    compatible = "maxim,max98357a";
    #sound-dai-cells = <0>;
    /* optional if SD_MODE is wired to GPIO:
     * sdmode-gpios = <&gpio ... GPIO_ACTIVE_HIGH>;
     */
};

sound {
    compatible = "simple-audio-card";
    simple-audio-card,name = "max98357a";
    simple-audio-card,format = "i2s";
    simple-audio-card,bitclock-master = <&cpu_dai>;
    simple-audio-card,frame-master = <&cpu_dai>;

    cpu_dai: simple-audio-card,cpu {
        sound-dai = <&tegra_i2sX>;
    };

    simple-audio-card,codec {
        sound-dai = <&max98357a>;
    };
};
```

The exact node names (`tegra_i2sX`, audio graph card endpoints, GPIO names)
vary by Jetson generation and JetPack version.

## Configure Nina once ALSA sees the card

Install playback tools and show devices:

```bash
cd ~/BLDC_HARI/Nvidia-jetson-platform
./scripts/setup-max98357a-audio.sh --list
```

Pick the ALSA PCM for the I2S/MAX98357A card. Examples:

```bash
plughw:CARD=max98357a,DEV=0
hw:2,0
plughw:CARD=tegrasndt210ref,DEV=0
```

Then configure Nina:

```bash
./scripts/setup-max98357a-audio.sh --device plughw:CARD=max98357a,DEV=0 --rate 48000
```

The script writes these lines into `/etc/nina-link/navigation.env`:

```bash
NINA_GREET_APLAY_DEVICE=plughw:CARD=max98357a,DEV=0
NINA_AUDIO_MPG123_DEVICE=plughw:CARD=max98357a,DEV=0
NINA_AUDIO_MP3_VIA_APLAY=1
NINA_AUDIO_APLAY_STEREO_MODE=left
NINA_AUDIO_OUTPUT_RATE=48000
NINA_AUDIO_OUTPUT_WARMUP_MS=100
NINA_AUDIO_PREROLL_MS=0
NINA_AUDIO_MUTE_PREROLL_SEC=0
NINA_AUDIO_APE_ROUTE=1
NINA_AUDIO_APE_CARD=APE
NINA_AUDIO_APE_I2S=I2S5
NINA_AUDIO_APE_MUX=ADMAIF1
NINA_AUDIO_APE_CHANNELS=2
NINA_AUDIO_APE_BITS=16
NINA_AUDIO_APE_FRAME_MODE=i2s
NINA_AUDIO_APE_MASTER_MODE=cbs-cfs
NINA_AUDIO_APE_BCLK_RATIO=64
NINA_AUDIO_APE_FSYNC_WIDTH=1
```

Then restart Nina:

```bash
systemctl --user restart nina-ui-kiosk.service
```

## Test commands

Raw ALSA sine test:

```bash
speaker-test -D plughw:CARD=max98357a,DEV=0 -c 2 -r 48000 -t sine -f 440
```

On the reference Jetson Orin Nano + MAX98357A build the validated APE route is:

```bash
amixer -c APE cset name='I2S5 Mux' ADMAIF1
amixer -c APE cset name='I2S5 Sample Rate' 48000
amixer -c APE cset name='I2S5 Playback Audio Channels' 2
amixer -c APE cset name='I2S5 Playback Audio Bit Format' 16
amixer -c APE cset name='I2S5 Client Channels' 2
amixer -c APE cset name='I2S5 Client Bit Format' 16
amixer -c APE cset name='I2S5 codec frame mode' i2s
amixer -c APE cset name='I2S5 codec master mode' cbs-cfs
amixer -c APE cset name='I2S5 BCLK Ratio' 64
amixer -c APE cset name='I2S5 FSYNC Width' 1
```

`scripts/launch-sirena.sh` applies those automatically at every Nina startup
when `NINA_AUDIO_APE_ROUTE=1` is present in `/etc/nina-link/navigation.env`.

Repo alert MP3 through the same PCM (the same decode-to-WAV path Nina uses
when `NINA_AUDIO_MP3_VIA_APLAY=1`):

```bash
mpg123 -q -r 48000 -w /tmp/nina-cant-move.wav nina/audio/alerts/cant_move.mp3
aplay -D plughw:CARD=max98357a,DEV=0 /tmp/nina-cant-move.wav
```

Nina runtime env:

```bash
systemctl --user show nina-ui-kiosk.service --property=Environment \
  | tr ' ' '\n' \
  | grep -E 'NINA_(GREET_APLAY_DEVICE|AUDIO_)'
```

## Troubleshooting

### `aplay -l` shows no I2S / MAX98357A card

The repo-side config cannot fix this. The Jetson pinmux / device tree is not
enabled yet. Revisit Jetson-IO or the board-specific overlay.

### ALSA card exists but no sound

Check:

- `SD_MODE` is high / enabled.
- MAX98357A has 5 V and common GND.
- Speaker is connected across the amp `+` / `-` terminals, not to GND.
- `DIN` is wired to Jetson **DOUT**, not DIN.
- `BCLK` and `LRC` are not swapped.
- Try `--rate 48000`; many Jetson I2S paths are happiest at 48 kHz.
- Use `speaker-test` first; if it fails, Nina will fail too.

### Direct `mpg123 -o alsa` fails or produces garbage

On Jetson Orin Nano APE -> MAX98357A, `speaker-test` / `aplay` can work while
direct `mpg123 -o alsa -a ...` fails or produces garbage. Leave
`NINA_AUDIO_MP3_VIA_APLAY=1` enabled (the setup script writes it by default).
Nina will decode MP3 to a temporary WAV with `mpg123 -w`, then play that WAV
through `aplay -D <device>`.

If the breakout is strapped to the left I2S slot (common on MAX98357A modules),
set:

```bash
NINA_AUDIO_APLAY_STEREO_MODE=left
```

That converts decoded mono MP3s to stereo WAVs with audio only on the left slot
and silence on the right. Other values are `right`, `dual`, and `none`.

### WAV / warmup is silent

The code passes `NINA_GREET_APLAY_DEVICE` to WAV playback as well as MP3
playback. Pull a commit that includes this doc and rerun the setup script.

### Sound is too quiet / too loud

MAX98357A gain is hardware-strapped. There is no runtime mixer on the chip.
Change the `GAIN` / `GAIN_SLOT` wiring per the board datasheet, or change the
source audio amplitude.

### Need microphone input

MAX98357A is playback-only. It has no ADC. Add a separate I2S digital MEMS mic
or switch to a real codec module (for example WM8960-class hardware).

