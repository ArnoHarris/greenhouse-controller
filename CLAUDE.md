# Greenhouse Predictive Climate Controller

## Project Overview

Predictive climate control system for a greenhouse, replacing reactive thermostats and timers. Uses a thermal model combined with weather forecasts to proactively manage indoor temperature via three actuators: exterior shades, exhaust fans, and HVAC minisplit. Runs on a Raspberry Pi 4.

The core idea: instead of reacting when temperature crosses a threshold, predict where temperature is heading based on forecast solar irradiance and outdoor conditions, and intervene early. Deploy shades before the greenhouse gets hot, not after.

## Control Strategy

Python service running a 5-minute control loop:
1. Read all sensors (indoor temp/humidity, outdoor conditions, solar irradiance)
2. Pull solar irradiance + temperature forecasts from Open-Meteo API
3. Apply bias correction to forecast using real-time AmbientWeather station readings
4. Run lumped-parameter thermal model forward 2-6 hours to predict indoor temperature trajectory
5. Apply rule-based priority logic to decide actuator actions: shades first (free, preventive) → fans (free cooling when outdoor temp permits) → HVAC (last resort)
6. Execute commands and log everything to SQLite

Seasonal behavior emerges naturally from the forecast: summer's high solar loads trigger earlier shade deployment; winter allows solar gain before intervention. No explicit seasonal rules needed.

This is NOT full MPC optimization. It's rule-based control informed by model predictions. Full optimization solver only worth pursuing if this approach proves insufficient.

## Hardware

### Controller
- **Raspberry Pi 4 Model B, 4GB RAM** (CanaKit Starter PRO Kit)
- Wired Ethernet connection (main LAN, not IoT VLAN)
- Raspberry Pi OS (64-bit)
- Headless operation (no monitor/keyboard)

### Actuators

**Exterior Shades:** WEFFORT motorized shades via Dooya Pro Hub DD7006
- 8 individual roof shades: 4 east (shades 1-4), 4 west (shades 5-8)
- Ridge runs N-S, so east shades block morning sun, west shades block afternoon sun
- Commanded as east group and west group (individual MACs in config.py)
- No gable-end shades
- Supports positional control (0-100%) as well as open/close — controller uses binary open/closed for simplicity
- Hub has 433.92 MHz RF to motors, Ethernet + Wi-Fi connectivity
- Control: `motionblinds` Python library (`pip install motionblinds`), local UDP protocol
- Key: 16-character string from Motion Blinds app (Settings → About → tap 5 times), stored in `.env` as `MOTION_GATEWAY_KEY`
- Hub IP stored in `.env` as `MOTION_GATEWAY_IP` (environment-specific; on IoT VLAN in production)

**Exhaust Fans:** 2x 14" exhaust fans (~2000 cfm total) on south gable, controlled via Shelly Plus 1 PM relay
- Intake: 4x 2'x2' louver vents on north gable, pulling air through adjacent flower shed
- Flower shed is unconditioned with concrete floor and open east side — provides pre-cooling in summer
- Control: Shelly Gen2+ local RPC API (`/rpc/Switch.Set`, `/rpc/Switch.GetStatus`)
- Includes power monitoring (watts, energy, voltage, current)
- Cleanest integration point in the system

**HVAC Minisplit:** Mitsubishi MSZ-WR18NA 1.5-ton
- Remove existing Kumo Cloud Wi-Fi adapter from CN105 port
- Replace with ESP32-WROOM-32D pre-assembled board from Tindie ("CN105 for Mitsubishi air conditioner" by "Home automation devices for Home Assistant")
- Powered by CN105 5V rail (pin 3)
- Firmware: echavet/MitsubishiCN105ESPHome via ESPHome
- Control: MQTT (publish commands, subscribe to state)
- Baud rate: likely 2400 for WR series (try 4800 if connection fails)
- CN105 port location: top edge of indoor unit alongside power button/volume rocker

### Sensors

