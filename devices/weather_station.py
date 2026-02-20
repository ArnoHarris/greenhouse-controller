"""AmbientWeather WS-2902 — outdoor conditions via REST API."""

import os
import logging
import requests
import config

log = logging.getLogger(__name__)


class WeatherStation:
    """Reads outdoor conditions from AmbientWeather.net REST API."""

    def __init__(self):
        self.api_key = os.getenv("AMBIENT_WEATHER_API_KEY")
        self.app_key = os.getenv("AMBIENT_WEATHER_APP_KEY")
        if not self.api_key or not self.app_key:
            raise ValueError("AMBIENT_WEATHER_API_KEY and AMBIENT_WEATHER_APP_KEY must be set in .env")

    def read(self):
        """Fetch latest station data. Returns dict with outdoor conditions."""
        resp = requests.get(
            config.AMBIENT_WEATHER_BASE_URL,
            params={
                "apiKey": self.api_key,
                "applicationKey": self.app_key,
            },
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        devices = resp.json()

        if not devices:
            raise ValueError("No devices returned from AmbientWeather API")

        # Use first device's most recent data
        data = devices[0].get("lastData", {})

        return {
            "outdoor_temp_f": data.get("tempf"),
            "outdoor_humidity": data.get("humidity"),
            "solar_irradiance_wm2": data.get("solarradiation"),
            "wind_speed_mph": data.get("windspeedmph"),
            "wind_gust_mph": data.get("windgustmph"),
            "barometric_pressure": data.get("baromrelin"),
            "daily_rain_in": data.get("dailyrainin"),
            "dew_point_f": data.get("dewPoint"),
            "feels_like_f": data.get("feelsLike"),
            "uv_index": data.get("uv"),
            "dateutc": data.get("dateutc"),
        }
