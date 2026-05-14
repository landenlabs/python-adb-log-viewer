#!/bin/bash
# Rebuild log-viewer.icns and log-viewer.png from log-viewer.iconset.
# Run this after updating any PNG files in the iconset directory.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ICONSET="$SCRIPT_DIR/log-viewer.iconset"
ICNS="$SCRIPT_DIR/log-viewer.icns"
PNG="$SCRIPT_DIR/log-viewer.png"

if [[ "$(uname)" != "Darwin" ]]; then
    echo "Error: this script requires macOS (iconutil, sips)" >&2
    exit 1
fi

echo "Rebuilding $ICNS ..."
iconutil -c icns "$ICONSET" -o "$ICNS"

echo "Rebuilding $PNG (512x512 from iconset) ..."
sips -z 512 512 "$ICONSET/icon_512x512.png" --out "$PNG" > /dev/null

echo "Done."
sips -g pixelWidth -g pixelHeight -g hasAlpha "$ICNS" "$PNG"