**Indoor:** Shelly H&T Gen3 (temperature + humidity)
- Battery device — sleeps between readings, even on USB power
- Primary data path: MQTT (device pushes to Mosquitto on Pi during brief wake cycles)
- Fallback: Shelly Cloud API (POST to `/device/status` with MAC-based device ID)
- Last resort: thermal model predicted indoor temperature (handled by resilience layer)
- MQTT topic prefix: `shellyhtg3-<mac>` (publishes to `status/temperature:0`, `status/humidity:0`)
- Cloud device ID format: MAC address only (e.g., `e4b323311d58`), not the full MQTT client ID
- Humidity data enables smarter fan vs. HVAC decisions (avoid pulling humid outdoor air that HVAC must dehumidify)

**Outdoor:** AmbientWeather WS-2902 weather station (already owned)
- Data via AmbientWeather.net REST API
- Provides: outdoor temp, humidity, solar irradiance, wind speed, barometric pressure, rain
- Eliminates need for separate pyranometer
- Enables real-time forecast bias correction

**Forecast:** Open-Meteo API (free, no key required)
- Solar irradiance + temperature forecasts, 6 hours ahead

### Dashboard Display
- **Amazon Fire HD 10 tablet** wall-mounted with 3D-printed bracket (10-15 degree forward tilt)
- Fully Kiosk Browser app for full-screen mode, auto-wake, screen dimming
- On main LAN WiFi (not IoT VLAN)
- USB-C powered, right-angle cable for flush wall mounting

## Network Architecture

### VLAN Setup (UniFi UCG Ultra)
- **Main LAN:** Pi, personal devices, Fire tablet
- **IoT VLAN ("IOT Sandbox" zone):** All IoT devices (Shelly, ESP32, WEFFORT hub, Rachio, ecobee, Dyson, Kasa, SimpliSafe)

### Firewall Rules (Zone-Based)
- IOT Sandbox is a custom zone (blocks all traffic to other zones by default)
- Allow: Internal → IOT Sandbox (all traffic, so Pi/phone/laptop can reach IoT devices)
- Allow: IOT Sandbox → Internal, Pi IP only, TCP port 1883 (MQTT from ESP32 to Mosquitto)
- Allow: IOT Sandbox → External (internet access for cloud-dependent IoT devices)
- Allow: IOT Sandbox → Gateway (DNS port 53, DHCP ports 67-68)
- Allow: VPN → IOT Sandbox (remote access to IoT devices)

### Device Addressing
- DHCP reservations (fixed IP per MAC address) — NOT mDNS
- All device IPs mapped in config file

## Software Architecture

### Tech Stack
- Python 3 (standalone service, no Home Assistant)
- paho-mqtt (ESP32/minisplit communication)
- python-dotenv (load secrets from .env file)
- requests (HTTP: weather API, Shelly devices, Open-Meteo)
- tinytuya (only if WEFFORT hub uses Tuya ecosystem)
- Flask (dashboard web app)
- SQLite (data logging)
- Mosquitto (MQTT broker on Pi)
- Chart.js (browser-side charting for dashboard)
- ESPHome (ESP32 firmware)

### File Structure
```
/home/pi/greenhouse/
    main.py                # entry point, main loop, orchestration
    state.py               # GreenhouseState dataclass
    thermal_model.py       # model equations, forward simulation
    controller.py          # control logic, decision rules
    resilience.py          # DeviceHealth tracker, retry_with_fallback wrapper
    alerts.py              # alert(message, severity), pluggable transports (email, SMS, dashboard)
    devices/
        shelly_ht.py       # Shelly H&T sensor reads
        shelly_relay.py    # Shelly relay commands
        minisplit.py        # ESP32/CN105 MQTT interface
        weather_station.py  # AmbientWeather API
        shades.py           # WEFFORT hub interface
    forecast.py            # Open-Meteo API + bias correction
    config.py              # setpoints, device IPs, tuning params
    .env                   # API keys, passwords (NOT in git, loaded via python-dotenv)
    logger.py              # data logging to SQLite
    web/
        app.py             # Flask dashboard (separate process)
        templates/
            dashboard.html
        static/
            style.css
            dashboard.js
```

