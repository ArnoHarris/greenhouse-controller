"""SQLite data logging for greenhouse controller."""

import json
import sqlite3
import logging
from datetime import datetime

import config

log = logging.getLogger(__name__)

_conn = None


def get_connection():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH)
        _conn.execute("PRAGMA journal_mode=WAL")
        _init_tables(_conn)
    return _conn


def _init_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sensor_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            indoor_temp_f REAL,
            indoor_humidity REAL,
            outdoor_temp_f REAL,
            outdoor_humidity REAL,
            solar_irradiance_wm2 REAL,
            wind_speed_mph REAL,
            shades_east TEXT,
            shades_west TEXT,
            fan_on INTEGER,
            hvac_mode TEXT,
            hvac_setpoint REAL
        );

        CREATE TABLE IF NOT EXISTS forecast_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            raw_forecast TEXT,
            corrected_forecast TEXT,
            bias_deltas TEXT
        );

        CREATE TABLE IF NOT EXISTS model_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            predicted_trajectory TEXT,
            model_params TEXT
        );

        CREATE TABLE IF NOT EXISTS overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actuator TEXT NOT NULL,
            command TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            source TEXT NOT NULL,
            cancelled_at TEXT
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            message TEXT NOT NULL,
            severity TEXT NOT NULL,
            acknowledged INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS heartbeat (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            timestamp TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS startups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            initial_state TEXT
        );

        CREATE TABLE IF NOT EXISTS model_accuracy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            predicted_temp_f REAL NOT NULL,
            actual_temp_f REAL NOT NULL,
            error_f REAL NOT NULL,
            horizon_minutes INTEGER NOT NULL
        );
    """)
    conn.commit()


def log_sensors(state):
    """Log current sensor readings."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO sensor_log
           (timestamp, indoor_temp_f, indoor_humidity, outdoor_temp_f,
            outdoor_humidity, solar_irradiance_wm2, wind_speed_mph,
            shades_east, shades_west, fan_on, hvac_mode, hvac_setpoint)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            state.timestamp.isoformat(),
            state.indoor_temp,
            state.indoor_humidity,
            state.outdoor_temp,
            state.outdoor_humidity,
            state.solar_irradiance,
            state.wind_speed,
            state.shades_east,
            state.shades_west,
            int(state.fan_on) if state.fan_on is not None else None,
            state.hvac_mode,
            state.hvac_setpoint,
        ),
    )
    conn.commit()


def log_forecast(raw_forecast, corrected_forecast, bias_deltas=None):
    """Log raw and corrected forecast data."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO forecast_log (timestamp, raw_forecast, corrected_forecast, bias_deltas) VALUES (?, ?, ?, ?)",
        (
            datetime.now().isoformat(),
            json.dumps(raw_forecast),
            json.dumps(corrected_forecast),
            json.dumps(bias_deltas) if bias_deltas else None,
        ),
    )
    conn.commit()


def log_model_prediction(trajectory, model_params):
    """Log thermal model prediction."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO model_log (timestamp, predicted_trajectory, model_params) VALUES (?, ?, ?)",
        (
            datetime.now().isoformat(),
            json.dumps(trajectory),
            json.dumps(model_params),
        ),
    )
    conn.commit()


def update_heartbeat():
    """Update heartbeat so dashboard knows controller is alive."""
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO heartbeat (id, timestamp) VALUES (1, ?)",
        (datetime.now().isoformat(),),
    )
    conn.commit()


def log_startup(initial_state=None):
    """Log a controller startup event."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO startups (timestamp, initial_state) VALUES (?, ?)",
        (datetime.now().isoformat(), json.dumps(initial_state) if initial_state else None),
    )
    conn.commit()


def log_model_accuracy(predicted_temp_f, actual_temp_f, horizon_minutes):
    """Log a single predicted-vs-actual comparison."""
    conn = get_connection()
    error = predicted_temp_f - actual_temp_f
    conn.execute(
        """INSERT INTO model_accuracy
           (timestamp, predicted_temp_f, actual_temp_f, error_f, horizon_minutes)
           VALUES (?, ?, ?, ?, ?)""",
        (datetime.now().isoformat(), predicted_temp_f, actual_temp_f, error, horizon_minutes),
    )
    conn.commit()


def get_model_rmse(hours_back=24):
    """Compute RMSE of model predictions over the last N hours.

    Returns dict with rmse, mean_bias, and count, or None if no data.
    """
    conn = get_connection()
    cutoff = datetime.now().isoformat()
    # SQLite datetime comparison works on ISO strings
    row = conn.execute(
        """SELECT
               COUNT(*) as n,
               AVG(error_f) as mean_bias,
               AVG(error_f * error_f) as mse
           FROM model_accuracy
           WHERE timestamp > datetime(?, '-%d hours')""" % hours_back,
        (cutoff,),
    ).fetchone()

    if row is None or row[0] == 0:
        return None

    import math
    return {
        "count": row[0],
        "mean_bias_f": round(row[1], 2),
        "rmse_f": round(math.sqrt(row[2]), 2),
    }


def close():
    global _conn
    if _conn:
        _conn.close()
        _conn = None
