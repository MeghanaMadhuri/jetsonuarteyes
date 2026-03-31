# Nvidia Jetson Platform — Robotics
**Work in progress** - Robotics project on NVIDIA Jetson Orin NX platform by Sirena Technologies.

## Overview
Modular robotics platform exploring vision, motor control, and robotic arm integration:
- **Vision:** Button detection using dual cameras + Roboflow inference
- **Robotic Arm:** 5-DOF arm with MX-28 servos
- **Autonomous Navigation:** BLDC motor control for movement

## Hardware
- NVIDIA Jetson Orin NX with Tanna TechBiz Eagle 201 carrier
- Dual USB cameras
- 5x MX-28 Dynamixel servos
- BLDC motor

## Branch Structure
| Branch | Description |
|--------|-------------|
| `main` | Shared base — hardware docs, architecture, config |
| `feature/arm-vision` | Robotic arm control + vision pipeline |
| `feature/autonomous-nav` | BLDC motor control + navigation |

## Repository Structure
```
├── arm-vision/         # Vision + robotic arm source code
├── autonomous-nav/     # Motor control + navigation source code
├── assets/             # Training image links (see below)
├── config/             # Shared configuration files
└── docs/
    ├── 01-hardware/
    ├── 02-architecture/
    ├── 03-bldc-motor/
    └── 04-robotic-arm/
```

## Training Data
Button detection training images are not stored in this repo. But here  
📁 https://drive.google.com/drive/folders/1_6ja5yJ4V1QREBKwZLTIX_0alCwHAJvq?usp=sharing 

## Requirements
- Python 3.8+
- OpenCV, NumPy, PySerial
- Roboflow Inference SDK

## Setup
Clone the repo and checkout the relevant branch:
```bash
git clone https://github.com/Sirena-Technologies/Nvidia-jetson-platform.git
cd Nvidia-jetson-platform

# For arm + vision work
git checkout feature/arm-vision

# For autonomous navigation
git checkout feature/autonomous-nav
```
# For autonomous navigation
git checkout feature/autonomous-nav
```