### State Object
```python
@dataclass
class GreenhouseState:
    indoor_temp: float
    indoor_humidity: float
    outdoor_temp: float
    outdoor_humidity: float
    solar_irradiance: float
    wind_speed: float
    shades_east: str          # "open" or "closed" (4 shades, commanded together)
    shades_west: str          # "open" or "closed" (4 shades, commanded together)
    fan_on: bool
    hvac_mode: str            # "off", "cool", "heat", "auto"
    hvac_setpoint: float
    timestamp: datetime
```
Data container only, no methods. All components read/write to it.

### Device Interface Pattern
Organize by device (not read vs. control). Each device object encapsulates all communication with that device:
- ShellyHT: reads temperature + humidity via MQTT (primary) or Shelly Cloud API (fallback)
- ShellyRelay: reads state + issues on/off commands via Gen2+ RPC API
- Minisplit: subscribes to MQTT state topics + publishes commands
- WeatherStation: polls AmbientWeather REST API
- Shades: handles hub protocol (TBD)

### Main Loop (main.py)
```python
while True:
    state = read_all_sensors()          # uses retry_with_fallback per device
    forecast = get_corrected_forecast(state)  # falls back to stale forecast
    trajectories = thermal_model.predict(state, forecast)
    actions = controller.decide(state, trajectories)
    actions = apply_override_filter(actions)   # skip actuators with active overrides
    execute_actions(actions)
    check_alert_thresholds()            # send alerts if devices down too long
    log(state, actions)
    update_heartbeat()                  # so dashboard knows controller is alive
    time.sleep(300)  # 5 minutes
```

### Thermal Model / Control Logic Separation
- **Thermal model:** Takes current state + forecast → produces predicted temperature trajectories (array of future temps for each actuator combination)
- **Control logic:** Looks at trajectories → decides what to do
- This separation allows tuning control thresholds independently from model parameters, and visualizing predictions even during manual override

## Greenhouse Physical Specs

### Dimensions
- Floorplate: 29' x 14.75' (427.75 ft² / 39.74 m²)
- Eave height: 7.6', ridge height: 13.1', roof pitch: 8.5/12
- Rafter length: 9.20' (calculated from pitch)
- Enclosed volume: 4,426 ft³ (125.3 m³)
- Ridge heading: ~295° (WNW). We refer to sides as N/S/E/W but the building is rotated ~25° CCW from true cardinal directions. "East" roof face actually faces ~NNE, "west" face ~SSW.

### Envelope
- **Glazing:** 6mm single-pane tempered glass (U ≈ 5.8 W/m²K, τ ≈ 0.82)
- **East roof:** 29' x 9.20' = 266.8 ft² (24.8 m²)
- **West roof:** 29' x 9.20' = 266.8 ft² (24.8 m²)
- **East side wall:** 29' x 7.6' = 220.4 ft² (20.5 m²)
- **West side wall:** 29' x 7.6' = 220.4 ft² (20.5 m²)
- **South gable:** 152.7 ft² (14.2 m²)
- **North gable:** 152.7 ft² (14.2 m²) — shared with flower shed (buffer zone)
- Total outdoor-exposed: 104.7 m² (excludes north wall)

### Foundation & Floor
- 2' x 8" concrete perimeter wall (structural, significant thermal mass)
- Mixed floor: concrete center aisle, gravel beds on sides

### Ventilation
- 6 ridge vents + 6 side vents (hydraulic, temperature-actuated — passive)
- 2x 14" exhaust fans on south gable (~2000 cfm total, 0.94 m³/s)
- 4x 2'x2' louver vents on north gable (fan intake)

### Adjacent Structure (Flower Shed)
- North gable connects to unconditioned "flower shed"
- Concrete floor, open east side, partial shelter
- Exhaust fans pull outdoor air through flower shed before entering greenhouse
- Provides pre-cooling effect in summer (concrete thermal mass + shade)

### HVAC
- Mitsubishi MSZ-WR18NA 1.5-ton (18,000 BTU) minisplit on north gable wall

## Thermal Model Design

### Structure
Lumped-parameter system, 2-3 nodes, Euler integration at ~1 minute steps:
- **Air node** (required): indoor air temperature
- **Soil/thermal mass node** (required): thermal flywheel, absorbs heat during day, releases at night
- **Canopy node** (optional, add if significant plant loading)

