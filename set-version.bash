#!/usr/bin/env bash
#
# set-version.bash - bump a project's version, commit, tag, and push to trigger
# the CI release. Portable across macOS and Linux (uses perl + iconv, not the
# BSD/GNU-divergent `sed -i`).
#
# Drop this script into any C#, Python, or C++ repo and run it from inside the
# repo. It locates the repo root via git and auto-detects which version-bearing
# files are present, updating each if found and skipping the rest:
#
#     VERSION                     bare  X.Y.Z
#     README.md                   <!-- VERSION -->vX.Y.Z  and  <!-- DATE -->dd-MMM-yyyy
#     *.csproj                    <Version>X.Y.Z</Version>
#     version.py / __init__.py    __version__ = "X.Y.Z"
#     *.py (standalone scripts)   VERSION = "vX.Y.Z"  (hardcoded literal, no import)
#     *version_info*.py           filevers/prodvers tuples + FileVersion/ProductVersion
#     *.cpp / *.h / ...           #define VERSION / _VERSION "vX.Y.Z"
#     *.rc                        FILEVERSION/PRODUCTVERSION + strings + (C) year
#
# Then: git add -> commit -> annotated tag -> push branch -> push tag.
# See SET-VERSION.md for the full reference.
#
# Usage:
#   ./set-version.bash -version <X.Y.Z|vX.Y.Z> -message "<msg>" [-force]

set -euo pipefail

usage() {
    if [ -n "${1:-}" ]; then echo "Error: $1" >&2; fi
    cat >&2 <<'EOF'
Usage: set-version.bash -version <X.Y.Z|vX.Y.Z> -message "<msg>" [-force]
  -version|--version   required, e.g. 1.2.3 or v1.2.3
  -message|--message   required, commit + annotated-tag message
  -force|--force       overwrite the tag if it already exists
EOF
    exit 1
}

# ── Parse named arguments ────────────────────────────────────────────────────
VERSION=""
MESSAGE=""
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        -version|--version) [ $# -ge 2 ] || usage "Missing value for $1"; VERSION="$2"; shift 2 ;;
        -message|--message) [ $# -ge 2 ] || usage "Missing value for $1"; MESSAGE="$2"; shift 2 ;;
        -force|--force)     FORCE=1; shift ;;
        *)                  usage "Unknown argument: $1" ;;
    esac
done

[ -n "$VERSION" ] || usage "Missing required -version"
[ -n "$MESSAGE" ] || usage "Missing required -message"

# ── Derive version strings ───────────────────────────────────────────────────
VER="${VERSION#v}"
if ! [[ "$VER" =~ ^[0-9]+\.[0-9]+(\.[0-9]+(\.[0-9]+)?)?$ ]]; then
    usage "Invalid version '$VERSION' (expected X.Y.Z or vX.Y.Z)"
fi
TAG="v$VER"
DATE="$(date +'%d-%b-%Y')"
YEAR="$(date +'%Y')"

# Win32 VERSIONINFO needs a 4-part numeric tuple: split, int-cast, pad to 4.
IFS='.' read -r -a RAW <<< "$VER"
PARTS=()
for idx in 0 1 2 3; do PARTS+=("$(( 10#${RAW[idx]:-0} ))"); done
WIN_TUPLE="${PARTS[0]},${PARTS[1]},${PARTS[2]},${PARTS[3]}"           # 1,2,3,0
WIN_TUPLE_SP="${PARTS[0]}, ${PARTS[1]}, ${PARTS[2]}, ${PARTS[3]}"     # 1, 2, 3, 0
WIN_DOTS="${PARTS[0]}.${PARTS[1]}.${PARTS[2]}.${PARTS[3]}"            # 1.2.3.0

# ── Locate repo root ─────────────────────────────────────────────────────────
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "Not inside a git repository." >&2; exit 1; }
cd "$ROOT"

echo "Repo    : $ROOT"
echo "Version : $VER  ->  tag $TAG"
echo "Date    : $DATE"
echo

# ── Existing-tag guard ───────────────────────────────────────────────────────
if [ "$(git tag -l "$TAG")" = "$TAG" ]; then
    if [ "$FORCE" -eq 1 ]; then
        echo "Tag '$TAG' exists - removing local tag (force)."
        git tag -d "$TAG"
    else
        echo "Tag '$TAG' already exists. Re-run with -force, or delete it: git tag -d $TAG" >&2
        exit 1
    fi
