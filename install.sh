#!/bin/bash
set -e

rm -rf /Applications/ll-log-viewer.app
cp -R "dist/ll-log-viewer.app" /Applications/ll-log-viewer.app
echo "Installed: /Applications/ll-log-viewer.app"
