# System Architecture Details

## Resilience & Fault Tolerance

### Design Principles
- Every HTTP request uses aggressive timeouts: 3–5 second connect, 5–10 second read
- On failure, retry once after a 2-second delay (catches transient network blips)
- If still failing, use fallback value and move on — the 5-minute loop is the backoff
- Never block the main loop waiting for an unresponsive device

### DeviceHealth Tracker (`resilience.py`)
Per-device health tracking:
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
- Use Open-Meteo current conditions for outdoor temp, humidity, solar irradiance
- Alert after 5 hours of continuous failure

**Actuator controls unreachable (shades, fan relay, minisplit):**
- Alert immediately on failure
- Assume device is in last known state; continue managing other actuators normally

### MQTT Resilience (Minisplit)
paho-mqtt manages its own reconnection:
- `on_disconnect` callback updates DeviceHealth
- `reconnect_delay_set(min_delay=5, max_delay=300)` for exponential backoff
- Controller checks "is MQTT connected and have I received a state update recently"

## Alerts (`alerts.py`)

### Channels
Three pluggable channels via `alert(message, severity)`:
- **Email:** SMTP (Gmail app password) — push notifications to phone
- **SMS:** Twilio, Amazon SNS, or email-to-SMS gateway
- **Dashboard:** Alerts stored in SQLite, displayed with timestamps

### Severity Levels
- **warning:** dashboard-only (e.g., single failed sensor read)
- **alert:** email + dashboard (e.g., device down for extended period)
- **critical:** email + SMS + dashboard (e.g., multiple systems down, HVAC stuck)

### Alert Thresholds
| Device                      | Alert After          | Severity |
|-----------------------------|----------------------|----------|
| Open-Meteo forecast         | 5 hours              | alert    |
| Shelly H&T                  | 2 hours              | alert    |
| AmbientWeather station      | 5 hours              | alert    |
| Any actuator                | immediate            | alert    |
| Controller repeated restarts| 3 restarts in 1 hour | critical |

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
- **Default duration:** 2 hours for shades/HVAC; 5 minutes for exhaust fans (fans left running can overheat in winter)
- Configurable to longer durations on dashboard
- **SQLite-backed:** overrides survive controller restarts
- **Latest wins:** a new override on the same actuator replaces the previous one
- **Immediate execution (planned):** dashboard will send command to device immediately, not wait for next cycle. Currently Flask writes to SQLite only.
- **Expiration:** controller resumes normal control on next cycle after expiry

### Override Flow
1. User taps override button on dashboard
2. Flask endpoint writes override to SQLite (and will immediately execute device command once controller.py is implemented)
3. Controller checks for active (non-expired) overrides before acting on each actuator
4. If active override exists for an actuator, controller skips that actuator
5. Dashboard shows active overrides with time remaining and a **Cancel** button
6. Cancel removes override from SQLite; controller resumes normal control next cycle

## Startup & Safe Defaults

On startup (including after crash/restart by systemd):
- **Read actual device states** from hardware — do not assume any defaults
- If a device is unreachable at startup, assume safe defaults: shades open, fans off, HVAC off
- Check SQLite for any active (non-expired) overrides and respect them
- Log startup event with timestamp and initial device states

## Data Logging (SQLite)

Log every control cycle (every 5 minutes):
- **Measured state:** indoor temp/humidity, outdoor temp/humidity, solar irradiance, wind speed
- **Actuator positions:** shade state, fan state, HVAC mode/setpoint (commanded and confirmed)
- **Raw forecast:** Open-Meteo values as received
- **Bias-corrected forecast:** after applying station-based correction
- **Model predictions:** predicted indoor temp trajectory + actuator assumptions used
- **Control decisions:** action chosen + reason string
- **Model parameters:** current coefficient values (for traceability)
- **Overrides:** timestamp, actuator, position, source="manual", duration

Store predicted trajectories as JSON blobs in a single SQLite column.
Store overrides and alerts in dedicated SQLite tables.
Enable WAL mode (`PRAGMA journal_mode=WAL`) so controller and Flask can read/write concurrently.
At ~100,000 rows/year, SQLite handles this trivially.
