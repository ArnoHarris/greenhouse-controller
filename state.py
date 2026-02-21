"""Greenhouse state data container."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class GreenhouseState:
    indoor_temp: float = None         # degF
    indoor_humidity: float = None     # %
    outdoor_temp: float = None        # degF
    outdoor_humidity: float = None    # %
    solar_irradiance: float = None    # W/m2
    wind_speed: float = None          # mph
    shades_east: str = "open"        # "open" or "closed" (4 shades, commanded together)
    shades_west: str = "open"        # "open" or "closed" (4 shades, commanded together)
    fan_on: bool = False
    circ_fans_on: bool = False
    hvac_mode: str = "off"            # "off", "cool", "heat", "auto"
    hvac_setpoint: float = None       # degF
    timestamp: datetime = field(default_factory=datetime.now)
