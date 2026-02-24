"""Flask dashboard for greenhouse controller."""

import json
import os
import sqlite3
import sys
import traceback
from datetime import datetime, timezone, timedelta

from flask import Flask, render_template, request, jsonify

# Add project root to path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from devices.shelly_relay import ShellyRelay
from devices.kasa_switch import KasaSwitch

exhaust_fan_relay = ShellyRelay(config.SHELLY_RELAY_IP, name="exhaust_fans")
circ_fan_switch   = KasaSwitch(config.KASA_CIRC_FANS_IP)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-key-change-in-production")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # Disable static file caching during development

# DB path is relative to project root
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), config.DB_PATH)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_overrides(conn):
    """Create overrides table if needed (web app owns this table)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actuator TEXT NOT NULL,
            command TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            source TEXT NOT NULL,
            cancelled_at TEXT
        )
    """)
    conn.commit()


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

        try:
            sensor = conn.execute(
                "SELECT * FROM sensor_log ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        except Exception:
            sensor = None

        try:
            power = conn.execute(
                "SELECT * FROM power_log ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        except Exception:
            power = None

        try:
            heartbeat = conn.execute(
                "SELECT timestamp FROM heartbeat WHERE id = 1"
            ).fetchone()
        except Exception:
            heartbeat = None

        try:
            ensure_overrides(conn)
            overrides = conn.execute(
                """SELECT actuator, command, created_at, expires_at
                   FROM overrides
                   WHERE expires_at > datetime('now') AND cancelled_at IS NULL
                   ORDER BY created_at DESC"""
            ).fetchall()
        except Exception:
            overrides = []

        try:
            forecast_row = conn.execute(
                "SELECT corrected_forecast FROM forecast_log ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        except Exception:
            forecast_row = None

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
            s = dict(sensor)
            state.update({
                "indoor_temp": s.get("indoor_temp_f"),
                "indoor_humidity": s.get("indoor_humidity"),
                "outdoor_temp": s.get("outdoor_temp_f"),
                "outdoor_humidity": s.get("outdoor_humidity"),
                "solar_irradiance": s.get("solar_irradiance_wm2"),
                "wind_speed": s.get("wind_speed_mph"),
                "shades_east": s.get("shades_east"),
                "shades_west": s.get("shades_west"),
                "fan_on": bool(s.get("fan_on")),
                "circ_fans_on": bool(s.get("circ_fans_on")),
                "hvac_mode": s.get("hvac_mode"),
                "hvac_setpoint": s.get("hvac_setpoint"),
                "timestamp": s.get("timestamp"),
            })

        if power:
            p = dict(power)
            va = p.get("voltage_a_v") or 0
            vb = p.get("voltage_b_v") or 0
            state.update({
                "power_kw": p.get("power_total_kw"),
                "power_a_kw": p.get("power_a_kw"),
                "power_b_kw": p.get("power_b_kw"),
                "current_a": p.get("current_a_a"),
                "voltage_v": va + vb,          # sum of both legs
                "freq_hz": p.get("freq_hz"),
            })

        if forecast_row:
            try:
                fc = json.loads(forecast_row["corrected_forecast"])
                state["forecast"] = _extract_forecast_summary(fc)
            except Exception:
                pass

        return jsonify(state)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "controller_online": False}), 500


def _extract_forecast_summary(fc):
    """Pull current + 2hr conditions from corrected forecast dict."""
    try:
        times   = fc.get("time", [])
        temps   = fc.get("temperature_f", [])
        codes   = fc.get("weather_code", [])
        is_day  = fc.get("is_day", [])
        now = datetime.now()  # local time — forecast uses timezone: "auto" (local)

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
            summary["current_code"]   = codes[cur_idx]  if cur_idx < len(codes)  else None
            summary["current_temp"]   = temps[cur_idx]  if cur_idx < len(temps)  else None
            summary["current_is_day"] = bool(is_day[cur_idx]) if cur_idx < len(is_day) else True
        if fwd_idx is not None:
            summary["forecast_2h_code"]   = codes[fwd_idx]  if fwd_idx < len(codes)  else None
            summary["forecast_2h_temp"]   = temps[fwd_idx]  if fwd_idx < len(temps)  else None
            summary["forecast_2h_is_day"] = bool(is_day[fwd_idx]) if fwd_idx < len(is_day) else True

        return summary
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# API: historical sensor data
# ---------------------------------------------------------------------------

RANGE_SECONDS = {
    "1h":  3600,
    "24h": 86400,
    "7d":  7 * 86400,
    "30d": 30 * 86400,
    "1y":  365 * 86400,
}


def time_window(range_param, offset=0):
    """Return (start_str, end_str) for SQLite BETWEEN clause.

    offset=0  → current period (now-duration to now)
    offset=-1 → previous period, etc.
    """
    secs = RANGE_SECONDS.get(range_param, 86400)
    now  = datetime.now(timezone.utc)
    end  = now + timedelta(seconds=offset * secs)
    start = end - timedelta(seconds=secs)
    fmt  = "%Y-%m-%d %H:%M:%S"
    return start.strftime(fmt), end.strftime(fmt)


@app.route("/api/history")
def api_history():
    range_param = request.args.get("range", "24h")
    offset      = int(request.args.get("offset", 0))
    start, end  = time_window(range_param, offset)
    try:
        conn = get_db()
        rows = conn.execute(
            """SELECT timestamp, indoor_temp_f, outdoor_temp_f,
                      indoor_humidity, outdoor_humidity,
                      solar_irradiance_wm2, shades_east, shades_west,
                      fan_on, circ_fans_on, hvac_mode
               FROM sensor_log
               WHERE datetime(timestamp) BETWEEN ? AND ?
               ORDER BY rowid ASC""",
            (start, end),
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
    offset      = int(request.args.get("offset", 0))
    start, end  = time_window(range_param, offset)
    try:
        conn = get_db()
        rows = conn.execute(
            """SELECT timestamp, predicted_temp_f, actual_temp_f, error_f
               FROM model_accuracy
               WHERE datetime(timestamp) BETWEEN ? AND ?
               ORDER BY rowid ASC""",
            (start, end),
        ).fetchall()

        predictions = conn.execute(
            """SELECT timestamp, predicted_trajectory
               FROM model_log
               WHERE datetime(timestamp) BETWEEN ? AND ?
               ORDER BY rowid ASC""",
            (start, end),
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
    offset      = int(request.args.get("offset", 0))
    start, end  = time_window(range_param, offset)
    try:
        conn = get_db()
        rows = conn.execute(
            """SELECT timestamp, power_a_kw, power_b_kw, power_total_kw,
                      current_a_a, voltage_a_v, energy_a_kwh, energy_b_kwh, energy_total_kwh
               FROM power_log
               WHERE datetime(timestamp) BETWEEN ? AND ?
               ORDER BY rowid ASC""",
            (start, end),
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: solar forecast vs actual (diagnostic)
# ---------------------------------------------------------------------------

@app.route("/api/solar_forecast")
def api_solar_forecast():
    range_param = request.args.get("range", "24h")
    offset      = int(request.args.get("offset", 0))
    start, end  = time_window(range_param, offset)
    try:
        conn = get_db()
        actual = conn.execute(
            """SELECT timestamp, solar_irradiance_wm2
               FROM sensor_log
               WHERE datetime(timestamp) BETWEEN ? AND ?
                 AND solar_irradiance_wm2 IS NOT NULL
               ORDER BY rowid ASC""",
            (start, end),
        ).fetchall()
        forecasts = conn.execute(
            """SELECT timestamp, corrected_forecast
               FROM forecast_log
               WHERE datetime(timestamp) BETWEEN ? AND ?
               ORDER BY rowid ASC""",
            (start, end),
        ).fetchall()
        conn.close()

        forecast_pts = []
        for row in forecasts:
            try:
                fc = json.loads(row["corrected_forecast"])
                times = fc.get("hourly", {}).get("time", [])
                solar = fc.get("hourly", {}).get("direct_radiation", []) or \
                        fc.get("hourly", {}).get("shortwave_radiation", [])
                for i, t in enumerate(times):
                    if i < len(solar) and solar[i] is not None:
                        forecast_pts.append({"timestamp": t, "solar_wm2": solar[i]})
            except Exception:
                pass

        return jsonify({
            "actual": [dict(r) for r in actual],
            "forecast": forecast_pts,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: actuator timeline (diagnostic)
# ---------------------------------------------------------------------------

@app.route("/api/actuator_timeline")
def api_actuator_timeline():
    range_param = request.args.get("range", "24h")
    offset      = int(request.args.get("offset", 0))
    start, end  = time_window(range_param, offset)
    try:
        conn = get_db()
        rows = conn.execute(
            """SELECT s.timestamp, shades_east, shades_west, fan_on,
                      circ_fans_on, hvac_mode, p.power_total_kw
               FROM sensor_log s
               LEFT JOIN (
                   SELECT timestamp AS p_ts, power_total_kw
                   FROM power_log
                   WHERE datetime(timestamp) BETWEEN ? AND ?
               ) p ON substr(s.timestamp,1,16) = substr(p.p_ts,1,16)
               WHERE datetime(s.timestamp) BETWEEN ? AND ?
               ORDER BY s.rowid ASC""",
            (start, end, start, end),
        ).fetchall()
        conn.close()
        return jsonify(_compute_actuator_timeline([dict(r) for r in rows]))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _compute_actuator_timeline(rows):
    """Convert time-series rows into ON/OFF periods per actuator."""
    checks = {
        "East Shades":      lambda r: r.get("shades_east") == "closed",
        "West Shades":      lambda r: r.get("shades_west") == "closed",
        "Exhaust Fans":     lambda r: bool(r.get("fan_on")),
        "Circ Fans":        lambda r: bool(r.get("circ_fans_on")),
        "HVAC":             lambda r: bool(r.get("hvac_mode") and r.get("hvac_mode") != "off"),
    }
    periods = {k: [] for k in checks}
    current = {k: None for k in checks}

    for row in rows:
        ts = row["timestamp"]
        for name, fn in checks.items():
            try:
                on = fn(row)
            except Exception:
                on = False
            if on and current[name] is None:
                current[name] = ts
            elif not on and current[name] is not None:
                periods[name].append({"start": current[name], "end": ts})
                current[name] = None

    if rows:
        last_ts = rows[-1]["timestamp"]
        for name in checks:
            if current[name] is not None:
                periods[name].append({"start": current[name], "end": last_ts})

    return periods


# ---------------------------------------------------------------------------
# API: HVAC runtime (diagnostic)
# ---------------------------------------------------------------------------

@app.route("/api/hvac_runtime")
def api_hvac_runtime():
    range_param = request.args.get("range", "7d")
    offset      = int(request.args.get("offset", 0))
    start, end  = time_window(range_param, offset)
    try:
        conn = get_db()
        rows = conn.execute(
            """SELECT date(timestamp) as day,
                      SUM(CASE WHEN hvac_mode != 'off' AND hvac_mode IS NOT NULL THEN 5 ELSE 0 END) / 60.0 as hours
               FROM sensor_log
               WHERE datetime(timestamp) BETWEEN ? AND ?
               GROUP BY day
               ORDER BY day ASC""",
            (start, end),
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
        ensure_overrides(conn)
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

        # Execute device command immediately
        device_error = None
        if actuator == "fan":
            try:
                if command.get("on"):
                    exhaust_fan_relay.turn_on()
                else:
                    exhaust_fan_relay.turn_off()
            except Exception as exc:
                device_error = str(exc)
                print(f"Fan command failed: {exc}", flush=True)
        elif actuator == "circ_fans":
            try:
                if command.get("on"):
                    circ_fan_switch.turn_on()
                else:
                    circ_fan_switch.turn_off()
            except Exception as exc:
                device_error = str(exc)
                print(f"Circ fan command failed: {exc}", flush=True)

        result = {"ok": True, "expires_at": expires_at.isoformat()}
        if device_error:
            result["device_error"] = device_error
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/override/<actuator>", methods=["DELETE"])
def api_cancel_override(actuator):
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = get_db()
        ensure_overrides(conn)
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
