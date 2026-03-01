"""Rule-based greenhouse control logic.

Priority: shades → exhaust fans → HVAC (stub).

Shades are deployed predictively using the 2-hour thermal model forecast.
Exhaust fans are reactive and only run when outdoor air is cool enough to help.
HVAC is predictive and is the last resort; commands are logged but not sent
until devices/minisplit.py is implemented.
"""

import json
import sqlite3
import logging
from datetime import datetime, timezone

import config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_setpoints(db_path):
    """Read (heat_sp, cool_sp) from the settings table. Returns (60.0, 80.0) on error."""
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key IN ('heat_setpoint', 'cool_setpoint')"
        ).fetchall()
        conn.close()
        d = {r[0]: float(r[1]) for r in rows}
        return d.get("heat_setpoint", 60.0), d.get("cool_setpoint", 80.0)
    except Exception:
        return 60.0, 80.0


def get_active_overrides(db_path):
    """Return set of actuator names that have an active (non-expired, non-cancelled) override."""
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT actuator FROM overrides "
            "WHERE expires_at > datetime('now') AND cancelled_at IS NULL"
        ).fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


def apply_override_states(state, db_path):
    """Apply active override commands to state so sensor_log reflects manual positions.

    When an actuator is under override, controller.execute() skips it, so
    state.shades_east/west/fan_on would otherwise stay at their restored
    (possibly stale) values and be logged incorrectly.

    Maps override actuator names and commands to GreenhouseState fields:
      shades_east: {"position": "open"|"closed"}  → state.shades_east
      shades_west: {"position": "open"|"closed"}  → state.shades_west
      fan:         {"on": true|false}              → state.fan_on
    """
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT actuator, command FROM overrides "
            "WHERE expires_at > datetime('now') AND cancelled_at IS NULL"
        ).fetchall()
        conn.close()
        for actuator, command_json in rows:
            try:
                cmd = json.loads(command_json)
                if actuator == "shades_east":
                    state.shades_east = cmd.get("position", state.shades_east)
                elif actuator == "shades_west":
                    state.shades_west = cmd.get("position", state.shades_west)
                elif actuator == "fan":
                    state.fan_on = bool(cmd.get("on", state.fan_on))
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Trajectory helpers
# ---------------------------------------------------------------------------

def _predicted_max(trajectory):
    """Return max indoor air temp (°F) over the prediction horizon, or None."""
    if not trajectory:
        return None
    temps = trajectory.get("air_temp_f", [])
    h = min(config.PREDICTION_HORIZON_MIN, len(temps) - 1)
    return max(temps[1:h + 1]) if h >= 1 else None


def _predicted_min(trajectory):
    """Return min indoor air temp (°F) over the prediction horizon, or None."""
    if not trajectory:
        return None
    temps = trajectory.get("air_temp_f", [])
    h = min(config.PREDICTION_HORIZON_MIN, len(temps) - 1)
    return min(temps[1:h + 1]) if h >= 1 else None


# ---------------------------------------------------------------------------
# Sunset detection
# ---------------------------------------------------------------------------