### Air Node Energy Balance
```
C_air * dT_air/dt = Q_solar_transmitted
                    - U_envelope * A * (T_air - T_outside)
                    - Q_ventilation
                    - Q_latent
                    + Q_HVAC
                    + Q_ground_exchange
```

Where:
- Q_solar_transmitted = I_solar * tau_cover * (roof_solar_east + roof_solar_west + wall_solar)
  - East/west roof shades reduce solar on their respective face independently
- Q_ventilation = rho * c_p * fan_flow * (T_air - T_outside), fan_flow = 0.94 m³/s when fans on
- tau_cover = cover transmittance (0.82 for 6mm single-pane glass)

### Calibration Strategy

Two-tier approach: lightweight online tracking + periodic offline fitting.

**Online accuracy tracking (every cycle):**
- Each cycle, compare the 5-minute-ahead prediction from the previous cycle against the actual indoor temp reading
- Log predicted vs. actual to `model_accuracy` table in SQLite
- Compute rolling RMSE and mean bias over 24h/7d windows
- Display on dashboard — gives immediate visibility into model drift
- Log accuracy summary to console every cycle (when sufficient data available)

**Offline parameter fitting (periodic):**
- Run standalone `fit_model.py` script against SQLite logs when accuracy degrades
- Fit U_envelope*A, thermal mass capacity, ground coupling, and solar fraction to minimize prediction error over a 1-2 week window of data
- Use `scipy.optimize.minimize` with parameter bounds
- Parameters change slowly (weeks/months): plant growth, cover degradation, seasonal sun angles
- No auto-tuning for now — manual fitting where results can be inspected
- Consider weekly auto-fit cron job with sanity checks only after manual process is well understood

**Why not continuous auto-tuning?** A sensor glitch or unusual event (door left open, equipment failure) could corrupt parameters. Outlier rejection and stability checks add complexity that isn't justified until the manual process proves the model is fundamentally sound.

**Empirical tuning outperforms theoretical parameter estimation.** The calculated parameters in config.py are starting estimates from geometry. Real-world performance will differ due to infiltration, thermal bridges, ground coupling uncertainty, and microclimate effects.

### Early Data Collection Strategy
Connect Shelly H&T, AmbientWeather station, and Open-Meteo API early — before actuators or full control logic are ready. Begin capturing sensor data to SQLite immediately. Use this real data to:
- Fit and validate thermal model coefficients against observed indoor temperature trajectories
- Evaluate Open-Meteo forecast accuracy over different time horizons
- Assess bias correction effectiveness
- Build confidence in model predictions before enabling automated control

### Forecast Bias Correction
Each control cycle:
1. Compare Open-Meteo's current-hour prediction against actual AmbientWeather station readings
2. Calculate delta (flat delta approach for v1)
3. Apply delta uniformly to forecast's next few hours
4. Use corrected forecast as input to thermal model

Flat delta is a known simplification (forecast error grows with horizon). Revisit with tapered or scaled correction if v1 accuracy proves insufficient.

## Resilience & Fault Tolerance

### Design Principles
- Every HTTP request uses aggressive timeouts: 3-5 second connect, 5-10 second read
- On failure, retry once after a 2-second delay (catches transient network blips)
- If still failing, use fallback value and move on — the 5-minute loop is the backoff
- Never block the main loop waiting for an unresponsive device

### DeviceHealth Tracker (`resilience.py`)
Per-device health tracking with:
- Last successful contact timestamp
- Last known good value
- Consecutive failure count
- Alert-sent flag (prevents repeated alerts every cycle)

Wrapper: `retry_with_fallback(device_call, fallback_value, device_name)` handles timeout, retry, fallback, and health updates.

### Fallback Rules

**Open-Meteo forecast unavailable:**
- Use the most recent successfully fetched forecast
- Alert after 5 hours of continuous failure

**Shelly H&T (indoor sensor) unreachable:**
- Priority: MQTT cached data (if < 10 minutes old) → Shelly Cloud API → last known value → thermal model prediction
- Alert after 2 hours of continuous failure across all sources

**AmbientWeather station unavailable:**
- Use Open-Meteo current conditions data for outdoor temp, humidity, solar irradiance
- Alert after 5 hours of continuous failure

