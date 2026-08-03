<#
.SYNOPSIS
    Bump a project's version, commit, tag, and push to trigger the CI release.

.DESCRIPTION
    Drop this script into any C#, Python, or C++ repo and run it from inside the
    repo. It locates the repo root via git and auto-detects which version-bearing
    files are present, updating each one if found and silently skipping the rest:

        VERSION                     bare  X.Y.Z
        README.md                   <!-- VERSION -->vX.Y.Z  and  <!-- DATE -->dd-MMM-yyyy
        *.csproj                    <Version>X.Y.Z</Version>
        version.py / __init__.py    __version__ = "X.Y.Z"
        *.py (standalone scripts)   VERSION = "vX.Y.Z"  (hardcoded literal, no import)
        *version_info*.py           filevers/prodvers tuples + FileVersion/ProductVersion
        *.cpp / *.h / ...           #define VERSION / _VERSION "vX.Y.Z"
        *.rc                        FILEVERSION/PRODUCTVERSION + strings + (C) year

    Then: git add -> commit -> annotated tag -> push branch -> push tag.

    See SET-VERSION.md for the full reference.

.EXAMPLE
    .\set-version.ps1 -version 1.2.3 -message "fix: long-path extraction"

.EXAMPLE
    .\set-version.ps1 --version v3.0 --message 'release notes' --force
#>

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ── Usage ────────────────────────────────────────────────────────────────────
function Show-Usage {
    param([string]$ErrorText)
    if ($ErrorText) { Write-Host "Error: $ErrorText" -ForegroundColor Red }
    Write-Host 'Usage: set-version.ps1 -version <X.Y.Z|vX.Y.Z> -message "<msg>" [-force]'
    Write-Host '  -version|--version   required, e.g. 1.2.3 or v1.2.3'
    Write-Host '  -message|--message   required, commit + annotated-tag message'
    Write-Host '  -force|--force       overwrite the tag if it already exists'
    exit 1
}

# ── Parse named arguments (manual, so we accept -x and --x) ──────────────────
$Version = $null
$Message = $null
$Force   = $false

$known = @('-version', '--version', '-message', '--message', '-force', '--force')
$i = 0
while ($i -lt $args.Count) {
    $a = [string]$args[$i]
    switch -Regex ($a.ToLower()) {
        '^--?version$' {
            $i++
            if ($i -ge $args.Count) { Show-Usage "Missing value for $a" }
            $Version = [string]$args[$i]
            break
        }
        '^--?force$' { $Force = $true; break }
        '^--?message$' {
            # Join trailing tokens up to the next known flag, so the message
            # works whether quoted or passed as several unquoted words.
            $i++
            $parts = @()
            while ($i -lt $args.Count -and $known -notcontains ([string]$args[$i]).ToLower()) {
                $parts += [string]$args[$i]
                $i++
            }
            $i--   # step back so the outer ++ lands on the next flag
            $Message = ($parts -join ' ').Trim()
            break
        }
        default { Show-Usage "Unknown argument: $a" }
    }
    $i++
}

if (-not $Version) { Show-Usage 'Missing required -version' }
if ([string]::IsNullOrWhiteSpace($Message)) { Show-Usage 'Missing required -message' }

# ── Derive version strings ───────────────────────────────────────────────────
$ver = $Version -replace '^v', ''
if ($ver -notmatch '^\d+\.\d+(\.\d+(\.\d+)?)?$') {
    Show-Usage "Invalid version '$Version' (expected X.Y.Z or vX.Y.Z)"
}
$tag  = "v$ver"
$date = (Get-Date).ToString('dd-MMM-yyyy', [System.Globalization.CultureInfo]::InvariantCulture)
$year = (Get-Date).Year

