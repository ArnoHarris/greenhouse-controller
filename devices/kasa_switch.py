"""Kasa smart switch control for circulating fans (HS210 3-way switch)."""

import asyncio
import logging

from kasa import SmartPlug

log = logging.getLogger(__name__)


class KasaSwitch:
    """Controls a Kasa smart switch via the python-kasa local API.

    python-kasa is async; each method wraps calls with asyncio.run() for
    compatibility with the synchronous main loop.
    """

    def __init__(self, ip):
        self.ip = ip

    def read(self):
        """Return current switch state. Raises on failure."""
        device = SmartPlug(self.ip)
        asyncio.run(device.update())
        return {"on": device.is_on}

    def turn_on(self):
        """Turn switch on. Raises on failure."""
        device = SmartPlug(self.ip)
        asyncio.run(device.update())
        asyncio.run(device.turn_on())

    def turn_off(self):
        """Turn switch off. Raises on failure."""
        device = SmartPlug(self.ip)
        asyncio.run(device.update())
        asyncio.run(device.turn_off())
