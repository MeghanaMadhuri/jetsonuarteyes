# Nina Robot — Customer Setup Manual

This guide walks you through powering, first use, charging, and daily operation of your **Sirena Nina** robot.

---

## Table of contents

1. [What you received](#1-what-you-received)
2. [Safety before you start](#2-safety-before-you-start)
3. [Power and battery](#3-power-and-battery)
4. [Getting started](#4-getting-started)
5. [Daily operation](#5-daily-operation)
6. [Charging and battery care](#6-charging-and-battery-care)
7. [Using the touchscreen app](#7-using-the-touchscreen-app)
8. [Optional: Android companion tablet](#8-optional-android-companion-tablet)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. What you received

A typical Nina unit includes:

| Item | Purpose |
|------|---------|
| **Nina robot** (with built-in computer and battery) | Ready-to-run platform |
| **10.1" touchscreen** (1024 × 600) | On-robot control panel |
| **24 Ah battery** (installed) | Powers the whole robot — computer, wheels, and electronics |
| **USB camera** | Front view on Drive / Vision screens |
| **Supplied charger** | Charges Nina at the charging port on the robot (see §6) |

The **dongle power switch**, **voltage checker**, and **charging port** are **attached to Nina** at the **bottom back** (not separate parts in the box).

---

## 2. Safety before you start

- **One battery** powers Nina (24 Ah). Use only the **charger supplied with your unit**.
- The pack can be charged up to **28 V** maximum; **26 V** is the recommended charge target for everyday use.
- **E-STOP:** On the Drive screen, tap **E-STOP** to stop the wheels immediately.

---

## 3. Power and battery

### 3.1 Single battery — how Nina is powered

Nina runs from one **24 Ah battery** installed in the robot. You do **not** plug in a separate computer power adapter for normal use.

| Location (bottom back of Nina — **attached to the robot**) | What it does |
|-------------------------------------------------------------|--------------|
| **Dongle switch** | Main power **on / off** |
| **Voltage checker** | Shows pack voltage (compare with the UI — see §9) |
| **Charging port** | Plug in the **supplied charger** to charge (see §6) |

### 3.2 Voltage you should see

| State | Approximate voltage | Notes |
|-------|---------------------|--------|
| **Recommended after charge** | **~26 V** | Best target when using the supplied charger |
| **Maximum charge** | **up to 28 V** | Do not exceed what your charger and pack allow |
| Normal use | **24 V – 26 V** | Voltage dips slightly under load |
| **Low battery warning** (UI / voice) | **≤ 25.5 V** | Robot asks you to charge; driving may be limited |
| **Resume driving after low warning** | **≥ 26.1 V** | Motion unlocks when the pack has recovered |

### 3.3 What happens when the battery is low

1. The **top bar** and **Drive** screen show pack voltage (e.g. `24.8 V`).
2. Below **25.5 V**: Nina plays *"I'm low on battery"* and you should charge soon.
3. **Driving is blocked** until the pack recovers to about **26.1 V** or higher — then you can drive again without restarting.
4. If the UI shows `n/a` for battery, see [§9 Troubleshooting](#9-troubleshooting).

---

## 4. Getting started

### 4.1 Before first drive

- [ ] Chassis secure; wheels spin freely.
- [ ] Touchscreen cable secure.
- [ ] **Dongle switch** at the bottom back is accessible.
- [ ] **Voltage checker** reads a healthy level (and matches the UI after power-on — §9).

### 4.2 Power on

1. Set the **dongle switch** at the **bottom back** of Nina to **ON**.
2. Wait ~30–60 seconds. The **Sirena Nina** app should appear fullscreen (**1024 × 600**).
3. Check **battery** in the top bar and compare with the **voltage checker** on the robot — they should agree within about **0.2 V**.

### 4.3 First drive (supervised)

1. Open **Drive** from the left sidebar.
2. Keep speed at the **low** end for indoor use.
3. Use the D-pad for a short forward test; release to stop.
4. Use **E-STOP** if anything unexpected happens.

**Default controls (Drive screen):**

| Control | Action |
|---------|--------|
| D-pad | Forward / back / turn in place |
| Speed slider | Keep low for indoor use |
| **E-STOP** | Emergency stop — stops the wheels immediately |

---

## 5. Daily operation

### 5.1 Startup

1. Turn **ON** the **dongle switch** at the bottom back; wait for the Nina UI.
2. Check **battery** in the top bar and on the **voltage checker** — both should show a realistic voltage (not `n/a`).
3. Open **Health** (tap the status title) if you want a full subsystem check.

### 5.2 Manual driving

1. Open **Drive**.
2. Set speed to the **low** end indoors.
3. Use the D-pad; release to stop.
4. Use **E-STOP** if needed.

### 5.3 Shutdown

1. Stop driving and tap **E-STOP** if the wheels were moving.
2. Turn **OFF** the **dongle switch** at the **bottom back** of Nina.
3. Charge when needed (§6) — use the **charging port** next to the dongle area with the **supplied charger**.

---

## 6. Charging and battery care

### 6.1 How to charge Nina

1. Turn **OFF** the **dongle switch** (robot powered down).
2. At the **bottom back**, locate the **charging port** (near the dongle switch and voltage checker).
3. Plug in the **charger supplied with your Nina**.
4. Charge in a **ventilated** area; do not cover the robot while charging.
5. **Recommended:** charge until the **voltage checker** (and UI, when you power on later) show about **26 V** for regular use.
6. The pack may reach up to **28 V** at full charge — follow your charger’s indicator lights and instructions; do not leave on charge unattended longer than the manufacturer recommends.
7. Unplug the charger when done, then turn the **dongle switch** **ON** to use Nina again.

### 6.2 When to charge

| What you see | What to do |
|--------------|------------|
| UI or voice says **low battery** (around **25.5 V** or below) | Stop driving; charge with the supplied charger |
| **Voltage checker** clearly lower than usual | Charge before the next session |
| After a full charge, checker shows **~26 V** | Good for normal use |
| Swollen, hot, or damaged battery area | **Do not use** — contact Sirena support |

### 6.3 Storage

- Turn **OFF** the **dongle switch** when Nina will sit unused for days.
- If storing for weeks, charge to about **26 V** first, then recheck every **1–3 months** and top up if the voltage checker has dropped noticeably.

### 6.4 How long it lasts

Runtime depends on speed, floor surface, and how much you drive. Lower speeds use less power. Expect to charge more often after long or fast driving sessions.

---

## 7. Using the touchscreen app

| Screen | Use |
|--------|-----|
| **Home** | Overview, quick status |
| **Drive** | Manual control, front camera |
| **Vision** | Face / object detection |
| **Map** | Map view (if enabled on your unit) |
| **Actions** | Recorded motions (e.g. wave, namaste) |
| **Settings** | Wi‑Fi, updates |
| **Health** | Battery and subsystem status — useful for support calls |

**Battery:** The top bar shows pack voltage. It should match the **voltage checker** on the bottom back of Nina (see §9).

---

## 8. Optional: Android companion tablet

1. Install the **Sirena** Android app from the APK supplied with your unit.
2. On the tablet **Setup** screen, enter Nina’s address (your installer or support team will provide the URL, often `http://<nina-ip>:8787` on the same Wi‑Fi).
3. Phone/tablet and Nina must be on the same network.

---

## 9. Troubleshooting

### 9.1 Display wrong size or blank

- Turn the **dongle switch** **OFF**, wait 10 seconds, **ON** again.
- If the problem continues, contact support with a photo of the screen.

### 9.2 Battery on screen does not match the voltage checker

The **voltage checker** (attached at the **bottom back**, next to the **dongle switch**) is your on-robot reference.

1. With Nina **ON**, compare the **checker reading** to the voltage shown in the **top bar** / **Drive** screen.
2. They should be **close** (within about **0.2 V**). Small differences under load are normal.
3. If the UI is much **lower** than the checker → note both numbers and contact support.
4. If the UI is much **higher** than the checker → note both numbers and contact support.
5. If both show a **low** voltage → **charge Nina** using the **charging port** and **supplied charger** (§6).

### 9.3 “Low battery” but the checker looks fine

- Compare **checker** vs **UI** as in §9.2.
- If the checker is above **~26 V** but the UI still blocks driving, power **OFF** the dongle switch, wait 30 seconds, power **ON**, and check again.
- If it persists, contact support with both readings.

### 9.4 Robot will not drive after a low-battery warning

This is normal protection. **Charge** Nina to about **26 V** (checker and UI). Driving unlocks when the pack recovers (typically **≥ 26.1 V** on the UI).

### 9.5 No sound on alerts

- Check volume on the speaker or amplifier if your unit has a physical volume control.
- Contact support if alerts never play after a confirmed low-battery or touch event.

### 9.6 Contacting support

Provide:

- Photo of **Health** screen  
- **Voltage checker** reading and **UI** battery reading at the same time  
- Whether the **dongle switch** is ON and whether you were charging  

---

*Sirena Technologies — Nina customer setup (May 2026).*
