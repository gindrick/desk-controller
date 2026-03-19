# DeskControl — IKEA RODULF Sit/Stand Desk Controller

Web-based controller for the **IKEA RODULF** sit/stand desk using an ESP32, MQTT, and an HC-SR04 ultrasonic height sensor. Supports manual control, customisable height presets, and automatic movement to a target height with settle-and-correct logic.

---

## Features

- Manual up/down control with auto-stop timer
- **Goto** — automatic movement to a target height (±0.5 cm deadband)
- **Settle & correct** — after stopping, waits 1.5 s for sensor to stabilise, then applies one correction move if needed
- Height presets: protected MIN/MAX defaults + unlimited custom presets (add, rename, delete via UI)
- 60 fps smooth height display using requestAnimationFrame + dead reckoning during movement
- Adaptive polling: 200 ms during movement / 5 s post-move cooldown / 2 s idle
- Motion model: learns desk speed from movement history (stored in SQLite)
- Profile history: every move is recorded and visualised
- Spurious sensor reading rejection (HC-SR04 readings > 1.25 m discarded at firmware level)
- NO_MOVEMENT detection: warns if relay activated but desk did not move (thermal cutout)
- Web UI accessible from any device on the local network

---

## Hardware

### Bill of materials

| Component | Notes |
|-----------|-------|
| **ESP32 DevKit** (38-pin, e.g. WROOM-32) | Main controller |
| **4-channel relay module with optocoupler** (5 V, isolated logic) | Controls desk motor — optocoupler is required |
| **HC-SR04** ultrasonic distance sensor | Measures desk height |
| **RJ45 breakout board** or connector | Connects to desk control panel |
| 5 V power supply for ESP32 (USB or module) | |
| Jumper wires, breadboard or PCB | |

> Estimated total component cost: ~10–20 EUR

### Wiring

```
IKEA RODULF RJ45 connector
  pin 3 (UP)   ──── NO relay 1 ──┐
  pin 4 (DOWN) ──── NO relay 2 ──┤
  pin 7 (COM)  ────────────────-─┘  (common wire)

Relay module:
  VCC  → 5 V ESP32
  GND  → GND ESP32
  IN1  → GPIO25 (relay UP)
  IN2  → GPIO26 (relay DOWN)

HC-SR04:
  VCC  → 5 V ESP32
  GND  → GND ESP32
  TRIG → GPIO18
  ECHO → GPIO19
```

> **Warning:** The relay module must have an optocoupler (separate VCC and JD-VCC pins). Without it, switching the relay can reset the ESP32.

### HC-SR04 placement

Mount the sensor on the **fixed part of the desk base frame**, beam pointing straight down to the floor. Keep it away from the telescopic leg — reflections from moving parts cause incorrect readings.

```
[desk top]
════════════════
      |
  telescopic
     leg
      |
════════════════  ← base crossbar → mount HC-SR04 HERE (beam ↓)
      |
══════════════════
    [floor]
```

### RODULF RJ45 pinout

The connector is standard RJ45. The exact control signal pins must be measured with a multimeter on your unit — IKEA does not publish the pinout. Typically:
- 2 pins for UP signal
- 2 pins for DOWN signal
- Signal voltage ~5 V DC

---

## Software requirements

### PC (server)

