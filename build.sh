#!/bin/bash
set -e

pip install -r requirements.txt pyinstaller

pyinstaller --noconfirm ll-log-viewer.spec

echo "Built: dist/ll-log-viewer.app"
