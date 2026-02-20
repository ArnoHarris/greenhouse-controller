"""Dooya/Motion Blinds Pro Hub DD7006 — shade control via motionblinds library.

Uses local UDP protocol. Requires a 16-character key from the Motion Blinds app:
  Settings → "Motion APP About" → tap that page 5 times rapidly.

Shades are commanded as east/west groups. Individual shade MACs are configured in
config.py (SHADES_EAST_MACS, SHADES_WEST_MACS) after being discovered via test_shades.py.
"""

import logging
import os
from motionblinds import MotionGateway
import config

log = logging.getLogger(__name__)


class ShadesController:
    """Controls east and west shade groups via Motion Blinds gateway."""

    def __init__(self, ip, key, east_macs, west_macs):
        self.ip = ip
        self.key = key
        self.east_macs = [m.lower() for m in east_macs]
        self.west_macs = [m.lower() for m in west_macs]
        self._gateway = None

    def connect(self):
        """Connect to gateway and populate device list. Raises on failure."""
        self._gateway = MotionGateway(ip=self.ip, key=self.key)
        self._gateway.GetDeviceList()
        self._gateway.Update()
        n = self._gateway.N_devices
        log.info("Shades: connected to gateway at %s, %d device(s) found", self.ip, n)

    def _blinds_for(self, macs):
        """Return list of MotionBlind objects for the given MAC list."""
        if self._gateway is None:
            raise RuntimeError("ShadesController not connected — call connect() first")
        devices = self._gateway.device_list
        blinds = []
        for mac in macs:
            blind = devices.get(mac)
            if blind is None:
                log.warning("Shades: MAC %s not found in gateway device list", mac)
            else:
                blinds.append(blind)
        return blinds

    def open_east(self):
        """Open all east-facing shades."""
        for blind in self._blinds_for(self.east_macs):
            blind.Open()
        log.info("Shades: east opened")

    def close_east(self):
        """Close all east-facing shades."""
        for blind in self._blinds_for(self.east_macs):
            blind.Close()
        log.info("Shades: east closed")

    def open_west(self):
        """Open all west-facing shades."""
        for blind in self._blinds_for(self.west_macs):
            blind.Open()
        log.info("Shades: west opened")

    def close_west(self):
        """Close all west-facing shades."""
        for blind in self._blinds_for(self.west_macs):
            blind.Close()
        log.info("Shades: west closed")

    def open_all(self):
        self.open_east()
        self.open_west()

    def close_all(self):
        self.close_east()
        self.close_west()

    def read(self):
        """Return current shade state as dict with 'east' and 'west' keys.

        Position 0 = fully open, 100 = fully closed (motionblinds convention).
        Returns 'open', 'closed', or 'unknown' for each group.
        """
        def group_state(macs):
            blinds = self._blinds_for(macs)
            if not blinds:
                return "unknown"
            for blind in blinds:
                try:
                    blind.Update()
                except Exception as e:
                    log.warning("Shades: failed to update blind %s: %s", blind.mac, e)
            positions = [b.position for b in blinds if b.position is not None]
            if not positions:
                return "unknown"
            avg = sum(positions) / len(positions)
            # position 0 = open, 100 = closed in motionblinds convention
            if avg <= 10:
                return "open"
            elif avg >= 90:
                return "closed"
            else:
                return f"partial({avg:.0f}%)"

        return {
            "east": group_state(self.east_macs),
            "west": group_state(self.west_macs),
        }

    @property
    def all_blinds(self):
        """Return all MotionBlind objects from the gateway device list."""
        if self._gateway is None:
            return []
        return list(self._gateway.device_list.values())
