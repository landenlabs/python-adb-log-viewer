#!/bin/bash
set -e

pip install -r requirements.txt pyinstaller

pyinstaller \
  --windowed \
  --name "ll-log-viewer" \
  --icon log-viewer.icns \
  --osx-bundle-identifier com.landenlabs.androidlogviewer \
  --add-data "log-viewer.png:." \
  --add-data "landen_labs_about_400.gif:." \
  main.py

echo "Built: dist/ll-log-viewer.app"
