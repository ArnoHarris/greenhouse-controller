"""Shelly H&T Gen3 — indoor temperature and humidity sensor.

Data sources in priority order:
  1. MQTT (local, pushed by device on wake cycles)
  2. Shelly Cloud API (fallback if MQTT data is stale)
  3. Thermal model prediction (handled by resilience layer)

The H&T G3 is a battery device that sleeps between readings.
Even on USB power, it connects briefly to MQTT, publishes, then disconnects.
We subscribe to MQTT and cache the latest values. If the cached data is too
old, we fall back to the Shelly Cloud API.
"""

import os
import json
import logging
import threading
from datetime import datetime

import requests
import config

log = logging.getLogger(__name__)

# Maximum age of MQTT data before falling back to cloud (seconds)
MQTT_STALE_THRESHOLD = 600  # 10 minutes


class ShellyHT:
    """Reads temperature and humidity from Shelly H&T G3.

    Primary: MQTT subscription (cached values from device pushes)
    Fallback: Shelly Cloud API
    """

    def __init__(self):
        self.device_id = config.SHELLY_HT_DEVICE_ID
        self.cloud_server = os.getenv("SHELLY_CLOUD_SERVER")
        self.cloud_auth_key = os.getenv("SHELLY_CLOUD_AUTH_KEY")

        # MQTT cached values (updated by mqtt_on_message callback)
        self._mqtt_data = {}
        self._mqtt_last_update = None
        self._lock = threading.Lock()

    def mqtt_on_message(self, topic, payload):
        """Called by MQTT subscriber when a message arrives from the H&T.

        Shelly Gen3 publishes status as JSON to:
          <device_id>/status/temperature:0
          <device_id>/status/humidity:0
          <device_id>/status/devicepower:0
        """
        with self._lock:
            try:
                data = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                log.warning("Failed to parse MQTT payload from %s", topic)
                return

            if "temperature:0" in topic:
                self._mqtt_data["temp_f"] = round(data.get("tF", 0), 1)
                self._mqtt_data["temp_c"] = data.get("tC")
                self._mqtt_last_update = datetime.now()
                log.info("MQTT: indoor temp %.1fF", self._mqtt_data["temp_f"])

            elif "humidity:0" in topic:
                self._mqtt_data["humidity"] = data.get("rh")
                self._mqtt_last_update = datetime.now()
                log.info("MQTT: indoor humidity %.0f%%", self._mqtt_data["humidity"])

            elif "devicepower:0" in topic:
                battery = data.get("battery", {})
                self._mqtt_data["battery_pct"] = battery.get("percent")
                self._mqtt_last_update = datetime.now()

    def read(self):
        """Get current readings. Tries MQTT cache first, then cloud API.

        Returns dict with temp_f, humidity, battery_pct.
        """
        # Try MQTT cached data first
        with self._lock:
            if self._mqtt_last_update is not None:
                age = (datetime.now() - self._mqtt_last_update).total_seconds()
                if age < MQTT_STALE_THRESHOLD and "temp_f" in self._mqtt_data:
                    log.debug("Using MQTT data (age: %.0fs)", age)
                    return {
                        "temp_f": self._mqtt_data.get("temp_f"),
                        "humidity": self._mqtt_data.get("humidity"),
                        "battery_pct": self._mqtt_data.get("battery_pct"),
                    }
                else:
                    log.info("MQTT data stale (age: %.0fs), trying cloud", age)

        # Fall back to Shelly Cloud API
        return self._read_cloud()

    def _read_cloud(self):
        """Fetch current readings from Shelly Cloud API."""
        if not self.cloud_server or not self.cloud_auth_key:
            raise ValueError("Shelly Cloud credentials not configured in .env")

        resp = requests.post(
            f"{self.cloud_server}/device/status",
            data={
                "id": self.device_id,
                "auth_key": self.cloud_auth_key,
            },
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        result = resp.json()

        if not result.get("isok"):
            raise ValueError(f"Shelly Cloud API error: {result}")

        status = result.get("data", {}).get("device_status", {})

        temp_f = status.get("temperature:0", {}).get("tF")
        humidity = status.get("humidity:0", {}).get("rh")
        battery_pct = status.get("devicepower:0", {}).get("battery", {}).get("percent")

        log.info("Cloud: indoor temp %.1fF, humidity %.0f%%",
                 temp_f or 0, humidity or 0)

        return {
            "temp_f": round(temp_f, 1) if temp_f is not None else None,
            "humidity": humidity,
            "battery_pct": battery_pct,
        }

    def get_mqtt_topics(self):
        """Return list of MQTT topics to subscribe to."""
        prefix = config.SHELLY_HT_MQTT_TOPIC_PREFIX
        return [
            f"{prefix}/status/temperature:0",
            f"{prefix}/status/humidity:0",
            f"{prefix}/status/devicepower:0",
        ]
