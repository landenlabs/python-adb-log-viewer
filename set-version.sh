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

# Derive Windows-format version components
IFS='.' read -r VER_MAJOR VER_MINOR VER_PATCH <<< "$PY_VERSION"
VER_MAJOR=$((10#${VER_MAJOR:-0}))
VER_MINOR=$((10#${VER_MINOR:-0}))
VER_PATCH=$((10#${VER_PATCH:-0}))
WIN_TUPLE="${VER_MAJOR}, ${VER_MINOR}, ${VER_PATCH}, 0"
WIN_VERSION="${PY_VERSION}.0"

echo "Setting version to ${VERSION} (Python: ${PY_VERSION})"

# Update android_log_viewer/version.py
sed -i '' "s/__version__ = \".*\"/__version__ = \"${PY_VERSION}\"/" android_log_viewer/version.py

# Update VERSION file
echo "${VERSION}" > VERSION

# Update README.md (<!-- VERSION --> and <!-- DATE --> markers)
CURRENT_DATE=$(date +"%d-%b-%Y")
sed -i '' "s|<!-- VERSION -->v[^ <]*|<!-- VERSION -->${VERSION}|" README.md
sed -i '' "s|<!-- DATE -->[^ <]*|<!-- DATE -->${CURRENT_DATE}|" README.md

# Update windows_version_info.py
sed -i '' -E "s/filevers=\([^)]+\)/filevers=(${WIN_TUPLE})/" windows_version_info.py
sed -i '' -E "s/prodvers=\([^)]+\)/prodvers=(${WIN_TUPLE})/" windows_version_info.py
sed -i '' -E "s/(StringStruct\('FileVersion',[[:space:]]+')[^']+'/\1${WIN_VERSION}'/" windows_version_info.py
sed -i '' -E "s/(StringStruct\('ProductVersion',[[:space:]]+')[^']+'/\1${WIN_VERSION}'/" windows_version_info.py

# Stage, commit, tag, push
git add android_log_viewer/version.py VERSION README.md windows_version_info.py
git commit -m "${MESSAGE}"
git tag -a "${VERSION}" -m "${MESSAGE}"
git push origin main --follow-tags

echo "Done: pushed ${VERSION} — GitHub Actions release build triggered."