fi

# ── Force: clean up the remote tag and any published GitHub release ──────────
# A GitHub release is a separate object from the tag; force-pushing the tag does
# NOT remove it, so a CI step like `gh release create` would fail on the
# duplicate. Remote cleanup is best-effort and needs an authenticated `gh`.
if [ "$FORCE" -eq 1 ]; then
    if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
        if gh release view "$TAG" >/dev/null 2>&1; then
            info="$(gh release view "$TAG" --json name,tagName,assets \
                    --jq '"\(.name)  (tag \(.tagName), \(.assets | length) asset(s))"' 2>/dev/null || true)"
            echo
            echo "A published GitHub release exists for $TAG :"
            echo "  $info"
            ans="n"
            if [ -t 0 ]; then
                printf 'Delete this release and its assets? [y/N] '
                read -r ans
            else
                echo "Non-interactive shell - not deleting the release. Re-run in a terminal to confirm." >&2
            fi
            case "$ans" in
                y|Y|yes|YES)
                    if gh release delete "$TAG" --yes; then echo "Deleted : GitHub release $TAG"
                    else echo "Could not delete release $TAG (continuing)." >&2; fi ;;
                *) echo "Keeping release $TAG - CI may fail to publish a duplicate." >&2 ;;
            esac
        fi
    else
        echo "gh CLI not available/authenticated - skipping GitHub release cleanup." >&2
    fi

    # Delete the remote tag so the re-push registers as a clean tag-create event.
    echo "Deleting: remote tag $TAG (if present)"
    git push origin ":refs/tags/$TAG" 2>/dev/null || true
fi

# ── Helpers ──────────────────────────────────────────────────────────────────
CHANGED=()
add_changed() { CHANGED+=("$1"); }

# Directories never searched for source files.
PRUNE=( -type d \( -name .git -o -name bin -o -name obj -o -name node_modules \
        -o -name build -o -name dist -o -name out -o -name packages -o -name .vs \) -prune )

# True if the file begins with a UTF-16 LE BOM (FF FE).
is_utf16le() {
    [ "$(head -c 2 "$1" | od -An -tx1 | tr -d ' \n')" = "fffe" ]
}

# ── 1. VERSION (always, repo root, bare X.Y.Z) ───────────────────────────────
printf '%s\n' "$VER" > VERSION
add_changed "VERSION"
echo "Updated : VERSION  ($VER)"

# ── 2. README.md (version + date markers, both styles, case-insensitive) ─────
if [ -f README.md ] && grep -qiE '<!--[[:space:]]*(version|date)[[:space:]]*-->' README.md; then
    perl -0pi -e "s/(<!--\s*version\s*-->)v?[^\s<]*/\${1}$TAG/gi; s/(<!--\s*date\s*-->)[^\r\n<]*/\${1}$DATE/gi" README.md
    add_changed "README.md"
    echo "Updated : README.md  (version=$TAG  date=$DATE)"
fi

# ── 3. C#: <Version> in any .csproj ──────────────────────────────────────────
while IFS= read -r f; do
    if grep -q '<Version>' "$f"; then
        perl -0pi -e "s|<Version>[^<]*</Version>|<Version>$VER</Version>|g" "$f"
        add_changed "$f"; echo "Updated : $f  (<Version>$VER</Version>)"
    fi
done < <(find . "${PRUNE[@]}" -o -type f -name '*.csproj' -print)

# ── 4. Python: __version__ = "X.Y.Z" ─────────────────────────────────────────
while IFS= read -r f; do
    if grep -qE '__version__[[:space:]]*=' "$f"; then
        perl -0pi -e "s/(__version__\s*=\s*[\"'])[^\"']*([\"'])/\${1}$VER\${2}/g" "$f"
        add_changed "$f"; echo "Updated : $f  (__version__ = $VER)"
    fi
done < <(find . "${PRUNE[@]}" -o -type f \( -name version.py -o -name _version.py -o -name __init__.py \) -print)

# ── 4b. Python: standalone-script literal  VERSION = "vX.Y.Z"  ──────────────
# Some scripts are distributed as a single file (no sibling version.py), so
# they hardcode VERSION instead of importing __version__.
while IFS= read -r f; do
    if grep -qE '^[[:space:]]*VERSION[[:space:]]*=[[:space:]]*["'"'"']v?[0-9]' "$f"; then
        perl -0pi -e "s/(VERSION\s*=\s*[\"'])v?[^\"']*([\"'])/\${1}$TAG\${2}/g" "$f"
        add_changed "$f"; echo "Updated : $f  (VERSION = \"$TAG\")"
    fi
