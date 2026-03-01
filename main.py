"""Greenhouse controller — predictive climate control.

Each 5-minute cycle:
  1. Read sensors (indoor temp/humidity, outdoor conditions, circulating fans)
  2. Restore last-cycle actuator state so the thermal model uses the correct inputs
  3. Fetch and bias-correct the Open-Meteo forecast
  4. Run thermal model twice: once with current state, once with shades forced open
  5. Apply rule-based control (shades → fans → HVAC stub) via controller.py
  6. Log sensor state (now reflects any actuator commands issued in step 5)
  7. Log power meter readings
"""

import copy
import os
import logging
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

import paho.mqtt.client as mqtt

import config
import controller
import forecast
import logger
import thermal_model
from resilience import retry_with_fallback, get_health
from state import GreenhouseState
from devices.shelly_ht import ShellyHT
from devices.shelly_relay import ShellyRelay
from devices.shades import ShadesController
from devices.weather_station import WeatherStation
from devices.kasa_switch import KasaSwitch
from devices.shelly_3em import Shelly3EM

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def setup_mqtt(shelly_ht):
    """Set up MQTT client to receive Shelly H&T data pushes."""
    broker_ip = os.getenv("MQTT_BROKER_IP")
    username = os.getenv("MQTT_USERNAME")
    password = os.getenv("MQTT_PASSWORD")

    if not broker_ip or broker_ip == "192.168.1.XXX":
        log.warning("MQTT broker not configured, skipping MQTT setup")
        return None

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if username:
        client.username_pw_set(username, password)

    def on_connect(client, userdata, flags, reason_code, properties):
        log.info("MQTT connected (reason: %s)", reason_code)
        # Subscribe to Shelly H&T topics
        for topic in shelly_ht.get_mqtt_topics():
            client.subscribe(topic)
            log.info("MQTT subscribed to %s", topic)

    def on_message(client, userdata, msg):
        shelly_ht.mqtt_on_message(msg.topic, msg.payload)

    def on_disconnect(client, userdata, flags, reason_code, properties):
        log.warning("MQTT disconnected (reason: %s)", reason_code)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    client.reconnect_delay_set(min_delay=5, max_delay=300)

    try:
        client.connect(broker_ip, 1883, keepalive=60)
        client.loop_start()  # runs in background thread
        log.info("MQTT client started, broker: %s", broker_ip)
    except Exception as e:
        log.error("MQTT connection failed: %s", e)
        return None

    return client


def read_all_sensors(shelly_ht, weather_station, kasa_circ_fans):
    """Read all sensors with retry/fallback. Returns a GreenhouseState."""
    state = GreenhouseState(timestamp=datetime.now(timezone.utc))

    # Indoor: Shelly H&T (reads from MQTT cache, falls back to cloud API)
    indoor, indoor_fallback = retry_with_fallback(
        shelly_ht.read, None, "shelly_ht"
    )
    if indoor:
        state.indoor_temp = indoor["temp_f"]
        state.indoor_humidity = indoor["humidity"]
    if indoor_fallback:
        log.warning("Using fallback for indoor sensor")

    # Outdoor: AmbientWeather station
    outdoor, outdoor_fallback = retry_with_fallback(
        weather_station.read, None, "ambient_weather"
    )
    if outdoor:
        state.outdoor_temp = outdoor["outdoor_temp_f"]
        state.outdoor_humidity = outdoor["outdoor_humidity"]
        state.solar_irradiance = outdoor["solar_irradiance_wm2"]
        state.wind_speed = outdoor["wind_speed_mph"]
    if outdoor_fallback:
        log.warning("Using fallback for weather station")

    # Circulating fans: Kasa HS210
    circ, circ_fallback = retry_with_fallback(
        kasa_circ_fans.read, {"on": None}, "kasa_circ_fans"
    )
    if circ and circ["on"] is not None:
        state.circ_fans_on = circ["on"]
    if circ_fallback:
        log.warning("Using fallback for circulating fans")

    return state, outdoor


