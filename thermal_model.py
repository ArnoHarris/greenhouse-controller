"""Lumped-parameter thermal model for greenhouse temperature prediction.

2-node model:
  - Air node: indoor air temperature
  - Thermal mass node: concrete perimeter, concrete floor, gravel beds (thermal flywheel)

Uses Euler integration at 1-minute steps.

Greenhouse geometry: 29' x 14.75', ridge N-S, 8.5/12 roof pitch.
East and west roof shades modeled independently (sun angle matters).
North gable shared with unconditioned flower shed (buffer zone, reduced U).
Exhaust fans pull air from south through flower shed via north louver vents.
"""

import logging
from datetime import datetime, timedelta, timezone

import config

log = logging.getLogger(__name__)

# Shorthand for greenhouse params
G = config.GREENHOUSE


def _f_to_c(f):
    return (f - 32) * 5 / 9


def _c_to_f(c):
    return c * 9 / 5 + 32


def predict(state, forecast, horizon_hours=None):
    """Run thermal model forward to predict indoor temperature trajectory.

    Args:
        state: GreenhouseState with current conditions
        forecast: corrected forecast dict with hourly arrays
            (time, temperature_f, solar_irradiance_wm2, wind_speed_mph, humidity)
        horizon_hours: how many hours ahead to predict (default from config)

    Returns:
        dict with:
            times: list of datetime strings at 1-minute intervals
            air_temp_f: list of predicted indoor air temps (degF)
            mass_temp_f: list of predicted thermal mass temps (degF)
            params: dict of model parameters used (for logging)
    """
    if horizon_hours is None:
        horizon_hours = config.MODEL_HORIZON_HOURS

    dt = config.MODEL_STEP_SECONDS  # integration step (seconds)
    steps = int(horizon_hours * 3600 / dt)

    # Initialize node temperatures (convert to Celsius for internal math)
    T_air = _f_to_c(state.indoor_temp) if state.indoor_temp is not None else 20.0
    # Thermal mass assumed close to air temp initially (no separate sensor)
    T_mass = T_air

    # Current actuator state
    shade_east = 1.0 if state.shades_east == "closed" else 0.0
    shade_west = 1.0 if state.shades_west == "closed" else 0.0
    fan_on = state.fan_on if state.fan_on is not None else False
    hvac_mode = (state.hvac_mode or "off") if state.hvac_mode is not None else "off"
    Q_hvac = (config.HVAC_CAPACITY_W if hvac_mode == "heat"
              else -config.HVAC_CAPACITY_W if hvac_mode == "cool"
              else 0.0)

    # Build interpolated outdoor conditions from hourly forecast
    outdoor_temps_c, solar_vals, wind_vals = _interpolate_forecast(
        forecast, steps, dt
    )

    # Model parameters
    C_air = G["air_heat_capacity_J_per_K"]
    C_mass = G["mass_heat_capacity_J_per_K"]
    tau = G["cover_transmittance"]
    f_mass = G["mass_solar_fraction"]
    U_ground = G["ground_coupling_W_per_K"]

    # Envelope heat loss: outdoor-exposed surfaces + buffered north wall
    U_env = G["envelope_U_W_per_m2K"]
    UA_outdoor = U_env * G["envelope_area_m2"]
    UA_north = U_env * G["north_wall_area_m2"] * G["north_wall_U_factor"]
    UA_total = UA_outdoor + UA_north

    # Roof face areas for per-side solar calculation
    A_roof_east = config.ROOF_EAST_AREA_M2
    A_roof_west = config.ROOF_WEST_AREA_M2
    # Non-roof glazing that admits solar (side walls + gables, horizontal projection)
    A_floor = G["floor_area_m2"]

    # Fan ventilation: actual exhaust fan flow rate
    rho_cp = 1.2 * 1006  # air density * specific heat (J/m3/K)
    fan_flow = G["fan_flow_m3_per_s"] if fan_on else 0.0

    # Results arrays
    times = []
    air_temps = [_c_to_f(T_air)]
    mass_temps = [_c_to_f(T_mass)]

    start_time = datetime.now()
    times.append(start_time.isoformat())

    for i in range(steps):
        T_out = outdoor_temps_c[min(i, len(outdoor_temps_c) - 1)]
        I_solar = solar_vals[min(i, len(solar_vals) - 1)]

        # Solar heat gain through glazing (W)
        # Roof shades block solar on their respective face; unshaded surfaces
        # still transmit. Simplified: treat total solar as split across floor area
        # projection, with east/west roof shades reducing their share proportionally.
        # East roof contributes ~half the roof solar, west the other half.
        roof_fraction = (A_roof_east + A_roof_west) / (A_roof_east + A_roof_west + A_floor)
        east_share = A_roof_east / (A_roof_east + A_roof_west)  # 0.5 for symmetric
        west_share = 1.0 - east_share

        # Effective solar transmission considering shades on each roof face
        roof_solar = I_solar * tau * A_floor * roof_fraction * (
            east_share * (1 - shade_east) + west_share * (1 - shade_west)
        )
        wall_solar = I_solar * tau * A_floor * (1 - roof_fraction)
        Q_solar = roof_solar + wall_solar

        # Solar absorbed by air vs thermal mass
        Q_solar_air = Q_solar * (1 - f_mass)
        Q_solar_mass = Q_solar * f_mass

        # Envelope heat loss (W) — all surfaces to outdoor
        Q_envelope = UA_total * (T_air - T_out)

        # Ventilation heat loss (W) — exhaust fans
        Q_vent = rho_cp * fan_flow * (T_air - T_out)

        # Ground/mass exchange (W)
        Q_ground = U_ground * (T_air - T_mass)

        # Air node energy balance (Q_hvac = 0 until minisplit.py is implemented)
        dT_air = (Q_solar_air - Q_envelope - Q_vent - Q_ground + Q_hvac) / C_air * dt
        T_air += dT_air

        # Thermal mass energy balance
        dT_mass = (Q_solar_mass + U_ground * (T_air - T_mass)) / C_mass * dt
        T_mass += dT_mass

        # Record every minute
        step_time = start_time + timedelta(seconds=(i + 1) * dt)
        times.append(step_time.isoformat())
        air_temps.append(round(_c_to_f(T_air), 1))
        mass_temps.append(round(_c_to_f(T_mass), 1))

    params = {
        "C_air": C_air,
        "C_mass": C_mass,
        "UA_total": UA_total,
        "cover_transmittance": tau,
        "floor_area_m2": A_floor,
        "mass_solar_fraction": f_mass,
        "ground_coupling_W_per_K": U_ground,
        "shade_east": shade_east,
        "shade_west": shade_west,
        "fan_on": fan_on,
        "fan_flow_m3s": fan_flow,
        "hvac_mode": hvac_mode,
        "hvac_capacity_w": config.HVAC_CAPACITY_W,
    }

    return {
        "times": times,
        "air_temp_f": air_temps,
        "mass_temp_f": mass_temps,
        "params": params,
    }


