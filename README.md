# DeskControl — IKEA RODULF Sit/Stand Desk Controller

> **Czech version below / Česká verze níže**

Web-based controller for the **IKEA RODULF** sit/stand desk using ESP32, MQTT and an ultrasonic height sensor. Supports manual control, height presets (SIT/STAND) and automatic movement to a target height.

---

## Features

- Manual up/down control
- Automatic movement to target height (seeking with ±1.5 cm deadband)
- Height presets (default: SIT = 75 cm, STAND = 117 cm), fully editable
- Live height display with 0.5 cm resolution
- Sensor obstruction detection (warning after 10 s without valid reading)
- Hardware maximum height limit (desk protection)
- Web UI accessible from any device on the local network

---

## Hardware

### Shopping list

| Component | Notes |
|-----------|-------|
| **ESP32 DevKit** (38-pin, e.g. WROOM-32) | Main controller |
| **4-channel relay module with optocoupler** (5V, isolated logic) | Controls desk motor — optocoupler required |
| **HC-SR04** ultrasonic distance sensor | Measures desk height |
| **RJ45 breakout board** or connector | Connects to desk control panel |
| 5V power supply for ESP32 (USB or module) | |
| Jumper wires, breadboard or PCB | |

> Estimated total component cost: ~10–20 EUR

### Wiring diagram

```
IKEA RODULF RJ45 connector
  pin 3 (UP)   ──── NO relay 1 ──┐
  pin 4 (DOWN) ──── NO relay 2 ──┤
  pin 7 (COM)  ─────────────────-┘  (common wire)

Relay module:
  VCC  → 5V ESP32
  GND  → GND ESP32
  IN1  → GPIO25 (relay_up)
  IN2  → GPIO26 (relay_down)

HC-SR04:
  VCC  → 5V ESP32
  GND  → GND ESP32
  TRIG → GPIO18
  ECHO → GPIO19
```

> **Warning:** The relay module must have an optocoupler (separate VCC and JD-VCC). Without it, switching the relay may reset the ESP32.

### HC-SR04 placement

Mount the sensor on the **fixed part of the desk base frame**, beam pointing straight down to the floor. Do not place it in the path of the telescopic leg — parasitic reflections from moving parts cause incorrect readings.

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

The connector is a standard RJ45. The relevant control signal pins must be measured with a multimeter on your specific desk unit — IKEA does not publish the pinout. Typically:
- 2 pins for UP signal
- 2 pins for DOWN signal
- Signal voltage ~5V DC

---

## Software requirements

### PC (server)