# Win32 VERSIONINFO needs a 4-part numeric tuple: split, int-cast, pad to 4.
$parts = @($ver.Split('.') | ForEach-Object { [int]$_ })
while ($parts.Count -lt 4) { $parts += 0 }
$parts          = $parts[0..3]
$winTuple       = ($parts -join ',')      # "1,2,3,0"
$winTupleSpaced = ($parts -join ', ')     # "1, 2, 3, 0"
$winDots        = ($parts -join '.')      # "1.2.3.0"

# ── Locate repo root ─────────────────────────────────────────────────────────
$root = (& git rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $root) {
    Write-Error 'Not inside a git repository.'
    exit 1
}
$root = ($root | Select-Object -First 1).Trim()
Set-Location $root

Write-Host "Repo    : $root"
Write-Host "Version : $ver  ->  tag $tag"
Write-Host "Date    : $date"
Write-Host ''

# ── Existing-tag guard ───────────────────────────────────────────────────────
if ((git tag -l $tag) -eq $tag) {
    if ($Force) {
        Write-Host "Tag '$tag' exists - removing local tag (force)."
        git tag -d $tag 2>&1 | ForEach-Object { Write-Host $_ }
    }
    else {
        Write-Error "Tag '$tag' already exists. Re-run with -force, or delete it: git tag -d $tag"
        exit 1
    }
}

