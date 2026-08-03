@echo off
setlocal
python -m pip install -r requirements.txt pyinstaller || exit /b 1

python -m PyInstaller ^
  --onefile --windowed ^
  --name ll-log-viewer ^
  --icon log-viewer.ico ^
  --version-file windows_version_info.py ^
  --add-data "log-viewer.png;." ^
  --add-data "log-viewer.ico;." ^
  --add-data "landen_labs_about_400.gif;." ^
  adb-log-viewer.py || exit /b 1

echo Built: dist\ll-log-viewer.exe
