"""Runs on the Raspberry Pi Pico 2 W. Connects to Wi-Fi, fetches the latest
pre-rendered dashboard image from GitHub, and pushes it to the e-paper panel
on a loop.

All the actual data-fetching/rendering happens server-side in GitHub Actions
(see ../render/). This device does no image processing -- it just copies the
raw bytes it downloads straight into the display's frame buffer.

Setup: copy secrets_template.py to secrets.py and fill in your Wi-Fi
credentials, then copy this whole firmware/ folder (minus secrets_template.py)
onto the Pico so main.py runs at boot.
"""
import time

import network

import requests
from epd7in5 import EPD_7in5

try:
    import secrets
except ImportError:
    raise RuntimeError(
        "Missing secrets.py -- copy secrets_template.py to secrets.py and fill in your Wi-Fi credentials"
    )

# Update this if you fork/rename the repo.
IMAGE_URL = "https://raw.githubusercontent.com/tylerjtran/e-ink-dashboard/main/dashboard/latest.bin"

REFRESH_INTERVAL_S = 5 * 60
WIFI_CONNECT_TIMEOUT_S = 20
RETRY_DELAY_S = 30


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return wlan

    wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASSWORD)
    start = time.time()
    while not wlan.isconnected():
        if time.time() - start > WIFI_CONNECT_TIMEOUT_S:
            raise RuntimeError("Wi-Fi connect timed out")
        time.sleep(0.5)
    print("Wi-Fi connected:", wlan.ifconfig())
    return wlan


def fetch_and_display(epd, last_data):
    print("Fetching", IMAGE_URL)
    resp = requests.get(IMAGE_URL)
    try:
        data = resp.content
    finally:
        resp.close()

    expected_len = epd.height * epd.width // 8
    if len(data) != expected_len:
        raise ValueError("expected {} bytes, got {}".format(expected_len, len(data)))

    # Polling is cheap (a small HTTPS GET, unrelated to GitHub Actions
    # minutes), but a physical e-paper refresh is not -- it's a visible
    # ~15-20s flash and a real refresh cycle against the panel's lifespan.
    # Skip it when the fetched bytes match what's already on screen, e.g.
    # every poll between two GitHub Actions runs.
    if data == last_data:
        print("No change since last display, skipping refresh")
        return data

    epd.init()
    epd.buffer_1Gray[:] = data
    epd.display(epd.buffer_1Gray)
    epd.sleep()
    print("Display updated")
    return data


def main():
    epd = EPD_7in5()
    last_data = None
    while True:
        try:
            connect_wifi()
            last_data = fetch_and_display(epd, last_data)
            time.sleep(REFRESH_INTERVAL_S)
        except Exception as e:
            print("Refresh failed, will retry:", e)
            time.sleep(RETRY_DELAY_S)


if __name__ == "__main__":
    main()
