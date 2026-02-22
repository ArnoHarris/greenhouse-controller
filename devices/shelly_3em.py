"""Shelly Pro 3EM power meter integration (Gen2 RPC API)."""

import logging

import requests

import config

log = logging.getLogger(__name__)


class Shelly3EM:
    """Reads power, current, voltage, and energy from a Shelly Pro 3EM (Gen2).

    Uses two Gen2 RPC endpoints:
    - /rpc/EM.GetStatus?id=0    → real-time power (W), current (A), voltage (V)
    - /rpc/EMData.GetStatus?id=0 → cumulative energy totals (Wh)

    Phase A = a_*, Phase B = b_*. Phase C is unused in US split-phase.

    Returns cumulative energy totals in kWh (converted from Wh). The caller
    is responsible for computing per-interval deltas.
    """

    def __init__(self, ip):
        self.ip = ip

    def read(self):
        """Fetch current readings. Returns dict with phase_a, phase_b, total_power_kw.

        Raises on connection failure or HTTP error.
        """
        em_resp = requests.get(
            f"http://{self.ip}/rpc/EM.GetStatus?id=0",
            timeout=config.HTTP_TIMEOUT,
        )
        em_resp.raise_for_status()
        em = em_resp.json()

        # Energy totals for per-interval delta computation
        try:
            emd_resp = requests.get(
                f"http://{self.ip}/rpc/EMData.GetStatus?id=0",
                timeout=config.HTTP_TIMEOUT,
            )
            emd_resp.raise_for_status()
            emd = emd_resp.json()
            total_kwh_a = emd.get("a_total_act_energy", 0) / 1000.0
            total_kwh_b = emd.get("b_total_act_energy", 0) / 1000.0
        except Exception:
            total_kwh_a = None
            total_kwh_b = None

        return {
            "phase_a": {
                "power_kw": em["a_act_power"] / 1000.0,
                "current_a": em["a_current"],
                "voltage_v": em["a_voltage"],
                "total_kwh": total_kwh_a,
            },
            "phase_b": {
                "power_kw": em["b_act_power"] / 1000.0,
                "current_a": em["b_current"],
                "voltage_v": em["b_voltage"],
                "total_kwh": total_kwh_b,
            },
            "total_power_kw": em["total_act_power"] / 1000.0,
            "freq_hz": em.get("freq"),
        }
