"""Flask dashboard for greenhouse controller."""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

from flask import Flask, render_template, request, jsonify

# Add project root to path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-key-change-in-production")

# DB path is relative to project root
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), config.DB_PATH)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_settings(conn):
    """Create settings table and insert defaults if needed."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT OR IGNORE INTO settings VALUES ('hvac_heat_setpoint', '60', ?)", (now,))
    conn.execute("INSERT OR IGNORE INTO settings VALUES ('hvac_cool_setpoint', '80', ?)", (now,))
    conn.commit()


def load_settings(conn):
    try:
        ensure_settings(conn)
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    except Exception:
        return {"hvac_heat_setpoint": "60", "hvac_cool_setpoint": "80"}


def controller_online(heartbeat_ts):
    """Return True if heartbeat is within the last 10 minutes."""
    if not heartbeat_ts:
        return False
    try:
        ts = datetime.fromisoformat(heartbeat_ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds() < 600
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    return render_template("dashboard.html", page="greenhouse")


@app.route("/history")
def history():
    return render_template("history.html", page="history")


@app.route("/energy")
def energy():
    return render_template("energy.html", page="energy")


@app.route("/diagnostic")
def diagnostic():
    return render_template("diagnostic.html", page="diagnostic")


# ---------------------------------------------------------------------------
# API: current state
# ---------------------------------------------------------------------------

@app.route("/api/state")
def api_state():
    try:
        conn = get_db()

        sensor = conn.execute(
            "SELECT * FROM sensor_log ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

        power = conn.execute(
            "SELECT * FROM power_log ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

        heartbeat = conn.execute(
            "SELECT timestamp FROM heartbeat WHERE id = 1"
        ).fetchone()

        overrides = conn.execute(
            """SELECT actuator, command, created_at, expires_at
               FROM overrides
               WHERE expires_at > datetime('now') AND cancelled_at IS NULL
               ORDER BY created_at DESC"""
        ).fetchall()

        # Latest forecast for weather code + 2hr prediction
        forecast_row = conn.execute(
            "SELECT corrected_forecast FROM forecast_log ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

        settings = load_settings(conn)
        conn.close()

        hb_ts = heartbeat["timestamp"] if heartbeat else None
        state = {
            "controller_online": controller_online(hb_ts),
            "controller_last_seen": hb_ts,
            "settings": settings,
            "overrides": [dict(r) for r in overrides],
        }

        if sensor:
            state.update({
                "indoor_temp": sensor["indoor_temp_f"],
                "indoor_humidity": sensor["indoor_humidity"],
                "outdoor_temp": sensor["outdoor_temp_f"],
                "outdoor_humidity": sensor["outdoor_humidity"],
                "solar_irradiance": sensor["solar_irradiance_wm2"],
                "wind_speed": sensor["wind_speed_mph"],
                "shades_east": sensor["shades_east"],
                "shades_west": sensor["shades_west"],
                "fan_on": bool(sensor["fan_on"]),
                "circ_fans_on": bool(sensor["circ_fans_on"]),
                "hvac_mode": sensor["hvac_mode"],
                "hvac_setpoint": sensor["hvac_setpoint"],
                "timestamp": sensor["timestamp"],
            })

        if power:
            state.update({
                "power_kw": power["power_total_kw"],
                "power_a_kw": power["power_a_kw"],
                "power_b_kw": power["power_b_kw"],
                "current_a": power["current_a_a"],
                "voltage_v": power["voltage_a_v"],
            })

        if forecast_row:
            try:
                fc = json.loads(forecast_row["corrected_forecast"])
                state["forecast"] = _extract_forecast_summary(fc)
            except Exception:
                pass

        return jsonify(state)

    except Exception as e:
        return jsonify({"error": str(e), "controller_online": False}), 500


def _extract_forecast_summary(fc):
    """Pull current + 2hr conditions from corrected forecast dict."""
    try:
        times = fc.get("hourly", {}).get("time", [])
        temps = fc.get("hourly", {}).get("temperature_2m", [])
        codes = fc.get("hourly", {}).get("weathercode", [])
        now = datetime.now(timezone.utc)

        def find_idx(offset_h):
            target = now + timedelta(hours=offset_h)
            target_str = target.strftime("%Y-%m-%dT%H:00")
            for i, t in enumerate(times):
                if t.startswith(target_str[:13]):
                    return i
            return None

        cur_idx = find_idx(0)
        fwd_idx = find_idx(2)

        summary = {}
        if cur_idx is not None:
            summary["current_code"] = codes[cur_idx] if cur_idx < len(codes) else None
            summary["current_temp"] = temps[cur_idx] if cur_idx < len(temps) else None
        if fwd_idx is not None:
            summary["forecast_2h_code"] = codes[fwd_idx] if fwd_idx < len(codes) else None
            summary["forecast_2h_temp"] = temps[fwd_idx] if fwd_idx < len(temps) else None

        return summary
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# API: historical sensor data
# ---------------------------------------------------------------------------

RANGE_MAP = {
    "1h":  "-1 hours",
    "24h": "-24 hours",
    "7d":  "-7 days",
    "30d": "-30 days",
    "1y":  "-1 year",
}


@app.route("/api/history")
def api_history():
    range_param = request.args.get("range", "24h")
    interval = RANGE_MAP.get(range_param, "-24 hours")
    try:
        conn = get_db()
        rows = conn.execute(
            f"""SELECT timestamp, indoor_temp_f, outdoor_temp_f,
                       indoor_humidity, outdoor_humidity,
                       solar_irradiance_wm2, shades_east, shades_west,
                       fan_on, circ_fans_on, hvac_mode
                FROM sensor_log
                WHERE timestamp > datetime('now', '{interval}')
                ORDER BY timestamp ASC"""
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: model accuracy (diagnostic)
# ---------------------------------------------------------------------------

@app.route("/api/model_accuracy")
def api_model_accuracy():
    range_param = request.args.get("range", "24h")
    interval = RANGE_MAP.get(range_param, "-24 hours")
    try:
        conn = get_db()
        rows = conn.execute(
            f"""SELECT timestamp, predicted_temp_f, actual_temp_f, error_f
                FROM model_accuracy
                WHERE timestamp > datetime('now', '{interval}')
                ORDER BY timestamp ASC"""
        ).fetchall()

        # Also fetch model predictions for overlay
        predictions = conn.execute(
            f"""SELECT timestamp, predicted_trajectory
                FROM model_log
                WHERE timestamp > datetime('now', '{interval}')
                ORDER BY timestamp ASC"""
        ).fetchall()
        conn.close()

        return jsonify({
            "accuracy": [dict(r) for r in rows],
            "predictions": [dict(r) for r in predictions],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: power data
# ---------------------------------------------------------------------------

@app.route("/api/power")
def api_power():
    range_param = request.args.get("range", "24h")
    interval = RANGE_MAP.get(range_param, "-24 hours")
    try:
        conn = get_db()
        rows = conn.execute(
            f"""SELECT timestamp, power_a_kw, power_b_kw, power_total_kw,
                       current_a_a, voltage_a_v, energy_a_kwh, energy_b_kwh, energy_total_kwh
                FROM power_log
                WHERE timestamp > datetime('now', '{interval}')
                ORDER BY timestamp ASC"""
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: overrides
# ---------------------------------------------------------------------------

@app.route("/api/override", methods=["POST"])
def api_set_override():
    data = request.json or {}
    actuator = data.get("actuator")
    command = data.get("command", {})
    duration_minutes = int(data.get("duration_minutes", 120))

    if not actuator:
        return jsonify({"error": "actuator required"}), 400

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=duration_minutes)

    try:
        conn = get_db()
        conn.execute(
            "UPDATE overrides SET cancelled_at = ? WHERE actuator = ? AND cancelled_at IS NULL",
            (now.isoformat(), actuator),
        )
        conn.execute(
            "INSERT INTO overrides (actuator, command, created_at, expires_at, source) VALUES (?,?,?,?,'dashboard')",
            (actuator, json.dumps(command), now.isoformat(), expires_at.isoformat()),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "expires_at": expires_at.isoformat()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/override/<actuator>", methods=["DELETE"])
def api_cancel_override(actuator):
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = get_db()
        conn.execute(
            "UPDATE overrides SET cancelled_at = ? WHERE actuator = ? AND cancelled_at IS NULL",
            (now, actuator),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: settings (HVAC setpoints)
# ---------------------------------------------------------------------------

@app.route("/api/settings", methods=["POST"])
def api_set_settings():
    data = request.json or {}
    allowed = {"hvac_heat_setpoint", "hvac_cool_setpoint"}
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = get_db()
        ensure_settings(conn)
        for key, value in data.items():
            if key in allowed:
                conn.execute(
                    "INSERT OR REPLACE INTO settings VALUES (?, ?, ?)",
                    (key, str(value), now),
                )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
