#!/bin/bash
set -e

cd "$(dirname "$0")"

if [ ! -d "dist/ll-log-viewer.app" ] || [ -n "$(find . -name '*.py' -newer dist/ll-log-viewer.app -print -quit)" ]; then
    echo "Build missing or stale, running build.sh..."
    ./build.sh
fi

rm -rf /Applications/ll-log-viewer.app
cp -R "dist/ll-log-viewer.app" /Applications/ll-log-viewer.app
echo "Installed: /Applications/ll-log-viewer.app"
