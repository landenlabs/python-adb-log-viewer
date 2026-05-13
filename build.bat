@echo off
pip install -r requirements.txt pyinstaller

pyinstaller ^
  --onefile --windowed ^
  --name android-log-viewer ^
  --icon log-viewer.ico ^
  --version-file windows_version_info.py ^
  --add-data "log-viewer.png;." ^
  --add-data "landen_labs_about_400.gif;." ^
  main.py

echo Built: dist\android-log-viewer.exe
