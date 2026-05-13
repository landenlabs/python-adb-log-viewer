#!/bin/bash
set -e

pip install -r requirements.txt pyinstaller

pyinstaller \
  --onefile --windowed \
  --name android-log-viewer \
  --icon log-viewer.icns \
  --add-data "log-viewer.png:." \
  --add-data "landen_labs_about_400.gif:." \
  main.py

echo "Built: dist/android-log-viewer"
