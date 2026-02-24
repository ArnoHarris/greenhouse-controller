# Dashboard Specification

Four-page dark-themed Flask app, optimized for Fire HD 10 tablet.
Entry point: `web/app.py` (separate process from controller).

## Page: Greenhouse (`dashboard.html`)

Current conditions and manual controls:
- Indoor temp/humidity (prominent)
- Current outdoor conditions with weather icon; 2-hour forecast (temp, icon, wind, humidity)
- Shade/fan/HVAC status with color indicators
- Predicted temperature trajectory chart
- Override buttons with large tap targets:
  - Shades: open / close (default 2-hour duration)
  - Exhaust fans: on / off (default 5-minute duration — prevents accidental winter overheat)
  - Circulating fans: on / off
  - HVAC: mode selector + setpoint (default 2-hour duration)
- Override duration configurable to longer values on dashboard
- Active overrides shown with time remaining and **Cancel** button
- Alert banner for active system alerts (device failures, etc.)
- Controller offline banner if heartbeat is stale (> 10 minutes)

## Page: History (`history.html`)

Historical trends with time-range selector (1h, 24h, 7d, 30d) and period navigation (‹ ›):
- **Temperature overlay:** actual indoor vs. outdoor temp
- **Solar forecast vs. actual:** Open-Meteo forecast vs. AmbientWeather station readings
- **Actuator timeline:** horizontal bars showing when shades deployed, fans running, HVAC active

## Page: Energy (`energy.html`)

Power monitoring from Shelly Pro 3EM, with time-range selector and period navigation:
- Current power gauge (Phase A + Phase B total, kW)
- Phase A and Phase B current readings (kW each)
- Voltage and frequency
- Power over time chart (kW)
- Cumulative energy chart (kWh per interval)
- Data from `power_log` SQLite table

## Page: Diagnostic (`diagnostic.html`)

Model accuracy and system health, with time-range selector (1h, 24h, 7d, 30d) and period navigation (‹ ›):
- **Temperature overlay:** actual vs. model prediction; RMSE, bias, n stats in header
- **Solar irradiance:** forecast vs. actual
- **Actuator timeline:** same as History page
- **HVAC runtime:** hours/day bar chart

## Dashboard Independence

Dashboard must function even when the controller process is down:
- Flask detects controller status via heartbeat timestamp in SQLite
- If offline: displays "Controller offline — manual mode only" banner
- Override buttons write to SQLite; device command execution happens at next controller cycle (or immediately once `controller.py` is implemented)
- Device communication modules must be importable by both controller and Flask

## Security

- Flask-Login with username/password (planned — not yet implemented)
- Currently no authentication; do not expose to untrusted networks
- MQTT: username/password on Mosquitto
- SSH: key-based auth on Pi; disable password auth
- API keys: `.env` file, never committed to git
- No inbound router ports; VPN for remote access
