#!/bin/bash
# Usage: ./set-version.sh -version v1.2.3 -message "release notes here"
set -e

VERSION=""
MESSAGE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -version|--version) VERSION="$2"; shift 2 ;;
        -message|--message) MESSAGE="$2"; shift 2 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

if [[ -z "$VERSION" || -z "$MESSAGE" ]]; then
    echo "Usage: $0 -version <version> -message <message>"
    echo "  Example: $0 -version v1.2.3 -message \"this is a new release\""
    exit 1
fi

# Ensure tag starts with 'v'
[[ "$VERSION" == v* ]] || VERSION="v${VERSION}"

# Strip leading 'v' for Python __version__
PY_VERSION="${VERSION#v}"

echo "Setting version to ${VERSION} (Python: ${PY_VERSION})"

# Update android_log_viewer/version.py
sed -i '' "s/__version__ = \".*\"/__version__ = \"${PY_VERSION}\"/" android_log_viewer/version.py

# Update VERSION file
echo "${VERSION}" > VERSION

# Stage, commit, tag, push
git add android_log_viewer/version.py VERSION
git commit -m "${MESSAGE}"
git tag -a "${VERSION}" -m "${MESSAGE}"
git push origin main --follow-tags

echo "Done: pushed ${VERSION} — GitHub Actions release build triggered."
