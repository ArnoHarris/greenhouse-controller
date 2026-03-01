"""Open-Meteo forecast retrieval and bias correction."""

import logging
from datetime import datetime, timezone

import requests
import config

log = logging.getLogger(__name__)


def fetch_forecast():
    """Fetch hourly forecast from Open-Meteo. Returns dict with hourly arrays.

    Keys: time, temperature_f, humidity, solar_irradiance_wm2, wind_speed_mph
    Each value is a list aligned by index (one entry per forecast hour).
    """
    params = {
        "latitude": config.LATITUDE,
        "longitude": config.LONGITUDE,
        "hourly": ",".join([
            "temperature_2m",
            "relative_humidity_2m",
            "direct_radiation",
            "diffuse_radiation",
            "wind_speed_10m",
            "weather_code",
            "is_day",
        ]),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "forecast_days": 2,
        "timezone": "UTC",
    }

    resp = requests.get(config.OPEN_METEO_BASE_URL, params=params,
                        timeout=config.HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    hourly = data["hourly"]

    # Combine direct + diffuse radiation for total global horizontal irradiance
    ghi = [
        (d or 0) + (diff or 0)
        for d, diff in zip(hourly["direct_radiation"], hourly["diffuse_radiation"])
    ]

    return {
        "time": hourly["time"],
        "temperature_f": hourly["temperature_2m"],
        "humidity": hourly["relative_humidity_2m"],
        "solar_irradiance_wm2": ghi,
        "wind_speed_mph": hourly["wind_speed_10m"],
        "weather_code": hourly.get("weather_code", []),
        "is_day": hourly.get("is_day", []),
    }


def apply_bias_correction(forecast, station_reading):
    """Apply flat-delta bias correction using current station readings.

    Compares the forecast's current-hour values against actual station data
    and shifts all future forecast values by the difference.

    Args:
        forecast: dict from fetch_forecast()
        station_reading: dict from WeatherStation.read() with current conditions

    Returns:
        Corrected forecast dict (same structure, shifted values).
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    current_hour = now.strftime("%Y-%m-%dT%H:00")

    # Find the index of the current hour in the forecast
    try:
        idx = forecast["time"].index(current_hour)
    except ValueError:
        log.warning("Current hour %s not found in forecast times, skipping bias correction",
                     current_hour)
        return forecast

    corrected = {
        "time": forecast["time"],
        "temperature_f": list(forecast["temperature_f"]),
        "humidity": list(forecast["humidity"]),
        "solar_irradiance_wm2": list(forecast["solar_irradiance_wm2"]),
        "wind_speed_mph": list(forecast["wind_speed_mph"]),
        "weather_code": list(forecast.get("weather_code", [])),
        "is_day": list(forecast.get("is_day", [])),
    }

    # Compute deltas: actual - forecast at current hour
    deltas = {}
    if station_reading.get("outdoor_temp_f") is not None and forecast["temperature_f"][idx] is not None:
        deltas["temperature_f"] = station_reading["outdoor_temp_f"] - forecast["temperature_f"][idx]

    if station_reading.get("outdoor_humidity") is not None and forecast["humidity"][idx] is not None:
        deltas["humidity"] = station_reading["outdoor_humidity"] - forecast["humidity"][idx]

    if station_reading.get("solar_irradiance_wm2") is not None and forecast["solar_irradiance_wm2"][idx] is not None:
        deltas["solar_irradiance_wm2"] = station_reading["solar_irradiance_wm2"] - forecast["solar_irradiance_wm2"][idx]

    if station_reading.get("wind_speed_mph") is not None and forecast["wind_speed_mph"][idx] is not None:
        deltas["wind_speed_mph"] = station_reading["wind_speed_mph"] - forecast["wind_speed_mph"][idx]

    # Apply deltas to all hours from current onward
    for key, delta in deltas.items():
        for i in range(idx, len(corrected[key])):
            if corrected[key][i] is not None:
                corrected[key][i] = corrected[key][i] + delta
                # Clamp non-negative fields
                if key in ("solar_irradiance_wm2", "humidity", "wind_speed_mph"):
                    corrected[key][i] = max(0, corrected[key][i])

    log.info("Bias correction deltas: %s", deltas)
    return corrected


def get_current_conditions_from_forecast(forecast):
    """Extract current-hour conditions from Open-Meteo forecast.

    Used as fallback when AmbientWeather station is unavailable.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    current_hour = now.strftime("%Y-%m-%dT%H:00")

    try:
        idx = forecast["time"].index(current_hour)
    except ValueError:
        log.warning("Current hour not found in forecast for fallback conditions")
        return None

    return {
        "outdoor_temp_f": forecast["temperature_f"][idx],
        "outdoor_humidity": forecast["humidity"][idx],
        "solar_irradiance_wm2": forecast["solar_irradiance_wm2"][idx],
        "wind_speed_mph": forecast["wind_speed_mph"][idx],
    }
