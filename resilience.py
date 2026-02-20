"""Device health tracking and resilient retry wrapper."""

import time
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field

import config

log = logging.getLogger(__name__)


@dataclass
class DeviceHealth:
    name: str
    last_success: datetime = None
    last_value: object = None
    consecutive_failures: int = 0
    alert_sent: bool = False

    def record_success(self, value):
        self.last_success = datetime.now()
        self.last_value = value
        self.consecutive_failures = 0
        self.alert_sent = False

    def record_failure(self):
        self.consecutive_failures += 1

    def hours_since_success(self):
        if self.last_success is None:
            return float("inf")
        delta = datetime.now() - self.last_success
        return delta.total_seconds() / 3600

    def should_alert(self):
        threshold = config.ALERT_THRESHOLDS.get(self.name)
        if threshold is None:
            return False
        if self.alert_sent:
            return False
        if threshold == 0.0 and self.consecutive_failures > 0:
            return True
        return self.hours_since_success() >= threshold

    def mark_alerted(self):
        self.alert_sent = True


# Global registry of device health trackers
_health = {}


def get_health(device_name):
    if device_name not in _health:
        _health[device_name] = DeviceHealth(name=device_name)
    return _health[device_name]


def retry_with_fallback(device_call, fallback_value, device_name):
    """Call device_call(). On failure, retry once. If still failing, return fallback.

    Updates DeviceHealth on success or failure.
    Returns (value, is_fallback) tuple.
    """
    health = get_health(device_name)

    for attempt in range(2):
        try:
            value = device_call()
            health.record_success(value)
            return value, False
        except Exception as e:
            if attempt == 0:
                log.warning("%s: attempt 1 failed (%s), retrying in %ds",
                            device_name, e, config.RETRY_DELAY)
                time.sleep(config.RETRY_DELAY)
            else:
                log.error("%s: attempt 2 failed (%s), using fallback", device_name, e)

    health.record_failure()

    # Use last known good value if available, otherwise provided fallback
    if health.last_value is not None:
        log.info("%s: using last known good value", device_name)
        return health.last_value, True

    log.info("%s: no previous value, using provided fallback", device_name)
    return fallback_value, True
