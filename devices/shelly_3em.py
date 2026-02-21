"""Shelly 3EM power meter integration (Gen1 API)."""

import logging

import requests

import config

log = logging.getLogger(__name__)


class Shelly3EM:
    """Reads power, current, voltage, and energy from a Shelly 3EM (Gen1).

    The 3EM has three channels (emeters[0..2]). Phase A = emeters[0],
    Phase B = emeters[1]. Phase C (emeters[2]) is typically unused in
    US split-phase installations.

    Returns cumulative energy totals in kWh (converted from Wh). The caller
    is responsible for computing per-interval deltas.
    """

    def __init__(self, ip):
        self.ip = ip

    def read(self):
        """Fetch current readings. Returns dict with phase_a, phase_b, total_power_kw.

        Raises on connection failure or HTTP error.
        """
        resp = requests.get(
            f"http://{self.ip}/status",
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        emeters = data["emeters"]

        def _parse_phase(e):
            if not e.get("is_valid", False):
                return {"power_kw": None, "current_a": None, "voltage_v": None, "total_kwh": None}
            return {
                "power_kw": e["power"] / 1000.0,
                "current_a": e["current"],
                "voltage_v": e["voltage"],
                "total_kwh": e["total"] / 1000.0,
            }

        return {
            "phase_a": _parse_phase(emeters[0]),
            "phase_b": _parse_phase(emeters[1]),
            "total_power_kw": data.get("total_power", 0) / 1000.0,
        }
