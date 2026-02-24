# Hardware Reference

## Controller

- **Raspberry Pi 4 Model B, 4GB RAM** (CanaKit Starter PRO Kit)
- Wired Ethernet connection (main LAN, not IoT VLAN)
- Raspberry Pi OS (64-bit), headless operation

## Actuators

### Exterior Shades — WEFFORT via Dooya Pro Hub DD7006
- 8 individual roof shades: 4 east (shades 1–4), 4 west (shades 5–8)
- Ridge runs N-S; east shades block morning sun, west shades block afternoon sun
- Commanded as east group and west group (individual MACs in config.py)
- No gable-end shades
- Supports positional control (0–100%); controller uses binary open/closed
- Hub: 433.92 MHz RF to motors, Ethernet + Wi-Fi connectivity
- Control: `motionblinds` Python library (`pip install motionblinds`), local UDP protocol
- Key: 16-character string from Motion Blinds app (Settings → About → tap 5×), stored in `.env` as `MOTION_GATEWAY_KEY`
- Hub IP: `.env` as `MOTION_GATEWAY_IP` (IoT VLAN in production)

### Exhaust Fans — Shelly Plus 1 PM relay
- 2× 14" exhaust fans (~2000 cfm total, 0.94 m³/s) on south gable
- Intake: 4× 2'×2' louver vents on north gable, pulling air through adjacent flower shed
- Flower shed is unconditioned with concrete floor and open east side — provides pre-cooling in summer
- Control: Shelly Gen2+ local RPC API (`/rpc/Switch.Set`, `/rpc/Switch.GetStatus`)
- Includes power monitoring (watts, energy, voltage, current)

### Circulating Fans — Kasa HS210 3-way smart switch
- 2× 18" circulating fans inside the greenhouse, single circuit
- Control: `python-kasa` library (async API wrapped with `asyncio.run()` for sync codebase)
- IP stored in `config.py` as `KASA_CIRC_FANS_IP`
- State tracked as `circ_fans_on` in GreenhouseState

### HVAC Minisplit — Mitsubishi MSZ-WR18NA 1.5-ton
- Remove existing Kumo Cloud Wi-Fi adapter from CN105 port
- Replace with ESP32-WROOM-32D pre-assembled board from Tindie ("CN105 for Mitsubishi air conditioner" by "Home automation devices for Home Assistant")
- Powered by CN105 5V rail (pin 3)
- Firmware: echavet/MitsubishiCN105ESPHome via ESPHome
- Control: MQTT (publish commands, subscribe to state)
- Baud rate: likely 2400 for WR series (try 4800 if connection fails)
- CN105 port: top edge of indoor unit alongside power button/volume rocker
- See `docs/network-config.md` for ESPHome YAML and flashing instructions

## Sensors

### Indoor — Shelly H&T Gen3 (temperature + humidity)
- Battery device — sleeps between readings, even on USB power
- **Primary:** MQTT (device pushes to Mosquitto on Pi during brief wake cycles)
  - Topic prefix: `shellyhtg3-<mac>` → `status/temperature:0`, `status/humidity:0`
- **Fallback:** Shelly Cloud API (POST to `/device/status`, device ID = MAC address only, e.g. `e4b323311d58`)
- **Last resort:** thermal model predicted indoor temperature (resilience layer)
- Alert after 2 hours of continuous failure across all sources
- Humidity data enables smarter fan vs. HVAC decisions (avoid pulling humid outdoor air)

### Outdoor — AmbientWeather WS-2902 weather station
- Data via AmbientWeather.net REST API
- Provides: outdoor temp, humidity, solar irradiance, wind speed, barometric pressure, rain
- Eliminates need for separate pyranometer; enables real-time forecast bias correction
- Fallback: use Open-Meteo current conditions for outdoor temp/humidity/solar
- Alert after 5 hours of continuous failure

### Forecast — Open-Meteo API
- Free, no API key required
- Solar irradiance + temperature forecasts, 6 hours ahead
- Alert after 5 hours of continuous failure

## Dashboard Display

- **Amazon Fire HD 10 tablet** wall-mounted with 3D-printed bracket (10–15° forward tilt)
- Fully Kiosk Browser: full-screen mode, auto-wake, screen dimming
- Main LAN WiFi (not IoT VLAN)
- USB-C powered, right-angle cable for flush wall mounting
