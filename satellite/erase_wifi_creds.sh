#!/usr/bin/env bash
# Erases only the ESP32 NVS partition (drivers/nvs_credentials.cpp's saved
# WiFi/MQTT creds) without touching the flashed app - no reflash needed
# after. Offset/size below are read straight from this board's actual
# partition table (~/.platformio/packages/framework-arduinoespressif32/
# tools/partitions/default.csv: "nvs, data, nvs, 0x9000, 0x5000").
#
# Usage: ./erase_wifi_creds.sh [serial-port]
# Defaults to /dev/ttyACM0 (this node's USB JTAG/serial debug unit).

set -euo pipefail

PORT="${1:-/dev/ttyACM0}"
PIO_PYTHON="$HOME/.platformio/penv/bin/python"
ESPTOOL="$HOME/.platformio/packages/tool-esptoolpy/esptool.py"

"$PIO_PYTHON" "$ESPTOOL" --chip esp32s3 --port "$PORT" erase_region 0x9000 0x5000
