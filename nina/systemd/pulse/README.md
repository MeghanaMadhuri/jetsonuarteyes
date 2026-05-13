# PulseAudio / I2S stability (Jetson + MAX98357 class)

Heavy bursts when **Autonomous** mode starts (SLAM, lidar, RealSense, ultrasonics, pilot) can starve PulseAudio’s real-time mixer thread and cause **underruns** (continuous tick, then hash / static on every app until the sink is restarted).

This folder ships a **daemon.conf.d** fragment with larger hardware buffers and mild priority tuning. It complements manual steps you may already use (`module-suspend-on-idle` off, 48 kHz / s32le, etc.).

## Install (automatic)

From the repo on the Jetson:

```bash
./scripts/install-nina-link-jetson.sh --all
# or at least:
./scripts/install-nina-link-jetson.sh --with-systemd
```

The installer copies `daemon-i2s-under-load.conf` into `/etc/pulse/daemon.conf.d/` and ensures `/etc/pulse/daemon.conf` includes the drop-in directory.

## Install (manual)

```bash
sudo mkdir -p /etc/pulse/daemon.conf.d
sudo cp nina/systemd/pulse/daemon-i2s-under-load.conf /etc/pulse/daemon.conf.d/99-nina-i2s-under-load.conf
grep -q 'daemon.conf.d' /etc/pulse/daemon.conf || \
  printf '\n### Nina drop-ins\n.include /etc/pulse/daemon.conf.d/*.conf\n' | sudo tee -a /etc/pulse/daemon.conf
```

Then reload PulseAudio as the **graphical session user** (not necessarily root):

```bash
pulseaudio -k && pulseaudio --start
```

Or reboot.

## Application-side tuning

`nina-link.service` is installed with `Nice=5` so the Sirena UI process yields CPU to PulseAudio under load. Override in a systemd drop-in if you need a different trade-off.

Autonomy bring-up can be stretched with wall-clock sleeps between subsystems (default **200 ms** total, split across steps):

```bash
# /etc/nina-link/navigation.env — optional  
NINA_AUTONOMY_ENABLE_STAGGER_MS=400
```

Set to `0` to disable staggering.

## Verify

```bash
pactl info | grep "Sample Specification"
pactl list sinks | grep -E 'Name:|Latency:|configured latency'
```

If **Underrun** / **Suspended** counters climb when toggling autonomy, increase `NINA_AUTONOMY_ENABLE_STAGGER_MS` or raise `default-fragment-size-msec` × `default-fragments` in the fragment.

## Rollback

```bash
sudo rm -f /etc/pulse/daemon.conf.d/99-nina-i2s-under-load.conf
pulseaudio -k && pulseaudio --start
```

Remove the appended `.include` line from `/etc/pulse/daemon.conf` only if nothing else uses `daemon.conf.d`.