done < <(find . "${PRUNE[@]}" -o -type f -name '*.py' -print)

# ── 5. PyInstaller: *version_info*.py tuples + version strings ───────────────
while IFS= read -r f; do
    if grep -q 'filevers' "$f"; then
        perl -0pi -e "
            s/(filevers\s*=\s*\()[^)]*(\))/\${1}$WIN_TUPLE_SP\${2}/g;
            s/(prodvers\s*=\s*\()[^)]*(\))/\${1}$WIN_TUPLE_SP\${2}/g;
            s/(u?'FileVersion',\s*u?')[^']*(')/\${1}$WIN_DOTS\${2}/g;
            s/(u?'ProductVersion',\s*u?')[^']*(')/\${1}$WIN_DOTS\${2}/g;
        " "$f"
        add_changed "$f"; echo "Updated : $f  ($WIN_DOTS)"
    fi
done < <(find . "${PRUNE[@]}" -o -type f -name '*version_info*.py' -print)

# ── 6. C++: #define VERSION / _VERSION "vX.Y.Z" (perl preserves any UTF-8 BOM)
while IFS= read -r f; do
    if grep -qE '#define[[:space:]]+_?VERSION[[:space:]]+"' "$f"; then
        perl -0pi -e "s/(#define\s+_?VERSION\s+\")[^\"]*(\")/\${1}$TAG\${2}/g" "$f"
        add_changed "$f"; echo "Updated : $f  (#define VERSION \"$TAG\")"
    fi
done < <(find . "${PRUNE[@]}" -o -type f \( -name '*.cpp' -o -name '*.cc' -o -name '*.cxx' \
            -o -name '*.h' -o -name '*.hpp' -o -name '*.hh' \) -print)

# ── 7. Win32 .rc: VERSIONINFO + string values + copyright year ───────────────
# Edit on a UTF-8 copy, then convert back to UTF-16 LE (the BOM round-trips).
rc_subs() {
    perl -0pi -e "
        s/FILEVERSION\s+\d+,\s*\d+,\s*\d+,\s*\d+/FILEVERSION $WIN_TUPLE/g;
        s/PRODUCTVERSION\s+\d+,\s*\d+,\s*\d+,\s*\d+/PRODUCTVERSION $WIN_TUPLE/g;
        s/(VALUE\s+\"FileVersion\",\s+)\"[^\"]*\"/\${1}\"$WIN_DOTS\"/g;
        s/(VALUE\s+\"ProductVersion\",\s+)\"[^\"]*\"/\${1}\"$WIN_DOTS\"/g;
        s/(VALUE\s+\"LegalCopyright\",\s+\"[^\"]*Copyright \(C\) )\d{4}/\${1}$YEAR/g;
    " "$1"
}
while IFS= read -r f; do
    if grep -qa 'FILEVERSION' "$f"; then
        if is_utf16le "$f"; then
            tmp="$(mktemp)"
            iconv -f UTF-16LE -t UTF-8 "$f" > "$tmp"
            rc_subs "$tmp"
            iconv -f UTF-8 -t UTF-16LE "$tmp" > "$f"
            rm -f "$tmp"
        else
            rc_subs "$f"
        fi
        add_changed "$f"; echo "Updated : $f  ($WIN_DOTS  (c) $YEAR)"
    fi
done < <(find . "${PRUNE[@]}" -o -type f -name '*.rc' -print)

# ── Commit, tag, push ────────────────────────────────────────────────────────
echo
git add -- "${CHANGED[@]}"
git commit -m "$MESSAGE"
git tag -a "$TAG" -m "$MESSAGE"
echo "Tagged  : $TAG"

# Push branch and tag as SEPARATE operations so GitHub delivers two webhook
# events; --follow-tags can coalesce them and skip the tag-triggered release.
echo "Pushing : branch -> origin"
git push origin HEAD
echo "Pushing : tag $TAG -> origin"
if [ "$FORCE" -eq 1 ]; then git push origin "$TAG" --force; else git push origin "$TAG"; fi

echo
echo "Done. Pushed $TAG - CI build + release should now run."
