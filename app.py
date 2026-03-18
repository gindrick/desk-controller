"""
DeskControl - RODULF Sit/Stand Desk Controller
Flask backend + MQTT communication with ESP32
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import paho.mqtt.client as mqtt
import json
import threading
import time
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder="static")
CORS(app)

MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_TOPIC_COMMAND = "desk/command"
MQTT_TOPIC_TARGET = "desk/target"
MQTT_TOPIC_HEIGHT = "desk/height"
MQTT_TOPIC_STATUS = "desk/move"
CONFIG_FILE = "config.json"

state = {
    "height": None,
    "status": "idle",
    "mqtt_connected": False,
    "last_update": None,
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {"presets": {"sit": 75, "stand": 110}}

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

config = load_config()

mqtt_client = mqtt.Client()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        state["mqtt_connected"] = True
        client.subscribe(MQTT_TOPIC_HEIGHT)
        client.subscribe(MQTT_TOPIC_STATUS)
        print(f"[MQTT] Connected to {MQTT_BROKER}:{MQTT_PORT}")
    else:
        state["mqtt_connected"] = False
        print(f"[MQTT] Connection error: {rc}")

def on_disconnect(client, userdata, rc):
    state["mqtt_connected"] = False
    print("[MQTT] Disconnected")

def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode("utf-8").strip()
    if topic == MQTT_TOPIC_HEIGHT:
        try:
            state["height"] = round(float(payload), 1)
            state["last_update"] = time.time()
        except ValueError:
            pass
    elif topic == MQTT_TOPIC_STATUS:
        state["status"] = payload

mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message = on_message

def mqtt_connect_loop():
    while True:
        if not state["mqtt_connected"]:
            try:
                mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
                mqtt_client.loop_start()
            except Exception as e:
                print(f"[MQTT] Cannot connect: {e}")
        time.sleep(5)

threading.Thread(target=mqtt_connect_loop, daemon=True).start()

@app.route("/api/state")
def get_state():
    return jsonify({
        "height": state["height"],
        "status": state["status"],
        "mqtt_connected": state["mqtt_connected"],
        "presets": config["presets"],
        "last_update": state["last_update"],
    })

@app.route("/api/command", methods=["POST"])
def send_command():
    data = request.get_json()
    cmd = data.get("command")
    if cmd not in ("up", "down", "stop"):
        return jsonify({"error": "Invalid command"}), 400
    if not state["mqtt_connected"]:
        return jsonify({"error": "MQTT not connected"}), 503
    mqtt_client.publish(MQTT_TOPIC_COMMAND, cmd)
    return jsonify({"ok": True, "command": cmd})

@app.route("/api/goto", methods=["POST"])
def goto_height():
    data = request.get_json()
    target = data.get("height")
    try:
        target = float(target)
        if not (70 <= target <= 117):
            return jsonify({"error": "Height must be 70-117 cm"}), 400
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid height"}), 400
    if not state["mqtt_connected"]:
        return jsonify({"error": "MQTT not connected"}), 503
    mqtt_client.publish(MQTT_TOPIC_TARGET, str(target))
    return jsonify({"ok": True, "target": target})

@app.route("/api/presets", methods=["GET"])
def get_presets():
    return jsonify(config["presets"])

@app.route("/api/presets", methods=["POST"])
def save_presets():
    data = request.get_json()
    for key, val in data.items():
        try:
            val = float(val)
            if 70 <= val <= 117:
                config["presets"][key] = val
        except (TypeError, ValueError):
            pass
    save_config(config)
    return jsonify({"ok": True, "presets": config["presets"]})

@app.route("/api/presets/save_current", methods=["POST"])
def save_current_as_preset():
    data = request.get_json()
    name = data.get("name", "").strip()
    height = state["height"]
    if not name:
        return jsonify({"error": "Missing preset name"}), 400
    if height is None:
        return jsonify({"error": "Height unknown"}), 400
    config["presets"][name] = height
    save_config(config)
    return jsonify({"ok": True, "name": name, "height": height})

@app.route("/api/presets/<name>", methods=["DELETE"])
def delete_preset(name):
    if name in ("sit", "stand"):
        return jsonify({"error": "Default presets cannot be deleted"}), 400
    config["presets"].pop(name, None)
    save_config(config)
    return jsonify({"ok": True})

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

if __name__ == "__main__":
    print("DeskControl running at http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)