def get_corrected_forecast(station_reading):
    """Fetch forecast with retry, apply bias correction if station data available.

    Returns (raw_forecast, corrected_forecast) tuple.
    """
    raw, raw_fallback = retry_with_fallback(
        forecast.fetch_forecast, None, "open_meteo"
    )

    if raw is None:
        log.error("No forecast available (no current or cached data)")
        return None, None

    if raw_fallback:
        log.warning("Using cached forecast data")

    # Apply bias correction if we have station data
    if station_reading:
        corrected = forecast.apply_bias_correction(raw, station_reading)
    else:
        corrected = raw
        log.info("No station data for bias correction, using raw forecast")

    return raw, corrected


def fill_state_from_forecast(state, corrected_forecast):
    """Fill missing outdoor conditions from forecast (fallback for weather station)."""
    if state.outdoor_temp is not None:
        return  # station data available, no need

    conditions = forecast.get_current_conditions_from_forecast(corrected_forecast)
    if conditions:
        state.outdoor_temp = conditions.get("outdoor_temp_f") or state.outdoor_temp
        state.outdoor_humidity = conditions.get("outdoor_humidity") or state.outdoor_humidity
        state.solar_irradiance = conditions.get("solar_irradiance_wm2") or state.solar_irradiance
        state.wind_speed = conditions.get("wind_speed_mph") or state.wind_speed
        log.info("Filled outdoor conditions from Open-Meteo forecast (station fallback)")