# ── Force: clean up the remote tag and any published GitHub release ──────────
# A GitHub release is a separate object from the tag; force-pushing the tag does
# NOT remove it, so a CI step like `gh release create` would fail on the
# duplicate. Remote cleanup is best-effort and needs an authenticated `gh`.
if ($Force) {
    function Test-GhReady {
        if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { return $false }
        & gh auth status 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    function Confirm-Delete {
        param([string]$Prompt)
        if ([Console]::IsInputRedirected) {
            Write-Warning 'Non-interactive shell - not deleting the release. Re-run in a terminal to confirm.'
            return $false
        }
        return ((Read-Host "$Prompt [y/N]") -match '^(y|yes)$')
    }

    if (Test-GhReady) {
        & gh release view $tag 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $info = (& gh release view $tag --json name,tagName,assets `
                        --jq '"\(.name)  (tag \(.tagName), \(.assets | length) asset(s))"' 2>$null)
            Write-Host ''
            Write-Host "A published GitHub release exists for $tag :" -ForegroundColor Yellow
            Write-Host "  $info"
            if (Confirm-Delete 'Delete this release and its assets?') {
                & gh release delete $tag --yes 2>&1 | ForEach-Object { Write-Host $_ }
                if ($LASTEXITCODE -eq 0) { Write-Host "Deleted : GitHub release $tag" }
                else { Write-Warning "Could not delete release $tag (continuing)." }
            }
            else {
                Write-Warning "Keeping release $tag - CI may fail to publish a duplicate."
            }
        }
    }
    else {
        Write-Warning 'gh CLI not available/authenticated - skipping GitHub release cleanup.'
    }

    # Delete the remote tag so the re-push registers as a clean tag-create event.
    Write-Host "Deleting: remote tag $tag (if present)"
    git push origin ":refs/tags/$tag" 2>&1 | ForEach-Object { Write-Host $_ }
}

# ── Helpers ──────────────────────────────────────────────────────────────────
$changed = New-Object System.Collections.Generic.List[string]
function Add-Changed { param([string]$Path) if (-not $changed.Contains($Path)) { $changed.Add($Path) } }

# Read a UTF-8 (or ASCII) text file, remembering whether it had a BOM so we can
# write it back identically (Visual Studio frequently saves sources with a BOM).
function Read-Utf8 {
    param([string]$Path)
    $bytes  = [System.IO.File]::ReadAllBytes($Path)
    $hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
    $enc    = New-Object System.Text.UTF8Encoding($hasBom)
    $offset = if ($hasBom) { 3 } else { 0 }
    [pscustomobject]@{
        Text     = $enc.GetString($bytes, $offset, $bytes.Length - $offset)
        Encoding = $enc
    }
}

# Files under the repo, excluding build/VCS directories.
function Get-RepoFiles {
    param([string[]]$Include)
    Get-ChildItem -Path $root -Recurse -File -Include $Include -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '[\\/](\.git|bin|obj|node_modules|build|dist|out|packages|\.vs)[\\/]' }
}

# Apply a UTF-8 text edit; write + record only when the content actually changes.
function Edit-Utf8 {
    param([string]$Path, [scriptblock]$Transform, [string]$Label)
    $f = Read-Utf8 $Path
    $new = & $Transform $f.Text
    if ($new -ne $f.Text) {
        [System.IO.File]::WriteAllText($Path, $new, $f.Encoding)
        Add-Changed $Path
        Write-Host "Updated : $Label"
    }
}

# ── 1. VERSION (always, repo root, bare X.Y.Z) ───────────────────────────────
$versionFile = Join-Path $root 'VERSION'
[System.IO.File]::WriteAllText($versionFile, "$ver$([Environment]::NewLine)")
Add-Changed $versionFile
Write-Host "Updated : VERSION  ($ver)"

# ── 2. README.md (version + date markers, both styles, case-insensitive) ─────
$readme = Join-Path $root 'README.md'
if (Test-Path $readme) {
    Edit-Utf8 $readme {
        param($t)
        $t = [regex]::Replace($t, '(?i)(<!--\s*version\s*-->)v?[^\s<]*', "`${1}$tag")
        $t = [regex]::Replace($t, '(?i)(<!--\s*date\s*-->)[^\r\n<]*',    "`${1}$date")
        $t
    } "README.md  (version=$tag  date=$date)"
}

# ── 3. C#: <Version> in any .csproj ──────────────────────────────────────────
foreach ($f in Get-RepoFiles '*.csproj') {
    $text = (Read-Utf8 $f.FullName).Text
    if ($text -match '<Version>[^<]*</Version>') {
        Edit-Utf8 $f.FullName {
            param($t) [regex]::Replace($t, '<Version>[^<]*</Version>', "<Version>$ver</Version>")
        } "$($f.Name)  (<Version>$ver</Version>)"
    }
}

# ── 4. Python: __version__ = "X.Y.Z" ─────────────────────────────────────────
foreach ($f in Get-RepoFiles @('version.py', '_version.py', '__init__.py')) {
    $text = (Read-Utf8 $f.FullName).Text
    if ($text -match '__version__\s*=') {
        Edit-Utf8 $f.FullName {
            param($t)
            $t = [regex]::Replace($t, '(__version__\s*=\s*")[^"]*(")', "`${1}$ver`${2}")
            $t = [regex]::Replace($t, "(__version__\s*=\s*')[^']*(')", "`${1}$ver`${2}")
            $t
        } "$($f.Name)  (__version__ = $ver)"
    }
}

# ── 4b. Python: standalone-script literal  VERSION = "vX.Y.Z"  ──────────────
# Some scripts are distributed as a single file (no sibling version.py), so
# they hardcode VERSION instead of importing __version__.
foreach ($f in Get-RepoFiles '*.py') {
    $text = (Read-Utf8 $f.FullName).Text
    if ($text -match '(?m)^\s*VERSION\s*=\s*["'']v?[0-9]') {
        Edit-Utf8 $f.FullName {
            param($t)
            $t = [regex]::Replace($t, '(VERSION\s*=\s*")v?[^"]*(")', "`${1}$tag`${2}")
            $t = [regex]::Replace($t, "(VERSION\s*=\s*')v?[^']*(')", "`${1}$tag`${2}")
            $t
        } "$($f.Name)  (VERSION = ""$tag"")"
    }
}

# ── 5. PyInstaller: *version_info*.py tuples + version strings ───────────────
foreach ($f in Get-RepoFiles '*version_info*.py') {
    $text = (Read-Utf8 $f.FullName).Text
    if ($text -match 'filevers\s*=') {
        Edit-Utf8 $f.FullName {
            param($t)
            $t = [regex]::Replace($t, '(filevers\s*=\s*\()[^)]*(\))', "`${1}$winTupleSpaced`${2}")
            $t = [regex]::Replace($t, '(prodvers\s*=\s*\()[^)]*(\))', "`${1}$winTupleSpaced`${2}")
            $t = [regex]::Replace($t, "(u?'FileVersion',\s*u?')[^']*(')",    "`${1}$winDots`${2}")
            $t = [regex]::Replace($t, "(u?'ProductVersion',\s*u?')[^']*(')", "`${1}$winDots`${2}")
            $t
        } "$($f.Name)  ($winDots)"
    }
}

# ── 6. C++: #define VERSION / _VERSION "vX.Y.Z" ──────────────────────────────
foreach ($f in Get-RepoFiles @('*.cpp', '*.cc', '*.cxx', '*.h', '*.hpp', '*.hh')) {
    $text = (Read-Utf8 $f.FullName).Text
    if ($text -match '#define\s+_?VERSION\s+"') {
        Edit-Utf8 $f.FullName {
            param($t) [regex]::Replace($t, '(#define\s+_?VERSION\s+")[^"]*(")', "`${1}$tag`${2}")
        } "$($f.Name)  (#define VERSION ""$tag"")"
    }
}

# ── 7. Win32 .rc: VERSIONINFO + string values + copyright year (UTF-16 LE) ───
foreach ($f in Get-RepoFiles '*.rc') {
    $unicode = [System.Text.Encoding]::Unicode
    $t = [System.IO.File]::ReadAllText($f.FullName, $unicode)
    if ($t -match 'FILEVERSION') {
        $orig = $t
        $t = [regex]::Replace($t, 'FILEVERSION\s+\d+,\s*\d+,\s*\d+,\s*\d+',                  "FILEVERSION $winTuple")
        $t = [regex]::Replace($t, 'PRODUCTVERSION\s+\d+,\s*\d+,\s*\d+,\s*\d+',               "PRODUCTVERSION $winTuple")
        $t = [regex]::Replace($t, '(VALUE\s+"FileVersion",\s+)"[^"]*"',                      "`${1}`"$winDots`"")
        $t = [regex]::Replace($t, '(VALUE\s+"ProductVersion",\s+)"[^"]*"',                   "`${1}`"$winDots`"")
        $t = [regex]::Replace($t, '(VALUE\s+"LegalCopyright",\s+"[^"]*Copyright \(C\) )\d{4}', "`${1}$year")
        if ($t -ne $orig) {
            [System.IO.File]::WriteAllText($f.FullName, $t, $unicode)
            Add-Changed $f.FullName
            Write-Host "Updated : $($f.Name)  ($winDots  (c) $year)"
        }
    }
}

# ── Commit, tag, push ────────────────────────────────────────────────────────
# Wrap git so its stderr (warnings) doesn't get turned into a fatal ErrorRecord
# by ErrorActionPreference=Stop; fail only on a non-zero exit code.
function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    & git @GitArgs 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-Error ("git " + ($GitArgs -join ' ') + " failed (exit $LASTEXITCODE)")
        exit 1
    }
}

Write-Host ''
Invoke-Git @(@('add', '--') + $changed)
Invoke-Git commit -m $Message
Invoke-Git tag -a $tag -m $Message
Write-Host "Tagged  : $tag"

# Push branch and tag as SEPARATE operations so GitHub delivers two webhook
# events; --follow-tags can coalesce them and skip the tag-triggered release.
Write-Host 'Pushing : branch -> origin'
Invoke-Git push origin HEAD
Write-Host "Pushing : tag $tag -> origin"
if ($Force) { Invoke-Git push origin $tag --force } else { Invoke-Git push origin $tag }

Write-Host ''
Write-Host "Done. Pushed $tag - CI build + release should now run."