def _interpolate_forecast(forecast, steps, dt):
    """Linearly interpolate hourly forecast to per-minute values.

    The forecast array starts at a fixed UTC time (e.g. midnight or the fetch
    hour of the previous day). We calculate how many hours have elapsed since
    that start and offset all array lookups accordingly, so the model always
    uses conditions that correspond to the current moment and forward — not
    stale data from the beginning of the forecast window.

    Returns (outdoor_temps_c, solar_vals, wind_vals) as lists with one
    entry per integration step.
    """
    # Hourly values
    hourly_temp_f = forecast.get("temperature_f", [])
    hourly_solar = forecast.get("solar_irradiance_wm2", [])
    hourly_wind = forecast.get("wind_speed_mph", [])
    forecast_times = forecast.get("time", [])

    n_hourly = len(hourly_temp_f)
    steps_per_hour = int(3600 / dt)

    # Determine how many hours into the forecast array "now" falls.
    # forecast_times entries are naive UTC strings (e.g. "2026-02-27T08:00").
    hour_offset = 0
    if forecast_times:
        try:
            first_time = datetime.fromisoformat(forecast_times[0])
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            elapsed_hours = (now_utc - first_time).total_seconds() / 3600
            hour_offset = max(0, int(elapsed_hours))
            log.debug("Forecast hour offset: %d (forecast starts %s, now %s)",
                      hour_offset, forecast_times[0], now_utc.isoformat(timespec="minutes"))
        except Exception as e:
            log.warning("Could not compute forecast hour offset: %s", e)

    outdoor_temps_c = []
    solar_vals = []
    wind_vals = []

    for i in range(steps):
        # Which hourly bucket does this step fall in, relative to now?
        hour_float = i / steps_per_hour
        hour_idx = int(hour_float) + hour_offset
        frac = hour_float - int(hour_float)

        if hour_idx + 1 < n_hourly:
            # Linear interpolation between this hour and next
            t = _lerp(hourly_temp_f[hour_idx], hourly_temp_f[hour_idx + 1], frac)
            s = _lerp(hourly_solar[hour_idx], hourly_solar[hour_idx + 1], frac)
            w = _lerp(hourly_wind[hour_idx], hourly_wind[hour_idx + 1], frac)
        elif hour_idx < n_hourly:
            t = hourly_temp_f[hour_idx] or 70
            s = hourly_solar[hour_idx] or 0
            w = hourly_wind[hour_idx] or 0
        else:
            # Past end of forecast — hold last value
            t = hourly_temp_f[-1] if hourly_temp_f else 70
            s = hourly_solar[-1] if hourly_solar else 0
            w = hourly_wind[-1] if hourly_wind else 0

        outdoor_temps_c.append(_f_to_c(t if t is not None else 70))
        solar_vals.append(max(0, s if s is not None else 0))
        wind_vals.append(max(0, w if w is not None else 0))

    return outdoor_temps_c, solar_vals, wind_vals


def _lerp(a, b, t):
    """Linear interpolation between a and b. t in [0, 1]."""
    if a is None:
        a = 0
    if b is None:
        b = 0
    return a + (b - a) * t