def main():
    log.info("Greenhouse controller starting")

    # Initialize sensor devices
    shelly_ht = ShellyHT()
    weather_station = WeatherStation()
    kasa_circ_fans = KasaSwitch(config.KASA_CIRC_FANS_IP)
    shelly_3em = Shelly3EM(config.SHELLY_3EM_IP)

    # Initialize actuator devices (for controller commands)
    exhaust_fan_relay = ShellyRelay(config.SHELLY_RELAY_IP, name="exhaust_fans")
    shades_ctrl = ShadesController(
        config.MOTION_GATEWAY_IP,
        os.getenv("MOTION_GATEWAY_KEY", ""),
        config.SHADES_EAST_MACS,
        config.SHADES_WEST_MACS,
    )
    try:
        shades_ctrl.connect()
        log.info("Shades controller connected")
    except Exception as e:
        log.warning("Shades controller connect failed at startup (will retry on command): %s", e)

    # Start MQTT subscriber for Shelly H&T
    mqtt_client = setup_mqtt(shelly_ht)

    # Log startup
    logger.log_startup()
    log.info("Startup logged. Entering main loop (interval: %ds)", config.POLL_INTERVAL_SECONDS)

    # Previous cycle's prediction for error tracking
    # Key: horizon_minutes → predicted_temp_f
    prev_prediction = None  # 5-minute-ahead prediction from last cycle
    prev_power_totals = None  # {"a": kWh, "b": kWh} for energy delta computation

    while True:
        cycle_start = time.time()

        try:
            # 1. Read all sensors
            state, station_reading = read_all_sensors(shelly_ht, weather_station, kasa_circ_fans)

            # 1b. Restore last-cycle actuator state so the thermal model uses the
            #     real shade/fan/HVAC state rather than GreenhouseState defaults.
            last_act = logger.get_last_actuator_state()
            if last_act:
                state.shades_east = last_act["shades_east"]
                state.shades_west = last_act["shades_west"]
                state.fan_on      = last_act["fan_on"]
                state.hvac_mode   = last_act["hvac_mode"]

            # 1c. Compare previous prediction against actual (model accuracy tracking)
            if prev_prediction is not None and state.indoor_temp is not None:
                logger.log_model_accuracy(
                    prev_prediction, state.indoor_temp, horizon_minutes=5
                )
                error = prev_prediction - state.indoor_temp
                log.info("Model accuracy: predicted %.1fF, actual %.1fF, error %+.1fF",
                         prev_prediction, state.indoor_temp, error)
            prev_prediction = None

            # 2. Fetch and correct forecast
            raw_forecast, corrected_forecast = get_corrected_forecast(station_reading)

            # 3. Fill outdoor state from forecast if weather station was down
            if corrected_forecast:
                fill_state_from_forecast(state, corrected_forecast)

            # 4. Run thermal model prediction
            trajectory = None
            if corrected_forecast and state.indoor_temp is not None:
                trajectory = thermal_model.predict(state, corrected_forecast)
                log.info("Model predicts indoor temp in 1h: %.1fF, 3h: %.1fF",
                         trajectory["air_temp_f"][min(60, len(trajectory["air_temp_f"]) - 1)],
                         trajectory["air_temp_f"][min(180, len(trajectory["air_temp_f"]) - 1)])

                # Save 5-minute-ahead prediction for next cycle's accuracy check
                poll_steps = config.POLL_INTERVAL_SECONDS // config.MODEL_STEP_SECONDS
                if poll_steps < len(trajectory["air_temp_f"]):
                    prev_prediction = trajectory["air_temp_f"][poll_steps]

            elif state.indoor_temp is None:
                log.warning("No indoor temp available, skipping model prediction")
            else:
                log.warning("No forecast available, skipping model prediction")

            # 4b. Run controller — predict with shades open, decide, execute
            if corrected_forecast and state.indoor_temp is not None:
                # Second model run: hypothetical open-shades state (used to check if
                # it's safe to open shades without exceeding the cool setpoint).
                state_open = copy.copy(state)
                state_open.shades_east = "open"
                state_open.shades_west = "open"
                trajectory_open = thermal_model.predict(state_open, corrected_forecast)

                heat_sp, cool_sp = controller.get_setpoints(config.DB_PATH)
                overridden = controller.get_active_overrides(config.DB_PATH)
                decisions = controller.decide(
                    state, trajectory, trajectory_open,
                    heat_sp, cool_sp, overridden, corrected_forecast,
                )
                controller.execute(decisions, state, shades_ctrl, exhaust_fan_relay)

            # 5. Log everything (after controller so sensor_log reflects commanded state)
            logger.log_sensors(state)
            if raw_forecast and corrected_forecast:
                logger.log_forecast(raw_forecast, corrected_forecast)
            if trajectory:
                # Downsample trajectory for storage (every 5 min instead of every 1 min)
                downsampled = {
                    "times": trajectory["times"][::5],
                    "air_temp_f": trajectory["air_temp_f"][::5],
                    "mass_temp_f": trajectory["mass_temp_f"][::5],
                }
                logger.log_model_prediction(downsampled, trajectory["params"])

            # 6. Power meter
            power, power_fallback = retry_with_fallback(
                shelly_3em.read, None, "shelly_3em"
            )
            if power:
                energy_a = energy_b = None
                if prev_power_totals is not None:
                    delta_a = power["phase_a"]["total_kwh"] - prev_power_totals["a"]
                    delta_b = power["phase_b"]["total_kwh"] - prev_power_totals["b"]
                    # Guard against meter reset or invalid reading
                    energy_a = delta_a if delta_a >= 0 else None
                    energy_b = delta_b if delta_b >= 0 else None
                prev_power_totals = {
                    "a": power["phase_a"]["total_kwh"],
                    "b": power["phase_b"]["total_kwh"],
                }
                logger.log_power(power, energy_a, energy_b)
                log.info("Power: A=%.2fkW B=%.2fkW total=%.2fkW",
                         power["phase_a"]["power_kw"] or 0,
                         power["phase_b"]["power_kw"] or 0,
                         power["total_power_kw"])
            if power_fallback:
                log.warning("Using fallback for power meter")

            # 8. Periodic accuracy summary (every ~1 hour = 12 cycles)
            accuracy_24h = logger.get_model_rmse(hours_back=24)
            if accuracy_24h and accuracy_24h["count"] >= 12:
                log.info("Model accuracy (24h): RMSE=%.1fF, bias=%+.1fF, n=%d",
                         accuracy_24h["rmse_f"], accuracy_24h["mean_bias_f"],
                         accuracy_24h["count"])

            logger.update_heartbeat()

            log.info("Cycle complete: indoor=%.1fF outdoor=%.1fF solar=%.0fW/m2",
                     state.indoor_temp or 0, state.outdoor_temp or 0,
                     state.solar_irradiance or 0)

        except Exception:
            log.exception("Unhandled error in main loop")

        # Sleep for remainder of interval
        elapsed = time.time() - cycle_start
        sleep_time = max(0, config.POLL_INTERVAL_SECONDS - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)


if __name__ == "__main__":
    mqtt_client = None
    try:
        main()
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        if mqtt_client:
            mqtt_client.loop_stop()
        logger.close()