def _minutes_to_sunset(corrected_forecast):
    """Return minutes until the next is_day 1→0 transition, or None.

    Uses the is_day hourly array already present in corrected_forecast
    (fetched from Open-Meteo — no API changes needed).
    Returns None if it is already night or no transition found in the window.
    """
    is_day = (corrected_forecast or {}).get("is_day", [])
    times  = (corrected_forecast or {}).get("time", [])
    for i in range(1, len(is_day)):
        if is_day[i - 1] == 1 and is_day[i] == 0 and i < len(times):
            try:
                sunset_hour = datetime.fromisoformat(times[i])
                return (sunset_hour - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() / 60
            except Exception:
                return None
    return None


def _is_near_sunset(corrected_forecast):
    """Return True if sunset is within SHADE_SUNSET_LEAD_MIN minutes."""
    mins = _minutes_to_sunset(corrected_forecast)
    return mins is not None and 0 <= mins <= config.SHADE_SUNSET_LEAD_MIN


# ---------------------------------------------------------------------------
# Control logic
# ---------------------------------------------------------------------------

def decide(state, trajectory_current, trajectory_shades_open,
           heat_sp, cool_sp, overridden, corrected_forecast):
    """Compute control decisions for all actuators.

    Args:
        state                 — current GreenhouseState (already has last-cycle actuator state)
        trajectory_current    — thermal model run with current actuator state
        trajectory_shades_open — thermal model run with shades forced open (for safe-to-open check)
        heat_sp, cool_sp      — temperature setpoints (°F)
        overridden            — set of actuator names with active dashboard overrides
        corrected_forecast    — dict from forecast.py (includes is_day, time arrays)

    Returns:
        dict mapping actuator name → desired state.
        Only actuators NOT in overridden are included.
        Possible values:
          shades_east/west: "open" | "closed"
          fan: True | False
          hvac: "off" | "heat" | "cool"   (hvac is a stub — logged, not executed)
    """
    decisions = {}
    actual  = state.indoor_temp
    outdoor = state.outdoor_temp

    pred_max      = _predicted_max(trajectory_current)
    pred_min      = _predicted_min(trajectory_current)
    pred_max_open = _predicted_max(trajectory_shades_open)
    near_sunset   = _is_near_sunset(corrected_forecast)

    fans_effective = (
        actual is not None and outdoor is not None
        and outdoor < actual - config.FAN_EFFECTIVENESS_DELTA_F
    )

    # --- Shades (predictive) ---
    # Close: predicted indoor max will exceed cool setpoint.
    # Open: approaching sunset (no solar gain needed) OR safe to open (model says temp
    #       stays below cool_sp with shades open, and actual is already 5°F below cool_sp).
    for actuator in ("shades_east", "shades_west"):
        if actuator in overridden:
            continue
        if pred_max is not None and pred_max > cool_sp:
            decisions[actuator] = "closed"
        elif near_sunset:
            decisions[actuator] = "open"
        elif (pred_max_open is not None and pred_max_open < cool_sp
              and actual is not None and actual < cool_sp - config.SHADE_OPEN_MARGIN_F):
            decisions[actuator] = "open"
        # else: hold current state — no decision

    # --- Exhaust fans (reactive, effectiveness-gated) ---
    # Run only when outdoor air is cool enough to actually lower indoor temp.
    # Never run when HVAC cooling would be active.
    if "fan" not in overridden and actual is not None:
        if actual > cool_sp and fans_effective:
            decisions["fan"] = True
        elif actual < cool_sp - config.FAN_STOP_MARGIN_F or not fans_effective:
            decisions["fan"] = False
        # else: hold current state

    # --- HVAC (predictive, stub) ---
    # Heating: predicted min will drop below heat setpoint.
    # Cooling: predicted max exceeds cool setpoint but fans are not effective.
    # Never run fans and HVAC cooling simultaneously.
    if "hvac" not in overridden and actual is not None:
        fan_commanded = decisions.get("fan", state.fan_on)
        hvac_decision = "off"
        if pred_min is not None and pred_min < heat_sp:
            hvac_decision = "heat"
        elif (pred_max is not None and pred_max > cool_sp
              and not fans_effective and not fan_commanded):
            hvac_decision = "cool"
        decisions["hvac"] = hvac_decision

    return decisions


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------

def execute(decisions, state, shades_controller, exhaust_fan_relay):
    """Execute device commands for the decisions returned by decide().

    Only sends a command when the desired state differs from current state.
    Updates state.shades_east/west and state.fan_on after each command so
    sensor_log captures the new state in the same cycle.

    HVAC is a stub: the decision is logged but no device command is sent.
    Each command is individually try/excepted so one failure doesn't block others.
    """
    for actuator, desired in decisions.items():
        try:
            if actuator == "shades_east":
                if desired != state.shades_east:
                    if desired == "closed":
                        shades_controller.close_east()
                    else:
                        shades_controller.open_east()
                    state.shades_east = desired
                    log.info("[controller] Shades east → %s", desired)

            elif actuator == "shades_west":
                if desired != state.shades_west:
                    if desired == "closed":
                        shades_controller.close_west()
                    else:
                        shades_controller.open_west()
                    state.shades_west = desired
                    log.info("[controller] Shades west → %s", desired)

            elif actuator == "fan":
                if desired != state.fan_on:
                    if desired:
                        exhaust_fan_relay.turn_on()
                    else:
                        exhaust_fan_relay.turn_off()
                    state.fan_on = desired
                    log.info("[controller] Exhaust fans → %s", "on" if desired else "off")

            elif actuator == "hvac":
                # Stub: log decision only — no command sent until minisplit.py is built
                if desired != "off":
                    log.info("[controller] HVAC → %s (stub — no command sent)", desired)

        except Exception as e:
            log.error("[controller] Command failed for %s: %s", actuator, e)
