"""Shelly Plus 1 PM — relay control and power monitoring.

Uses Gen2+ RPC API: http://<ip>/rpc/Switch.GetStatus?id=0
"""

import logging
import requests
import config

log = logging.getLogger(__name__)


class ShellyRelay:
    """Controls a Shelly Plus 1 PM relay via local RPC API."""

    def __init__(self, ip, name="relay"):
        self.ip = ip
        self.name = name
        self.base_url = f"http://{self.ip}/rpc"

    def read(self):
        """Read current relay state and power. Returns dict."""
        resp = requests.get(
            f"{self.base_url}/Switch.GetStatus",
            params={"id": 0},
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        return {
            "on": data.get("output", False),
            "power_w": data.get("apower", 0),
            "energy_wh": data.get("aenergy", {}).get("total", 0),
            "voltage": data.get("voltage"),
            "current_a": data.get("current"),
            "temperature_c": data.get("temperature", {}).get("tC"),
        }

    def turn_on(self):
        """Turn relay on."""
        resp = requests.get(
            f"{self.base_url}/Switch.Set",
            params={"id": 0, "on": "true"},
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        log.info("%s: turned ON", self.name)

    def turn_off(self):
        """Turn relay off."""
        resp = requests.get(
            f"{self.base_url}/Switch.Set",
            params={"id": 0, "on": "false"},
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        log.info("%s: turned OFF", self.name)