**Actuator controls unreachable (shades, fan relay, minisplit):**
- Alert immediately on failure
- Proceed with the assumption that the device is in its last known state
- Controller continues making decisions for other actuators normally

### MQTT Resilience (Minisplit)
paho-mqtt manages its own connection and reconnection. Use:
- `on_disconnect` callback to update DeviceHealth
- `reconnect_delay_set(min_delay=5, max_delay=300)` for built-in exponential backoff
- Controller checks "is MQTT connected and have I received a state update recently" rather than actively retrying

## Alerts (`alerts.py`)

### Channels
Three alert channels, all pluggable via an `alert(message, severity)` function:
- **Email:** SMTP (Gmail app password or similar) — push notifications to phone
- **SMS:** Twilio, Amazon SNS, or email-to-SMS gateway
- **Dashboard:** Alerts stored in SQLite, displayed on Flask dashboard with timestamps

### Severity Levels
- **warning:** informational, dashboard-only (e.g., single failed sensor read)
- **alert:** email + dashboard (e.g., device down for extended period)
- **critical:** email + SMS + dashboard (e.g., multiple systems down, HVAC stuck)

### Alert Thresholds
| Device | Alert After | Severity |
|--------|------------|----------|
| Open-Meteo forecast | 5 hours | alert |
| Shelly H&T | 2 hours | alert |
| AmbientWeather station | 5 hours | alert |
| Any actuator | immediate | alert |
| Controller repeated restarts | 3 restarts in 1 hour | critical |

## Overrides

### Data Structure
```python
@dataclass
class Override:
    actuator: str          # "shades", "fan", "hvac"
    command: dict          # {"position": "closed"}, {"on": True}, {"mode": "cool", "setpoint": 72}
    expires_at: datetime
    source: str            # "dashboard", "api"
    created_at: datetime
```

### Behavior
- Per-actuator: overriding shades does not affect controller management of fans or HVAC
- **Default duration: 2 hours**, configurable on dashboard (longer durations available)
- **SQLite-backed:** overrides survive controller restarts
- **Latest wins:** a new override on the same actuator replaces the previous one (old one logged)
- **Immediate execution:** dashboard sends command to device immediately, does not wait for next control cycle
- **Expiration:** when override expires, controller resumes normal control on next cycle

### Override Flow
1. User taps override button on dashboard
2. Flask endpoint writes override to SQLite AND immediately executes command against device
3. Controller checks for active (non-expired) overrides before acting on each actuator
4. If active override exists for an actuator, controller skips that actuator
5. Dashboard shows active overrides with time remaining and a **Cancel** button
6. Cancel removes override from SQLite; controller resumes normal control next cycle

## Startup & Safe Defaults

On startup (including after crash/restart by systemd):
- **Read actual device states** from hardware — do not assume any default positions
- If a device is unreachable at startup, assume safe defaults: shades open, fans off, HVAC off
- Check SQLite for any active (non-expired) overrides and respect them
- Log startup event with timestamp and initial device states

## Data Logging (SQLite)

Log every control cycle (every 5 minutes):
- **Measured state:** indoor temp/humidity, outdoor temp/humidity, solar irradiance, wind speed
- **Actuator positions:** shade state, fan state, HVAC mode/setpoint (both commanded and confirmed)
- **Raw forecast:** Open-Meteo values as received
- **Bias-corrected forecast:** after applying station-based correction
- **Model predictions:** predicted indoor temp trajectory + actuator assumptions used
- **Control decisions:** action chosen + reason string (e.g., "shades deployed: predicted temp exceeds 85F within 90 minutes")
- **Model parameters:** current coefficient values (for traceability if doing online adaptation)
- **Overrides:** manual override decisions with timestamp, actuator, position, source="manual", duration

Store predicted trajectories as JSON blobs in single SQLite column.

Store overrides and alerts in dedicated SQLite tables (not just the main log).

**Enable WAL mode** (`PRAGMA journal_mode=WAL`) to allow the controller and Flask dashboard to read/write concurrently without locking issues.

At ~100,000 rows/year (1 per 5-min cycle), SQLite handles this trivially.

