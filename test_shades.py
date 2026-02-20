"""Test script for Dooya/Motion Blinds hub connection.

Run this on the Pi (or dev machine on the same LAN) to:
  1. Verify gateway connection
  2. List all discovered blinds with MAC addresses and current state
  3. Optionally test open/close on a single blind

Usage:
    python3 test_shades.py
    python3 test_shades.py --move <mac>    # open then close a specific blind

Set MOTION_GATEWAY_KEY in your .env file first.
"""

import argparse
import os
import sys
import time
from dotenv import load_dotenv
from motionblinds import MotionGateway, MotionDiscovery

load_dotenv()

GATEWAY_IP = os.getenv("MOTION_GATEWAY_IP", "192.168.1.103")
GATEWAY_KEY = os.getenv("MOTION_GATEWAY_KEY", "")


def discover():
    """Try to auto-discover gateways on the network (no key required)."""
    print("Scanning for Motion Blinds gateways on the network...")
    d = MotionDiscovery()
    found = d.discover()
    if found:
        print(f"  Found {len(found)} gateway(s):")
        for ip, info in found.items():
            print(f"    {ip}  —  {info}")
    else:
        print("  No gateways found via multicast discovery.")
    print()


def connect_and_list(key):
    print(f"Connecting to gateway at {GATEWAY_IP} ...")
    gw = MotionGateway(ip=GATEWAY_IP, key=key)

    print("  Fetching device list ...")
    gw.GetDeviceList()
    gw.Update()

    print(f"  Gateway status : {gw.status}")
    print(f"  Devices found  : {gw.N_devices}")
    print(f"  RSSI           : {gw.RSSI} dBm")
    print(f"  Firmware       : {gw.firmware}")
    print()

    if not gw.device_list:
        print("No blinds found. Check that shades are paired to the hub in the app.")
        return gw

    print(f"{'MAC':<20} {'Type':<25} {'Position':>10} {'Battery':>10} {'RSSI':>8}")
    print("-" * 80)
    for mac, blind in gw.device_list.items():
        try:
            blind.Update()
        except Exception as e:
            print(f"  {mac}  — failed to update: {e}")
            continue
        pos = f"{blind.position}%" if blind.position is not None else "unknown"
        bat = f"{blind.battery_level}%" if blind.battery_level is not None else "unknown"
        rssi = f"{blind.RSSI} dBm" if blind.RSSI is not None else "unknown"
        btype = str(blind.blind_type) if blind.blind_type else "unknown"
        print(f"{mac:<20} {btype:<25} {pos:>10} {bat:>10} {rssi:>8}")

    print()
    print("Copy the MAC addresses above into config.py:")
    print("  SHADES_EAST_MACS = [...]   # shades 1-4 (east/NNE-facing)")
    print("  SHADES_WEST_MACS = [...]   # shades 5-8 (west/SSW-facing)")

    return gw


def test_move(gw, mac):
    blind = gw.device_list.get(mac.lower())
    if blind is None:
        print(f"MAC '{mac}' not found in device list.")
        sys.exit(1)

    print(f"\nTesting movement on blind {mac} ...")
    print("  Closing ...")
    blind.Close()
    time.sleep(15)

    blind.Update()
    print(f"  Position after close: {blind.position}%")

    print("  Opening ...")
    blind.Open()
    time.sleep(15)

    blind.Update()
    print(f"  Position after open: {blind.position}%")
    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Test Motion Blinds hub connection")
    parser.add_argument("--discover", action="store_true", help="Run multicast discovery first")
    parser.add_argument("--move", metavar="MAC", help="Test open/close on this blind MAC")
    args = parser.parse_args()

    if args.discover:
        discover()

    if not GATEWAY_KEY or GATEWAY_KEY == "your_16char_key_here":
        print("ERROR: MOTION_GATEWAY_KEY not set in .env")
        print("  Get it from the Motion Blinds app: Settings → About → tap 5 times")
        sys.exit(1)

    gw = connect_and_list(GATEWAY_KEY)

    if args.move:
        test_move(gw, args.move)


if __name__ == "__main__":
    main()
