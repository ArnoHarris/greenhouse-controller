# Network & Device Configuration

## Network Architecture

### VLAN Setup (UniFi UCG Ultra)
- **Main LAN:** Pi, personal devices, Fire HD 10 tablet
- **IoT VLAN ("IOT Sandbox" zone):** All IoT devices (Shelly, ESP32, WEFFORT hub, Rachio, ecobee, Dyson, Kasa, SimpliSafe)

### Firewall Rules (Zone-Based)
IOT Sandbox is a custom zone (blocks all traffic to other zones by default):
- Allow: Internal → IOT Sandbox (all traffic — Pi/phone/laptop can reach IoT devices)
- Allow: IOT Sandbox → Internal, Pi IP only, TCP port 1883 (MQTT from ESP32 to Mosquitto)
- Allow: IOT Sandbox → External (internet access for cloud-dependent IoT devices)
- Allow: IOT Sandbox → Gateway (DNS port 53, DHCP ports 67–68)
- Allow: VPN → IOT Sandbox (remote access to IoT devices)

### Device Addressing
- DHCP reservations (fixed IP per MAC address) — NOT mDNS
- All device IPs mapped in `config.py`

## MQTT Configuration (Mosquitto)

Running on Pi as system service (`/etc/mosquitto/mosquitto.conf`):
```
listener 1883
allow_anonymous false
password_file /etc/mosquitto/passwd
```

Add users: `sudo mosquitto_passwd -c /etc/mosquitto/passwd <username>`

## ESP32 Firmware (ESPHome) — Minisplit

### ESPHome Configuration (`minisplit.yaml`)
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

Baud rate: 2400 for WR series (try 4800 if connection fails).

### Flashing
- First flash: USB from dev machine — `esphome run minisplit.yaml`, select serial port
- Subsequent updates: OTA over Wi-Fi — `esphome run minisplit.yaml`, select network
- Verify before physical install: check DHCP client list, stream logs, verify MQTT messages appear
