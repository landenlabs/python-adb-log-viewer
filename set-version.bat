@echo off
:: Usage: set-version.bat -version v1.2.3 -message "release notes here"
setlocal enabledelayedexpansion

set "VERSION="
set "MESSAGE="

:parse
if "%~1"=="" goto validate
if /i "%~1"=="-version"  ( set "VERSION=%~2" & shift & shift & goto parse )
if /i "%~1"=="--version" ( set "VERSION=%~2" & shift & shift & goto parse )
if /i "%~1"=="-message"  ( set "MESSAGE=%~2" & shift & shift & goto parse )
if /i "%~1"=="--message" ( set "MESSAGE=%~2" & shift & shift & goto parse )
echo Unknown argument: %~1
exit /b 1

:validate
if "%VERSION%"=="" goto usage
if "%MESSAGE%"=="" goto usage
goto run

:usage
echo Usage: %~n0 -version ^<version^> -message ^<message^>
echo   Example: %~n0 -version v1.2.3 -message "this is a new release"
exit /b 1

:run
:: Ensure tag starts with 'v'
set "VTAG=%VERSION%"
if not "!VTAG:~0,1!"=="v" set "VTAG=v!VTAG!"

:: Strip leading 'v' for Python __version__
set "PY_VERSION=!VTAG:~1!"

:: Parse version components for Windows format
for /f "tokens=1,2,3 delims=." %%a in ("!PY_VERSION!") do (
    set "VER_MAJOR=%%a"
    set "VER_MINOR=%%b"
    set "VER_PATCH=%%c"
)
if "!VER_PATCH!"=="" set "VER_PATCH=0"
set "WIN_TUPLE=!VER_MAJOR!, !VER_MINOR!, !VER_PATCH!, 0"
set "WIN_VERSION=!PY_VERSION!.0"

echo Setting version to !VTAG! (Python: !PY_VERSION!)

:: Update android_log_viewer/version.py
powershell -NoProfile -Command ^
  "(Get-Content 'android_log_viewer\version.py') -replace '__version__ = \".*\"', '__version__ = \"!PY_VERSION!\"' | Set-Content 'android_log_viewer\version.py'"
if %errorlevel% neq 0 ( echo ERROR: failed to update version.py & exit /b 1 )

:: Update VERSION file (no trailing space before redirect)
echo !VTAG!>VERSION
if %errorlevel% neq 0 ( echo ERROR: failed to update VERSION & exit /b 1 )

:: Update README.md
powershell -NoProfile -Command ^
  "(Get-Content 'README.md') -replace '<!-- VERSION -->v[^ <]+', '<!-- VERSION -->!VTAG!' | Set-Content 'README.md'"
if %errorlevel% neq 0 ( echo ERROR: failed to update README.md & exit /b 1 )

:: Update windows_version_info.py - filevers tuple
powershell -NoProfile -Command ^
  "(Get-Content 'windows_version_info.py') -replace 'filevers=\([^)]+\)', 'filevers=(!WIN_TUPLE!)' | Set-Content 'windows_version_info.py'"
if %errorlevel% neq 0 ( echo ERROR: failed to update filevers & exit /b 1 )

:: Update windows_version_info.py - prodvers tuple
powershell -NoProfile -Command ^
  "(Get-Content 'windows_version_info.py') -replace 'prodvers=\([^)]+\)', 'prodvers=(!WIN_TUPLE!)' | Set-Content 'windows_version_info.py'"
if %errorlevel% neq 0 ( echo ERROR: failed to update prodvers & exit /b 1 )

:: Update windows_version_info.py - FileVersion string
powershell -NoProfile -Command ^
  "(Get-Content 'windows_version_info.py') -replace '''FileVersion'',\s+''[^'']+''', '''FileVersion'',      ''!WIN_VERSION!''' | Set-Content 'windows_version_info.py'"
if %errorlevel% neq 0 ( echo ERROR: failed to update FileVersion & exit /b 1 )

:: Update windows_version_info.py - ProductVersion string
powershell -NoProfile -Command ^
  "(Get-Content 'windows_version_info.py') -replace '''ProductVersion'',\s+''[^'']+''', '''ProductVersion'',   ''!WIN_VERSION!''' | Set-Content 'windows_version_info.py'"
if %errorlevel% neq 0 ( echo ERROR: failed to update ProductVersion & exit /b 1 )

:: Stage files
git add android_log_viewer/version.py VERSION README.md windows_version_info.py
if %errorlevel% neq 0 ( echo ERROR: git add failed & exit /b 1 )

:: Commit
git commit -m "!MESSAGE!"
if %errorlevel% neq 0 ( echo ERROR: git commit failed & exit /b 1 )

:: Annotated tag
git tag -a "!VTAG!" -m "!MESSAGE!"
if %errorlevel% neq 0 ( echo ERROR: git tag failed & exit /b 1 )

:: Push with tags
git push origin main --follow-tags
if %errorlevel% neq 0 ( echo ERROR: git push failed & exit /b 1 )

echo Done: pushed !VTAG! -- GitHub Actions release build triggered.
endlocal