- **Python 3.10+**
- **Mosquitto MQTT broker** — [mosquitto.org](https://mosquitto.org/download/)
- **ESPHome** — to flash firmware to the ESP32

Install Python dependencies:
```bash
pip install flask flask-cors paho-mqtt
```

### ESP32

Firmware is managed via **ESPHome** (`desk_esp32.yaml`).

---

## Installation & setup

### 1. Clone the repository

```bash
git clone https://github.com/gindrick/desk-controller.git
cd desk-controller
```

### 2. Configuration

Create `secrets.yaml` (not tracked by git):
```yaml
wifi_ssid: "YourWiFiName"
wifi_password: "YourWiFiPassword"
mqtt_broker: "192.168.x.x"   # IP address of the PC running Mosquitto
```

Edit `main.py` if your MQTT broker is not on localhost:
```python
MQTT_BROKER = "192.168.x.x"
MQTT_PORT   = 1883
```

### 3. Flash the ESP32

Connect the ESP32 via USB, then:
```bash
esphome run desk_esp32.yaml
```

After flashing, the ESP32 connects to WiFi and MQTT automatically. Subsequent updates can be done via OTA (`esphome upload desk_esp32.yaml`).

### 4. Sensor calibration

After mounting the HC-SR04, set `sensor_offset` in `desk_esp32.yaml`:

```yaml
globals:
  - id: sensor_offset
    type: float
    initial_value: '8.0'   # adjust based on calibration
```

Calibration steps:
1. Set the desk to a known height (measure from floor to desk surface with a tape measure)
2. Monitor the MQTT height topic: `mosquitto_sub -h localhost -t "desk/height"`
3. Compare the reported height to the actual height
4. Adjust `sensor_offset` by the difference and reflash

### 5. Start

```bash
# Terminal 1 — Mosquitto MQTT broker
mosquitto

# Terminal 2 — Flask backend
python main.py
```

Web UI: **http://localhost:5001**

Access from other devices on the same WiFi: **http://192.168.x.x:5001**

---

## UI overview

- **Height card** — large real-time height display, sensor/DR badge, comparison row
- **Controls & Presets card**
  - UP / DOWN buttons with configurable hold duration
  - STOP button
  - MIN and MAX protected presets (cannot be deleted)
  - Custom presets — add, rename (pencil icon), delete (✕)
  - GOTO row — enter any target height and press GO
  - SET HEIGHT row — manually override the stored height (use after sensor calibration)
- **Motion Model** (collapsible) — learned speed profiles for UP and DOWN
- **Profile History** (collapsible) — chart and table of recorded moves

---

## Architecture

```
[Browser / UI]
       |  HTTP (adaptive polling)
       ↓
[Flask  main.py  :5001]
       |         |
  SQLite DB   paho-mqtt
(desk_state.db)   |
                  ↓
         [Mosquitto  :1883]
                  |  WiFi / MQTT
                  ↓
         [ESP32 + ESPHome]
                  |  GPIO25 / GPIO26
                  ↓
          [Relay module]
                  |  RJ45
                  ↓
         [IKEA RODULF motor]
                  ↑
         [HC-SR04] → GPIO18/19 → ESP32 → desk/height → Flask → UI
```

### MQTT topics

| Topic | Direction | Payload |
|-------|-----------|---------|
| `desk/command` | PC → ESP32 | `up` / `down` / `stop` |
| `desk/height` | ESP32 → PC | current height in cm (float) |
| `desk/move` | ESP32 → PC | `moving_up` / `moving_down` / `idle` |
| `desk/debug` | ESP32 → PC | ESPHome log output |

### Key constants (`main.py`)

| Constant | Default | Description |
|----------|---------|-------------|
| `DEADBAND` | 0.5 cm | Goto stop tolerance |
| `HEIGHT_MIN` | 70.0 cm | Lower travel limit |
| `HEIGHT_MAX` | 116.5 cm | Upper travel limit |
| `STALE_SEC` | 2.0 s | Sensor age threshold for dead reckoning fallback |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| UI shows MQTT ERROR | Mosquitto not running or wrong IP | Check `MQTT_BROKER` in `main.py` |
| Height not displayed | ESP32 not connected to WiFi/MQTT | Check `secrets.yaml`, reflash |
| Desk does not move | Relay wiring issue or missing optocoupler | Check wiring and relay module type |
| Goto stops at wrong height | Spurious HC-SR04 reading | Ensure `desk_esp32.yaml` filter is `if (x < 0.60f \|\| x > 1.25f) return {};` and reflash |
| NO_MOVEMENT warning in log | Motor thermal cutout triggered | Wait 10–15 minutes for motor to cool down |
| Height jumps during movement | Sensor obstruction or bad placement | Reposition HC-SR04, see placement section |

---

## Project files

```
desk-controller/
├── main.py             # Flask backend + MQTT client + goto logic
├── desk_esp32.yaml     # ESPHome firmware for ESP32
├── desk_state.db       # SQLite database (auto-created; not in git)
├── secrets.yaml        # WiFi + MQTT credentials (not in git)
├── .gitignore
└── static/
    └── index2.html     # Web UI (served at /)
```

---

## Notes

- **Motor thermal protection:** IKEA RODULF has a built-in thermal cutout. After many consecutive moves (e.g. during calibration), the motor stops responding even though the relay clicks. Wait 10–15 minutes before resuming.
- **Multi-device access:** The server must run on a machine that has access to the local MQTT broker. For access from outside the local network, use Tailscale or a similar VPN mesh.
- **Sensor accuracy:** HC-SR04 has ~3–5 mm repeatability. The settle-and-correct logic compensates for most overshoot/undershoot. For better accuracy, consider upgrading to a VL53L1X ToF sensor.
