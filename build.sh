#!/bin/bash
set -e

pip install -r requirements.txt pyinstaller
pyinstaller --onefile --windowed main.py --name android-log-viewer
echo "Built: dist/android-log-viewer"
