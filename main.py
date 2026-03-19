"""
DeskControl v3 — RODULF Sit/Stand Desk Controller

Hybrid: live sensor (HC-SR04 @ 10 Hz) when fresh, dead reckoning as fallback.
Automatic velocity profiling on every movement → speed + accel/decel model.
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import paho.mqtt.client as mqtt
import threading
import time
import os
import sqlite3
import logging
from dotenv import load_dotenv

load_dotenv()

# ── Audit log ─────────────────────────────────────────────────────
LOG_FILE = "desk_audit.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("desk")

app = Flask(__name__, static_folder="static")
CORS(app)

MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT   = int(os.environ.get("MQTT_PORT", 1883))

DB_FILE        = "desk_state.db"
HEIGHT_MIN     = 70.0
HEIGHT_MAX     = 116.5
DEADBAND       = 0.5   # cm — goto stop tolerance
STALE_SEC      = 2.0   # sensor older than this → use dead reckoning

# ── In-memory state ───────────────────────────────────────────────
_lock      = threading.Lock()
_prof_lock = threading.Lock()

sensor = {"height": None, "last_seen": None}   # updated by MQTT

movement = {
    "direction":    None,   # "up" | "down" | None
    "start_time":   None,
    "start_height": None,
}

# Live profile recording (filled while desk moves)
prof = {
    "active":    False,
    "direction": None,
    "samples":   [],        # [(t_relative_s, h_cm)]
    "start_t":   None,
}

# Last finalized profile — sent to frontend for graph
last_profile = None   # dict set by finalize_profile()

mqtt_connected = False
_goto_gen      = 0
_goto_gen_lock = threading.Lock()


# ── Database ──────────────────────────────────────────────────────
def init_db():
    with sqlite3.connect(DB_FILE) as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS desk_pos (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                height     REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS presets (
                name   TEXT PRIMARY KEY,
                height REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profiles (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                direction    TEXT NOT NULL,
                start_h      REAL,
                end_h        REAL,
                duration     REAL,
                speed        REAL,
                accel_time   REAL,
                decel_time   REAL,
                sample_count INTEGER,
                created_at   REAL NOT NULL
            );
        """)
        c.execute("INSERT OR IGNORE INTO desk_pos VALUES (1,?,?)", (HEIGHT_MIN, time.time()))
        c.execute("INSERT OR IGNORE INTO presets VALUES ('min', 70.0)")
        c.execute("INSERT OR IGNORE INTO presets VALUES ('max', 116.5)")
        c.commit()


def db_get_height():
    with sqlite3.connect(DB_FILE) as c:
        r = c.execute("SELECT height FROM desk_pos WHERE id=1").fetchone()
        return float(r[0]) if r else HEIGHT_MIN


def db_set_height(h):
    h = round(max(HEIGHT_MIN, min(HEIGHT_MAX, h)), 1)
    with sqlite3.connect(DB_FILE) as c:
        c.execute("INSERT OR REPLACE INTO desk_pos VALUES (1,?,?)", (h, time.time()))
        c.commit()
    return h


