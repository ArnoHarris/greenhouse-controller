# Greenhouse Predictive Climate Controller

## Project Overview

Predictive climate control for a greenhouse. Uses a lumped-parameter thermal model + weather forecasts to proactively manage indoor temperature via three actuators: exterior shades, exhaust fans, and HVAC minisplit. Runs on a Raspberry Pi 4. Core idea: deploy shades *before* the greenhouse gets hot, not after.

## Control Strategy

Python service, 5-minute control loop:
1. Read all sensors (indoor temp/humidity, outdoor conditions, solar irradiance)
2. Pull solar irradiance + temperature forecasts from Open-Meteo API
3. Apply bias correction to forecast using real-time AmbientWeather station readings
4. Run thermal model forward 2–6 hours to predict indoor temperature trajectory
5. Apply rule-based priority logic: shades first → fans (free cooling) → HVAC (last resort)
6. Execute commands and log everything to SQLite

Rule-based control informed by predictions — not full MPC optimization.

## Implementation Status

**Built (data collection phase):** Steps 1–4 operational. Sensor reads, forecast fetch, bias correction, thermal model, full Flask dashboard (4 pages), SQLite logging.

**Not yet built:** `controller.py` (steps 5–6), `alerts.py`, `devices/minisplit.py`. Flask-Login auth is planned but not implemented.

## File Structure

```
/home/pi/greenhouse/
    main.py                # entry point, main loop, orchestration (data collection mode)
    state.py               # GreenhouseState dataclass
    thermal_model.py       # model equations, forward simulation
    resilience.py          # DeviceHealth tracker, retry_with_fallback wrapper
    forecast.py            # Open-Meteo API + bias correction
    config.py              # setpoints, device IPs, tuning params
    .env                   # API keys, passwords (NOT in git, loaded via python-dotenv)
    logger.py              # data logging to SQLite
    devices/
        shelly_ht.py       # Shelly H&T sensor reads (MQTT primary, cloud fallback)
        shelly_relay.py    # Shelly Plus 1 PM relay commands (exhaust fans)
        shelly_3em.py      # Shelly Pro 3EM power meter (Gen2 RPC API)
        kasa_switch.py     # Kasa HS210 smart switch (circulating fans, async wrapped)
        weather_station.py # AmbientWeather API
        shades.py          # WEFFORT hub via motionblinds library
    web/
        app.py             # Flask dashboard (separate process)
        templates/
            base.html      # shared layout, navigation
            dashboard.html # Greenhouse page: current conditions, controls, overrides
            history.html   # History page: temperature/solar/actuator charts
            energy.html    # Energy page: power meter charts, current readings
            diagnostic.html # Diagnostic page: model accuracy, actuator timeline
        static/
            style.css
            dashboard.js

# Not yet implemented (planned):
    controller.py          # control logic, decision rules
    alerts.py              # alert(message, severity), pluggable transports
    devices/minisplit.py   # ESP32/CN105 MQTT interface
```

## State Object

```python
@dataclass
class GreenhouseState:
    indoor_temp: float
    indoor_humidity: float
    outdoor_temp: float
    outdoor_humidity: float
    solar_irradiance: float
    wind_speed: float
    shades_east: str          # "open" or "closed"
    shades_west: str          # "open" or "closed"
    fan_on: bool
    circ_fans_on: bool
    hvac_mode: str            # "off", "cool", "heat", "auto"
    hvac_setpoint: float
    timestamp: datetime
```

Data container only, no methods. All components read/write to it.

## Device Interface Pattern

One module per device, encapsulates all communication:
- `ShellyHT` — indoor temp/humidity via MQTT (primary) or Shelly Cloud API (fallback)
- `ShellyRelay` — exhaust fan relay via Gen2+ RPC (`/rpc/Switch.Set`, `/rpc/Switch.GetStatus`)
- `Shelly3EM` — power meter via Gen2 RPC (`/rpc/EM.GetStatus`, `/rpc/EMData.GetStatus`)
- `KasaSwitch` — circulating fans via python-kasa async API (wrapped with `asyncio.run()`)
- `WeatherStation` — outdoor conditions via AmbientWeather REST API
- `Shades` — WEFFORT shade hub via `motionblinds` Python library (local UDP)
- `Minisplit` — MQTT subscribe/publish to ESPHome ESP32 on CN105 port (not yet implemented)

See `docs/hardware.md` for device-specific details (IPs, topics, API endpoints, fallback rules).

## Tech Stack

Python 3 · Flask · SQLite (WAL mode) · Chart.js · paho-mqtt · python-dotenv · requests · motionblinds · python-kasa · Mosquitto (MQTT broker on Pi) · ESPHome (ESP32 firmware, planned)

## Coding Conventions

- Python 3, no type hints required but welcome
- Dataclasses for structured data
- Device modules encapsulate all communication with their device
- Config values in `config.py`, secrets in `.env` (via python-dotenv, `os.getenv()`)
- Logging to SQLite, not flat files
- No Home Assistant dependency anywhere in the control path
- Flask dashboard is a separate process from the controller

## Development Workflow

- Code on Windows, deploy to Pi via `git pull`
- Service name: `greenhouse.service` (not `greenhouse-web.service`)
- "commit" means commit AND push
- Do not commit until explicitly asked
- `.env` must be created manually on each machine (not in git)
- Test on Pi via SSH: `python3 main.py` manually; promote to systemd when stable

## Reference Docs

- [`docs/hardware.md`](docs/hardware.md) — device specs, APIs, wiring, sensor fallback hierarchy
- [`docs/thermal-model.md`](docs/thermal-model.md) — greenhouse physical specs, model equations, calibration
- [`docs/architecture.md`](docs/architecture.md) — resilience, alerts, overrides, data logging, startup defaults
- [`docs/dashboard.md`](docs/dashboard.md) — dashboard page specs, override UX, alert display
- [`docs/network-config.md`](docs/network-config.md) — VLAN, firewall rules, ESP32 ESPHome YAML, Mosquitto config