- **Python 3.10+**
- **Mosquitto MQTT broker** — [mosquitto.org](https://mosquitto.org/download/)
- **ESPHome** — to flash firmware to the ESP32

Install Python dependencies:
```bash
pip install -r requirements.txt
```

`requirements.txt` includes: `flask`, `flask-cors`, `paho-mqtt`, `python-dotenv`

### ESP32

- Firmware is flashed via **ESPHome** (see below)

---

## Installation & setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd desk_ctrl
```

### 2. Configuration — secrets

Create `secrets.yaml` (not tracked by git):
```yaml
wifi_ssid: "YourWiFiName"
wifi_password: "YourWiFiPassword"
mqtt_broker: "192.168.x.x"   # IP address of the PC running Mosquitto
```

Create `.env` (not tracked by git):
```
MQTT_BROKER=192.168.x.x
MQTT_PORT=1883
```

### 3. Flash the ESP32

```bash
esphome run desk_esp32.yaml
```

ESP32 must be connected via USB. After flashing it will connect to WiFi and MQTT automatically.

### 4. Sensor calibration

After mounting the HC-SR04, set the `sensor_offset` in `desk_esp32.yaml`:

```yaml
globals:
  - id: sensor_offset
    type: float
    initial_value: '8.0'   # adjust based on calibration
```

Calibration steps:
1. Set the desk to a known height (measure with a tape measure from floor to top of desk surface)
2. Start MQTT monitor: `mosquitto_sub -h localhost -t "desk/height"`
3. Compare the displayed height to the actual height
4. Adjust `sensor_offset` by the difference and reflash

### 5. Start all services

```bash
start.bat
```

Or manually:
```bash
# Terminal 1 — Mosquitto
mosquitto

# Terminal 2 — Flask backend
python app.py

# Terminal 3 — ESPHome dashboard (optional)
esphome dashboard .
```

Web UI: **http://localhost:5000**

---

## Configuration

### Height presets

Edit `config.json` or use the "Save current height" button in the UI:
```json
{
  "presets": {
    "sit": 77,
    "stand": 117
  }
}
```

> Values are target heights in cm sent to the ESP32. Actual stopping height may differ by ±2 cm depending on calibration.

### Maximum height

Set `max_height` in `desk_esp32.yaml` to match your desk (default 117 cm):
```yaml
  - id: max_height
    type: float
    initial_value: '117.0'
```

---

## Architecture

```
[Browser / UI]
       |  HTTP polling (300 ms)
       ↓
[Flask app.py :5000]
       |  paho-mqtt
       ↓
[Mosquitto MQTT broker :1883]
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
[HC-SR04] → GPIO18/19 → ESP32 → desk/height topic → Flask → UI
```

### MQTT topics

| Topic | Direction | Payload |
|-------|-----------|---------|
| `desk/command` | PC → ESP32 | `up` / `down` / `stop` |
| `desk/target` | PC → ESP32 | target height in cm (float) |
| `desk/height` | ESP32 → PC | current height in cm |
| `desk/move` | ESP32 → PC | `moving_up` / `moving_down` / `idle` |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| UI shows OFFLINE | Mosquitto not running or wrong IP in `.env` | Check `MQTT_BROKER` in `.env` |
| Height not displayed | ESP32 not connected to WiFi/MQTT | Check `secrets.yaml`, reflash |
| Desk not moving | Relay wiring issue or missing optocoupler | Check wiring and relay module type |
| Nonsensical height readings | Sensor obstructed or misplaced | Reposition HC-SR04, see placement section |
| ⚠ zastíněno in UI | Sensor has not seen the floor for >10 s | Remove obstruction in front of sensor |

---

## Project files

```
desk_ctrl/
├── app.py              # Flask backend + MQTT client
├── desk_esp32.yaml     # ESPHome firmware for ESP32
├── config.json         # Height presets (auto-generated)
├── requirements.txt    # Python dependencies
├── start.bat           # One-click launcher (Windows)
├── secrets.yaml        # WiFi + MQTT credentials (not in git)
├── .env                # Environment variables for Flask (not in git)
├── .gitignore
└── static/
    └── index.html      # Web UI
```

---
---

# DeskControl — Ovladač stolu IKEA RODULF (CZ)

Webové rozhraní pro ovládání výškově nastavitelného stolu **IKEA RODULF** přes ESP32, MQTT a ultrazvukový senzor výšky. Umožňuje manuální ovládání, přednastavené polohy (SIT/STAND) a automatický přesun na zadanou výšku.

---

## Funkce

- Manuální ovládání nahoru/dolů
- Automatický přesun na cílovou výšku (seeking s deadbandem ±1.5 cm)
- Přednastavené polohy (výchozí: SIT = 75 cm, STAND = 117 cm), lze přidávat/mazat
- Živé zobrazení výšky stolu s přesností 0.5 cm
- Detekce zastínění senzoru (varování po 10 s bez platného čtení)
- Hardwarový limit maximální výšky (ochrana stolu)
- Webové UI přístupné z libovolného zařízení v síti

---

## Hardware

### Co koupit

| Součástka | Poznámka |
|-----------|----------|
| **ESP32 DevKit** (38-pin, např. WROOM-32) | Mozek celého systému |
| **Relé modul 4× s optočlenem** (5V, oddělená logika) | Ovládání motoru stolu — nutný optočlen |
| **HC-SR04** (ultrazvukový senzor vzdálenosti) | Měření výšky stolu |
| **RJ45 breakout board** nebo konektor | Propojení s ovládacím panelem stolu |
| Napájení 5V pro ESP32 (USB nebo modul) | |
| Propojovací vodiče, breadboard nebo DPS | |

> Celková cena součástek: cca 300–500 Kč

### Schéma zapojení

```
IKEA RODULF RJ45 konektor
  pin 3 (UP)   ──── NO relé 1 ──┐
  pin 4 (DOWN) ──── NO relé 2 ──┤
  pin 7 (COM)  ────────────────-┘  (společný vodič)

Relé modul:
  VCC  → 5V ESP32
  GND  → GND ESP32
  IN1  → GPIO25 (relay_up)
  IN2  → GPIO26 (relay_down)

HC-SR04:
  VCC  → 5V ESP32
  GND  → GND ESP32
  TRIG → GPIO18
  ECHO → GPIO19
```

> **Pozor:** Relé modul musí mít optočlen (oddělená VCC a JD-VCC). Bez optočlenu hrozí reset ESP32 při spínání.

### Umístění senzoru HC-SR04

Senzor připevni na **pevnou část základny rámu stolu**, paprsek míří kolmo dolů k podlaze. Nesmí být v dráze teleskopické nohy — parasitní odrazy od pohyblivých částí způsobují chybná čtení.

```
[deska stolu]
════════════════
      |
  teleskop.
    noha
      |
════════════════  ← příčník základny → SEM dát HC-SR04 (paprsek ↓)
      |
══════════════════
    [podlaha]
```

### Piny RJ45 stolu RODULF

Konektor je standardní RJ45. Relevantní piny ovládacího signálu je nutné změřit multimetrem pro konkrétní kus stolu — IKEA nepublikuje pinout. Typicky:
- 2 piny pro signál UP
- 2 piny pro signál DOWN
- Napětí signálu ~5V DC

---

## Software — závislosti

### PC (server)

- **Python 3.10+**
- **Mosquitto MQTT broker** — [mosquitto.org](https://mosquitto.org/download/)
- **ESPHome** — pro flash firmware do ESP32

Instalace Python závislostí:
```bash
pip install -r requirements.txt
```

`requirements.txt` obsahuje: `flask`, `flask-cors`, `paho-mqtt`, `python-dotenv`

### ESP32

- Firmware se nahrává přes **ESPHome** (viz níže)

---

## Instalace a spuštění

### 1. Klonování repozitáře

```bash
git clone <repo-url>
cd desk_ctrl
```

### 2. Konfigurace — secrets

Vytvoř soubor `secrets.yaml` (není v gitu):
```yaml
wifi_ssid: "NazevTveSite"
wifi_password: "HesloWifi"
mqtt_broker: "192.168.x.x"   # IP adresa PC s Mosquitto
```

Vytvoř soubor `.env` (není v gitu):
```
MQTT_BROKER=192.168.x.x
MQTT_PORT=1883
```

### 3. Flash ESP32

```bash
esphome run desk_esp32.yaml
```

ESP32 musí být připojen přes USB. Po úspěšném flashování se připojí k WiFi a MQTT.

### 4. Kalibrace senzoru

Po namontování HC-SR04 je nutné nastavit `sensor_offset` v `desk_esp32.yaml`:

```yaml
globals:
  - id: sensor_offset
    type: float
    initial_value: '8.0'   # upravit dle kalibrace
```

Postup kalibrace:
1. Nastav stůl na známou výšku (změř metrem od podlahy k vrchní hraně desky)
2. Spusť MQTT monitor: `mosquitto_sub -h localhost -t "desk/height"`
3. Porovnej zobrazenou výšku se skutečnou
4. Uprav `sensor_offset` o rozdíl a přeflashuj

### 5. Spuštění všech služeb

```bash
start.bat
```

Nebo ručně:
```bash
# Terminal 1 — Mosquitto
mosquitto

# Terminal 2 — Flask backend
python app.py

# Terminal 3 — ESPHome dashboard (volitelně)
esphome dashboard .
```

Webové UI: **http://localhost:5000**

---

## Konfigurace

### Přednastavené polohy

Edituj `config.json` nebo použij tlačítko "Uložit aktuální výšku" v UI:
```json
{
  "presets": {
    "sit": 77,
    "stand": 117
  }
}
```

> Hodnoty jsou cílové výšky v cm odesílané do ESP32. Skutečná výška zastavení se může mírně lišit (±2 cm) v závislosti na kalibraci.

### Maximální výška

V `desk_esp32.yaml` uprav `max_height` dle svého stolu (výchozí 117 cm):
```yaml
  - id: max_height
    type: float
    initial_value: '117.0'
```

---

## Architektura

```
[Prohlížeč / UI]
       |  HTTP polling (300 ms)
       ↓
[Flask app.py :5000]
       |  paho-mqtt
       ↓
[Mosquitto MQTT broker :1883]
       |  WiFi / MQTT
       ↓
[ESP32 + ESPHome]
       |  GPIO25 / GPIO26
       ↓
[Relé modul]
       |  RJ45
       ↓
[IKEA RODULF motor]
       ↑
[HC-SR04] → GPIO18/19 → ESP32 → desk/height topic → Flask → UI
```

### MQTT topics

| Topic | Směr | Obsah |
|-------|------|-------|
| `desk/command` | PC → ESP32 | `up` / `down` / `stop` |
| `desk/target` | PC → ESP32 | cílová výška v cm (float) |
| `desk/height` | ESP32 → PC | aktuální výška v cm |
| `desk/move` | ESP32 → PC | `moving_up` / `moving_down` / `idle` |

---

## Řešení problémů

| Symptom | Příčina | Řešení |
|---------|---------|--------|
| UI ukazuje OFFLINE | Mosquitto neběží nebo špatná IP v `.env` | Zkontroluj `MQTT_BROKER` v `.env` |
| Výška se nezobrazuje | ESP32 není připojen k WiFi/MQTT | Zkontroluj `secrets.yaml`, přeflashuj |
| Stůl se nehýbe | Relé není správně zapojeno nebo chybí optočlen | Zkontroluj zapojení a typ relé modulu |
| Výška čte nesmyslné hodnoty | Senzor zastíněn nebo špatně umístěn | Přemísti HC-SR04, viz sekce Umístění senzoru |
| ⚠ zastíněno v UI | Senzor nevidí podlahu >10 s | Odstraň překážku před senzorem |

---

## Soubory v projektu

```
desk_ctrl/
├── app.py              # Flask backend + MQTT klient
├── desk_esp32.yaml     # ESPHome firmware pro ESP32
├── config.json         # Přednastavené polohy (generováno)
├── requirements.txt    # Python závislosti
├── start.bat           # Spouštěč všech služeb (Windows)
├── secrets.yaml        # WiFi + MQTT přihlašovací údaje (není v gitu)
├── .env                # Proměnné prostředí pro Flask (není v gitu)
├── .gitignore
└── static/
    └── index.html      # Webové UI
```
