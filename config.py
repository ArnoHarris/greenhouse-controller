"""Configuration for greenhouse controller.

Device IPs, API endpoints, greenhouse physical parameters, and tuning values.
Secrets (API keys, passwords) live in .env, not here.
Environment-specific IPs (e.g. devices that move between VLANs) also live in .env.
"""

# ---------------------------------------------------------------------------
# Device addresses (DHCP reservations — update with actual IPs)
# ---------------------------------------------------------------------------
SHELLY_HT_IP = "192.168.2.100"          # Shelly H&T G3 on IoT VLAN
SHELLY_HT_DEVICE_ID = "e4b323311d58"   # MAC-based ID (cloud API uses this format)
SHELLY_RELAY_IP = "192.168.1.XXX"       # Shelly Plus 1 PM on IoT VLAN
KASA_CIRC_FANS_IP = "192.168.2.102"    # Kasa HS210 3-way switch (2x circulating fans)
WEATHER_STATION_IP = "192.168.2.101"   # AmbientWeather WS-2902 on IoT VLAN
MOTION_GATEWAY_IP = "192.168.1.103"    # Dooya/Motion Blinds Pro Hub DD7006 (main LAN; IoT VLAN move deferred)
SHELLY_3EM_IP = "192.168.2.105"        # Shelly 3EM power meter

# MAC addresses of individual shades (discovered via test_shades.py — fill in after first run)
# East shades face ~NNE, block morning sun. West shades face ~SSW, block afternoon sun.
SHADES_EAST_MACS = ["9451dc9567200001", "9451dc9567200002", "9451dc9567200003", "9451dc9567200004"]
SHADES_WEST_MACS = ["9451dc9567200005", "9451dc9567200006", "9451dc9567200007", "9451dc9567200008"]

# Shelly MQTT topics (Gen3 H&T publishes to these)
SHELLY_HT_MQTT_TOPIC_PREFIX = "shellyhtg3-e4b323311d58"

# ---------------------------------------------------------------------------
# AmbientWeather API
# ---------------------------------------------------------------------------
AMBIENT_WEATHER_BASE_URL = "https://api.ambientweather.net/v1/devices"

# ---------------------------------------------------------------------------
# Open-Meteo API
# ---------------------------------------------------------------------------
OPEN_METEO_BASE_URL = "https://api.open-meteo.com/v1/forecast"

# Greenhouse location and orientation
LATITUDE = 38.4199
LONGITUDE = -122.9037
RIDGE_HEADING_DEG = 295     # ridge azimuth in degrees (WNW), ~25° off true N-S

# ---------------------------------------------------------------------------
# Polling / loop timing
# ---------------------------------------------------------------------------
POLL_INTERVAL_SECONDS = 300  # 5 minutes

# ---------------------------------------------------------------------------
# HTTP resilience
# ---------------------------------------------------------------------------
HTTP_CONNECT_TIMEOUT = 5     # seconds
HTTP_READ_TIMEOUT = 10       # seconds
HTTP_TIMEOUT = (HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT)
RETRY_DELAY = 2              # seconds between retry attempts

# ---------------------------------------------------------------------------
# Forecast parameters
# ---------------------------------------------------------------------------
FORECAST_HOURS_AHEAD = 6

# ---------------------------------------------------------------------------
# Greenhouse physical parameters (calculated from measured dimensions)
# ---------------------------------------------------------------------------
# Structure: 29' x 14.75' floorplate, 7.6' eaves, 13.1' ridge, 8.5/12 roof pitch
# Ridge heading ~295° (WNW). We refer to sides as N/S/E/W but rotated ~25° from true.
# "East" roof face actually faces ~NNE, "west" face ~SSW. 6mm single-pane tempered glass.
# 2' x 8" concrete perimeter wall. Mixed floor: concrete center, gravel beds sides.
# North gable shared with unconditioned flower shed (buffer zone).
# 6 ridge vents + 6 side vents (hydraulic/temp-actuated), passive ventilation.
# 2x 14" exhaust fans (~2000 cfm total) on south gable.
# 4x 2'x2' louver vents on north gable (fan intake from flower shed).
# Mitsubishi 18,000 BTU minisplit on north gable wall.

GREENHOUSE = {
    # Geometry
    "floor_area_m2": 39.74,             # 29' x 14.75' = 427.75 ft²
    "volume_m3": 125.3,                 # 4,426 ft³ measured

    # Glazing
    "cover_transmittance": 0.82,        # 6mm single-pane tempered glass

    # Envelope (outdoor-exposed surfaces only)
    "envelope_U_W_per_m2K": 5.8,        # single-pane glass U-value
    "envelope_area_m2": 104.7,          # roof (49.6) + side walls (41.0) + south gable (14.2)
    #   East roof face:  29' x 9.20' = 266.8 ft² = 24.8 m²
    #   West roof face:  29' x 9.20' = 266.8 ft² = 24.8 m²
    #   East side wall:  29' x 7.6'  = 220.4 ft² = 20.5 m²
    #   West side wall:  29' x 7.6'  = 220.4 ft² = 20.5 m²
    #   South gable:     152.7 ft²   = 14.2 m²

    # North wall (shared with flower shed — reduced heat loss)
    "north_wall_area_m2": 14.2,         # same geometry as south gable
    "north_wall_U_factor": 0.5,         # effective reduction (flower shed buffers temp)

    # Thermal capacitance
    "air_heat_capacity_J_per_K": 151200.0,    # rho * c_p * V = 1.2 * 1006 * 125.3
    "mass_heat_capacity_J_per_K": 10000000.0, # concrete perimeter + floor + gravel beds
    #   Concrete perimeter wall (active 10cm): ~3,300,000 J/K
    #   Concrete floor center (~1/3 area):     ~2,700,000 J/K
    #   Gravel beds (~2/3 area, 15cm deep):    ~5,000,000 J/K
    "mass_solar_fraction": 0.40,        # fraction of solar absorbed by thermal mass

    # Coupling
    "ground_coupling_W_per_K": 150.0,   # air ↔ thermal mass (floor + perimeter ~56 m², ~5 W/m²K effective)

    # Ventilation
    "fan_flow_m3_per_s": 0.944,         # 2x 14" exhaust fans, ~2000 cfm total
}

# Roof face areas for east/west shade modeling
ROOF_EAST_AREA_M2 = 24.8   # 29' x 9.20' rafter
ROOF_WEST_AREA_M2 = 24.8   # 29' x 9.20' rafter

# ---------------------------------------------------------------------------
# Thermal model integration
# ---------------------------------------------------------------------------
MODEL_STEP_SECONDS = 60      # Euler integration step (1 minute)
MODEL_HORIZON_HOURS = 6      # how far ahead to predict

# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------
DB_PATH = "greenhouse.db"

# ---------------------------------------------------------------------------
# Alert thresholds (hours of continuous failure before alerting)
# ---------------------------------------------------------------------------
ALERT_THRESHOLDS = {
    "open_meteo": 5.0,
    "shelly_ht": 2.0,
    "ambient_weather": 5.0,
    "shelly_3em": 1.0,      # 1 hour (monitoring device, not critical)
    "shelly_relay": 0.0,    # immediate
    "kasa_circ_fans": 0.0,  # immediate
    "minisplit": 0.0,       # immediate
    "shades": 0.0,          # immediate
}