def db_save_profile(direction, start_h, end_h, duration, speed, accel_t, decel_t, n):
    with sqlite3.connect(DB_FILE) as c:
        c.execute("""
            INSERT INTO profiles
              (direction,start_h,end_h,duration,speed,accel_time,decel_time,sample_count,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (direction, start_h, end_h, duration, speed, accel_t, decel_t, n, time.time()))
        c.commit()


def db_get_profiles():
    with sqlite3.connect(DB_FILE) as c:
        rows = c.execute("""
            SELECT id,direction,start_h,end_h,duration,speed,accel_time,decel_time,sample_count,created_at
            FROM profiles ORDER BY created_at DESC LIMIT 30
        """).fetchall()
    keys = ("id","direction","start_h","end_h","duration","speed","accel_time","decel_time","sample_count","created_at")
    return [dict(zip(keys, r)) for r in rows]


def db_delete_profile(pid):
    with sqlite3.connect(DB_FILE) as c:
        c.execute("DELETE FROM profiles WHERE id=?", (pid,))
        c.commit()


def db_clear_profiles():
    with sqlite3.connect(DB_FILE) as c:
        c.execute("DELETE FROM profiles")
        c.commit()


def db_get_presets():
    with sqlite3.connect(DB_FILE) as c:
        rows = c.execute("SELECT name,height FROM presets").fetchall()
        return {r[0]: r[1] for r in rows}


def db_set_preset(name, height):
    with sqlite3.connect(DB_FILE) as c:
        c.execute("INSERT OR REPLACE INTO presets VALUES (?,?)", (name, height))
        c.commit()


def db_delete_preset(name):
    with sqlite3.connect(DB_FILE) as c:
        c.execute("DELETE FROM presets WHERE name=?", (name,))
        c.commit()


# ── Motion model (derived from stored profiles) ───────────────────
def get_model():
    """
    Derive speed as dist/duration (more accurate than fitted plateau).
    Accel/decel from profiler average.
    Only uses profiles with dist >= 5 cm to avoid short-move noise.
    """
    profiles = db_get_profiles()
    result = {}
    for d in ("up", "down"):
        pts = [p for p in profiles
               if p["direction"] == d
               and p["start_h"] is not None and p["end_h"] is not None
               and p["duration"] is not None and p["duration"] > 0
               and abs(p["end_h"] - p["start_h"]) >= 5.0]
        if pts:
            speeds = [abs(p["end_h"] - p["start_h"]) / p["duration"] for p in pts]
            accels = [p["accel_time"] for p in pts if p["accel_time"] is not None]
            decels = [p["decel_time"] for p in pts if p["decel_time"] is not None]
            result[d] = {
                "speed":      round(sum(speeds) / len(speeds), 3),
                "accel_time": round(sum(accels) / len(accels), 3) if accels else 0.3,
                "decel_time": round(sum(decels) / len(decels), 3) if decels else 0.2,
                "count":      len(pts),
            }
        else:
            result[d] = None
    return result


# ── Velocity profiler ─────────────────────────────────────────────
def _velocities(samples):
    """[(t_mid, v_cm_per_s)] from consecutive (t, h) samples."""
    vels = []
    for i in range(1, len(samples)):
        dt = samples[i][0] - samples[i-1][0]
        if dt > 0.01:
            v = abs(samples[i][1] - samples[i-1][1]) / dt
            vels.append(((samples[i][0] + samples[i-1][0]) / 2, round(v, 3)))
    return vels


def _fit(samples):
    """
    Returns (speed, accel_time, decel_time) or None.
    speed      = plateau velocity (cm/s)
    accel_time = seconds from start until plateau reached
    decel_time = seconds from plateau drop to end
    """
    if len(samples) < 6:
        return None
    vels = _velocities(samples)
    if len(vels) < 4:
        return None

    vs = [v for _, v in vels]
    n  = len(vs)

    # Plateau = median of middle half
    mid_vs = sorted(vs[n // 4: 3 * n // 4])
    if not mid_vs:
        return None
    speed = mid_vs[len(mid_vs) // 2]
    if speed < 0.1:
        return None

    threshold = 0.80 * speed

    # Acceleration: first moment velocity crosses threshold
    accel_time = vels[0][0]
    for t, v in vels:
        if v >= threshold:
            accel_time = t
            break

    # Deceleration start: last moment velocity is above threshold
    decel_start = vels[-1][0]
    for t, v in reversed(vels):
        if v >= threshold:
            decel_start = t
            break

    total_time = samples[-1][0]
    decel_time = max(0.0, total_time - decel_start)

    return (round(speed, 3), round(max(0.0, accel_time), 3), round(decel_time, 3))


def finalize_profile():
    global last_profile
    with _prof_lock:
        samples   = list(prof["samples"])
        direction = prof["direction"]

    if len(samples) < 6:
        log.warning(f"PROFILE too short  dir={direction}  n={len(samples)}  — discarded")
        return

    start_h  = samples[0][1]
    end_h    = samples[-1][1]
    dist     = abs(end_h - start_h)
    duration = round(samples[-1][0], 3)
    vels     = _velocities(samples)
    fit      = _fit(samples)

    speed = accel_t = decel_t = None
    if fit:
        speed, accel_t, decel_t = fit

    db_save_profile(direction, start_h, end_h, duration, speed, accel_t, decel_t, len(samples))

    # ── Detect relay-click-without-movement ───────────────────────
    if dist < 0.5 and duration > 0.4:
        log.warning(
            f"NO_MOVEMENT  dir={direction}  start={start_h:.1f}  end={end_h:.1f}"
            f"  dist={dist:.2f}cm  dur={duration:.2f}s  n={len(samples)}"
            f"  — relay activated but desk did not move"
        )
    else:
        log.info(
            f"PROFILE  dir={direction}  {start_h:.1f}->{end_h:.1f}cm"
            f"  dist={dist:.1f}cm  dur={duration:.2f}s"
            f"  speed={speed}cm/s  accel={accel_t}s  n={len(samples)}"
        )

    last_profile = {
        "direction":  direction,
        "samples":    samples,
        "velocities": vels,
        "speed":      speed,
        "accel_time": accel_t,
        "decel_time": decel_t,
        "duration":   duration,
        "start_h":    start_h,
        "end_h":      end_h,
    }


# ── Height estimate ───────────────────────────────────────────────
def sensor_fresh():
    s = sensor["last_seen"]
    return s is not None and (time.time() - s) < STALE_SEC


def _dr_from_movement(direction, start_time, start_height):
    """Pure dead-reckoning calculation given movement state."""
    elapsed = time.time() - start_time
    model   = get_model()
    m       = model.get(direction)
    if m:
        accel_t = m["accel_time"]
        speed   = m["speed"]
        if elapsed <= accel_t and accel_t > 0:
            delta = 0.5 * speed * (elapsed ** 2) / accel_t
        else:
            accel_dist = 0.5 * speed * accel_t
            delta = accel_dist + (elapsed - accel_t) * speed
    else:
        delta = elapsed * (HEIGHT_MAX - HEIGHT_MIN) / 22.0
    h = start_height + delta if direction == "up" else start_height - delta
    return round(max(HEIGHT_MIN, min(HEIGHT_MAX, h)), 1)


def compute_dr_height():
    """Always returns dead-reckoning height (never sensor). Used for comparison."""
    with _lock:
        direction    = movement["direction"]
        start_time   = movement["start_time"]
        start_height = movement["start_height"]
    if direction is not None:
        return _dr_from_movement(direction, start_time, start_height)
    return db_get_height()


def compute_height():
    """Best estimate: DR during movement, sensor when idle+fresh, else DB."""
    with _lock:
        direction    = movement["direction"]
        start_time   = movement["start_time"]
        start_height = movement["start_height"]

    if direction is not None:
        return _dr_from_movement(direction, start_time, start_height)

    # Idle: prefer live sensor
    if sensor_fresh():
        return round(sensor["height"], 1)
    return db_get_height()


def start_movement(direction):
    h = compute_height()
    with _lock:
        movement["direction"]    = direction
        movement["start_time"]   = time.time()
        movement["start_height"] = h
    log.info(f"MOVE_START  dir={direction}  height={h:.1f}cm")


def stop_movement():
    # Prefer live sensor for final position — prevents DR drift accumulation
    with _lock:
        s_h = sensor["height"]
        s_t = sensor["last_seen"]
        prev_dir = movement["direction"]
        prev_h   = movement["start_height"]
        movement["direction"]    = None
        movement["start_time"]   = None
        movement["start_height"] = None
    if s_h is not None and s_t is not None and (time.time() - s_t) < 1.5:
        h = round(s_h, 1)
    else:
        h = compute_height()
    db_set_height(h)
    if prev_h is not None:
        delta = h - prev_h
        log.info(f"MOVE_STOP   dir={prev_dir}  from={prev_h:.1f}  to={h:.1f}  delta={delta:+.1f}cm")
    return h


# ── MQTT ──────────────────────────────────────────────────────────
mqtt_client = mqtt.Client()


def on_connect(client, userdata, flags, rc):
    global mqtt_connected
    mqtt_connected = (rc == 0)
    if rc == 0:
        client.subscribe([("desk/height", 0), ("desk/move", 0)])
        log.info(f"MQTT connected  broker={MQTT_BROKER}:{MQTT_PORT}")
    else:
        log.error(f"MQTT connect failed  rc={rc}")


def on_disconnect(client, userdata, rc):
    global mqtt_connected
    mqtt_connected = False
    log.warning(f"MQTT disconnected  rc={rc}")


def on_message(client, userdata, msg):
    topic   = msg.topic
    payload = msg.payload.decode().strip()

    if topic == "desk/height":
        try:
            h = float(payload)
            sensor["height"]    = h
            sensor["last_seen"] = time.time()
            with _prof_lock:
                if prof["active"] and prof["start_t"] is not None:
                    t = time.time() - prof["start_t"]
                    prof["samples"].append((round(t, 4), round(h, 2)))
        except ValueError:
            pass

    elif topic == "desk/move":
        if payload in ("moving_up", "moving_down"):
            direction = "up" if "up" in payload else "down"
            with _prof_lock:
                prof["active"]    = True
                prof["direction"] = direction
                prof["samples"]   = []
                prof["start_t"]   = time.time()
        elif payload == "idle":
            with _prof_lock:
                was_active = prof["active"]
                prof["active"] = False
            if was_active:
                threading.Thread(target=finalize_profile, daemon=True).start()


mqtt_client.on_connect    = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message    = on_message


def mqtt_loop():
    while True:
        if not mqtt_connected:
            try:
                mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
                mqtt_client.loop_start()
            except Exception as e:
                print(f"[MQTT] Cannot connect: {e}")
        time.sleep(5)


threading.Thread(target=mqtt_loop, daemon=True).start()


# ── Goto worker ───────────────────────────────────────────────────
def goto_worker(target, my_gen):
    current = compute_height()
    log.info(f"GOTO_START  target={target:.1f}cm  current={current:.1f}cm")

    if abs(current - target) < DEADBAND:
        log.info(f"GOTO_SKIP   already at target ({current:.1f} ≈ {target:.1f})")
        return

    direction = "up" if target > current else "down"
    start_movement(direction)
    mqtt_client.publish("desk/command", direction)

    while True:
        with _goto_gen_lock:
            if _goto_gen != my_gen:
                log.info(f"GOTO_CANCEL  superseded by newer goto  target={target:.1f}")
                return          # cancelled — do NOT send stop, new goto handles it
        # Prefer live sensor during movement — DR is for display only
        with _lock:
            s_h = sensor["height"]
            s_t = sensor["last_seen"]
        if s_h is not None and s_t is not None and (time.time() - s_t) < 1.0:
            current = round(s_h, 1)
        else:
            current = compute_height()
        if abs(current - target) < DEADBAND:
            break
        if direction == "up"   and current >= target:
            break
        if direction == "down" and current <= target:
            break
        if current >= HEIGHT_MAX or current <= HEIGHT_MIN:
            log.warning(f"GOTO_LIMIT  hit limit at {current:.1f}cm  target={target:.1f}")
            break
        time.sleep(0.1)

    mqtt_client.publish("desk/command", "stop")
    final_h = stop_movement()
    err = abs(final_h - target)
    log.info(f"GOTO_DONE   target={target:.1f}  actual={final_h:.1f}  error={err:.1f}cm")

    # ── settle & correct ───────────────────────────────────────────
    # Wait for sensor to stabilise after motor stops (vibration + median filter lag)
    time.sleep(1.5)

    with _goto_gen_lock:
        if _goto_gen != my_gen:
            return  # superseded by new goto — skip correction

    with _lock:
        s_h = sensor["height"]
        s_t = sensor["last_seen"]
    if s_h is None or s_t is None or (time.time() - s_t) > 2.0:
        log.warning("GOTO_SETTLE  no fresh sensor — skipping correction")
        return

    err_settled = abs(s_h - target)
    if err_settled <= DEADBAND:
        log.info(f"GOTO_SETTLE  height={s_h:.1f}  target={target:.1f}  err={err_settled:.1f}cm  OK")
        return

    log.info(f"GOTO_CORRECT  height={s_h:.1f}  target={target:.1f}  err={err_settled:.1f}cm — correcting")
    corr_dir = "up" if target > s_h else "down"
    start_movement(corr_dir)
    mqtt_client.publish("desk/command", corr_dir)

    for _ in range(100):  # max 10s correction
        with _goto_gen_lock:
            if _goto_gen != my_gen:
                return
        with _lock:
            c_h = sensor["height"]
            c_t = sensor["last_seen"]
        current = round(c_h, 1) if (c_h is not None and c_t is not None and (time.time() - c_t) < 1.0) else compute_height()
        if abs(current - target) < DEADBAND:
            break
        if corr_dir == "up"   and current >= target:
            break
        if corr_dir == "down" and current <= target:
            break
        time.sleep(0.1)

    mqtt_client.publish("desk/command", "stop")
    final_h2 = stop_movement()
    log.info(f"GOTO_CORRECTED  target={target:.1f}  actual={final_h2:.1f}  err={abs(final_h2-target):.1f}cm")


def start_goto(target):
    global _goto_gen
    with _goto_gen_lock:
        _goto_gen += 1
        gen = _goto_gen
    threading.Thread(target=goto_worker, args=(target, gen), daemon=True).start()


# ── API ───────────────────────────────────────────────────────────
@app.route("/api/state")
def get_state():
    h = compute_height()
    with _lock:
        direction = movement["direction"]
    status = "idle" if direction is None else f"moving_{direction}"
    return jsonify({
        "height":         h,
        "sensor_height":  round(sensor["height"], 1) if sensor["height"] else None,
        "sensor_fresh":   sensor_fresh(),
        "dr_height":      compute_dr_height(),
        "status":         status,
        "mqtt_connected": mqtt_connected,
        "presets":        db_get_presets(),
        "model":          get_model(),
    })


@app.route("/api/command", methods=["POST"])
def send_command():
    data = request.get_json()
    cmd  = data.get("command")
    if cmd not in ("up", "down", "stop"):
        return jsonify({"error": "Invalid command"}), 400
    if not mqtt_connected:
        return jsonify({"error": "MQTT not connected"}), 503
    # cancel any active goto
    global _goto_gen
    with _goto_gen_lock:
        _goto_gen += 1
    if cmd in ("up", "down"):
        start_movement(cmd)
    else:
        stop_movement()
    mqtt_client.publish("desk/command", cmd)
    return jsonify({"ok": True})


@app.route("/api/goto", methods=["POST"])
def goto_height():
    data = request.get_json()
    try:
        target = float(data.get("height"))
        if not (HEIGHT_MIN <= target <= HEIGHT_MAX):
            return jsonify({"error": f"Height must be {HEIGHT_MIN}–{HEIGHT_MAX} cm"}), 400
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid height"}), 400
    if not mqtt_connected:
        return jsonify({"error": "MQTT not connected"}), 503
    start_goto(target)
    return jsonify({"ok": True, "target": target})


@app.route("/api/set_height", methods=["POST"])
def set_height_manual():
    data = request.get_json()
    try:
        h = float(data.get("height"))
        if not (HEIGHT_MIN <= h <= HEIGHT_MAX):
            return jsonify({"error": f"Height must be {HEIGHT_MIN}–{HEIGHT_MAX} cm"}), 400
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid height"}), 400
    global _goto_gen
    with _goto_gen_lock:
        _goto_gen += 1
    with _lock:
        movement["direction"] = None
    db_set_height(h)
    return jsonify({"ok": True, "height": h})


@app.route("/api/profiles", methods=["GET"])
def get_profiles():
    return jsonify({
        "profiles":     db_get_profiles(),
        "model":        get_model(),
        "last_profile": last_profile,
    })


@app.route("/api/profiles/<int:pid>", methods=["DELETE"])
def delete_profile(pid):
    db_delete_profile(pid)
    return jsonify({"ok": True})


@app.route("/api/profiles", methods=["DELETE"])
def clear_profiles():
    db_clear_profiles()
    return jsonify({"ok": True})


@app.route("/api/presets", methods=["GET"])
def get_presets():
    return jsonify(db_get_presets())


@app.route("/api/presets", methods=["POST"])
def save_presets():
    data = request.get_json()
    for key, val in data.items():
        try:
            val = float(val)
            if HEIGHT_MIN <= val <= HEIGHT_MAX:
                db_set_preset(key, val)
        except (TypeError, ValueError):
            pass
    return jsonify({"ok": True, "presets": db_get_presets()})


@app.route("/api/presets/save_current", methods=["POST"])
def save_current_as_preset():
    data = request.get_json()
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Missing preset name"}), 400
    h = compute_height()
    db_set_preset(name, h)
    return jsonify({"ok": True, "name": name, "height": h})


@app.route("/api/presets/<name>", methods=["DELETE"])
def delete_preset(name):
    if name in ("min", "max"):
        return jsonify({"error": "Default presets cannot be deleted"}), 400
    db_delete_preset(name)
    return jsonify({"ok": True})


@app.route("/")
def index():
    return send_from_directory("static", "index2.html")


if __name__ == "__main__":
    init_db()
    print("DeskControl v3  ->  http://localhost:5001")
    app.run(host="0.0.0.0", port=5001, debug=False)