## Dashboard (Flask)

### Real-Time Display
- Current indoor temp/humidity (prominent)
- Outdoor conditions
- Shade/fan/HVAC status with color indicators
- Predicted temperature trajectory chart (next few hours)
- Override buttons with large tap targets (open/close shades, fan on/off, HVAC mode)
- Override duration: default 2 hours, configurable to longer on dashboard
- Active overrides shown with time remaining and **Cancel** button
- Alert banner for active system alerts (device failures, etc.)

### Dashboard Independence
The dashboard must function even when the controller process is down:
- Flask detects controller status via heartbeat timestamp in SQLite
- If controller is offline, displays warning banner ("Controller offline — manual mode only")
- Override buttons still work — Flask calls device APIs directly using the same device modules
- Overrides written to SQLite as usual, so controller respects them when it comes back
- Device communication code must be importable by both controller and Flask app

### Historical Charts (Chart.js via AJAX to Flask JSON endpoints)
- **Temperature overlay:** actual indoor temp vs. model prediction (primary accuracy diagnostic)
- **Solar forecast vs. actual:** Open-Meteo forecast vs. AmbientWeather station
- **Actuator timeline:** horizontal bars showing when shades deployed, fans running, HVAC active
- **HVAC runtime:** hours per day/week (energy cost proxy)
- **Time ranges:** last 24 hours, 7 days, 30 days
- Visually distinguish override periods (background shading or different color)

### Security
- Flask-Login with username/password
- Prevents unauthorized override commands

## ESP32 Firmware (ESPHome)

### Configuration (minisplit.yaml)
```yaml
esphome:
  name: greenhouse-minisplit
  friendly_name: Greenhouse Minisplit

esp32:
  board: esp32dev
  framework:
    type: esp-idf

wifi:
  ssid: "iot_wifi_ssid"
  password: "iot_wifi_password"
  ap:
    ssid: "Minisplit Fallback"
    password: "fallback_password"

captive_portal:
logger:
  level: INFO

api:
  encryption:
    key: "generate_random_key"

ota:
  platform: esphome
  password: "your_ota_password"

mqtt:
  broker: PI_IP_ADDRESS
  username: "mqtt_user"
  password: "mqtt_password"

uart:
  id: HP_UART
  baud_rate: 2400
  tx_pin: GPIO17
  rx_pin: GPIO16

external_components:
  - source: github://echavet/MitsubishiCN105ESPHome

climate:
  - platform: cn105
    name: "Greenhouse Minisplit"
    id: hp
    update_interval: 2s
```

### Flashing
- First flash: USB from development computer (`esphome run minisplit.yaml`, select serial port)
- Subsequent updates: OTA over Wi-Fi (`esphome run minisplit.yaml`, select network)
- Verify before physical install: check DHCP client list, stream logs, verify MQTT messages

## MQTT Configuration (Mosquitto)

Running on Pi as system service. Authentication required:
```
listener 1883
allow_anonymous false
password_file /etc/mosquitto/passwd
```

## Security Notes

- MQTT: username/password authentication on Mosquitto
- Flask: Flask-Login for dashboard
- SSH: key-based authentication on Pi (disable password auth)
- API keys: stored in .env file, loaded via python-dotenv, added to .gitignore, never committed
- Router: no inbound ports opened. VPN for remote access if needed.
- IoT VLAN isolation via UniFi zone-based firewall

## Development Workflow

- Write code on Windows development machine using Claude Code + VS Code with Remote-SSH
- Git repo with GitHub remote for backup and deployment
- Deploy to Pi: `git clone` initially, then `git pull` for updates
- Test on Pi via SSH: run `python3 main.py` manually, watch output
- When stable: register as systemd service for unattended operation
- .env file must be created manually on each machine (not in git)

## Coding Conventions

- Python 3, no type hints required but welcome
- Dataclasses for structured data
- Device modules encapsulate all communication with their device
- Config values in config.py, secrets in .env (loaded via python-dotenv, accessed with os.getenv())
- Logging to SQLite, not flat files
- No Home Assistant dependency anywhere in the control path
- Flask dashboard is a separate process from the controller
