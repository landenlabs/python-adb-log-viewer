# PyInstaller Windows version resource.
# Appears in Explorer → Properties → Details tab.
# Pass to PyInstaller with: --version-file windows_version_info.py

VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(6, 8, 1, 0),
    prodvers=(6, 8, 1, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',   # en-US / Unicode
        [
          StringStruct('CompanyName',      'LanDen Labs'),
          StringStruct('FileDescription',  'Android Log Viewer'),
          StringStruct('FileVersion',      '6.8.1.0'),
          StringStruct('InternalName',     'll-log-viewer'),
          StringStruct('LegalCopyright',   '\xa9 2026 LanDen Labs'),
          StringStruct('OriginalFilename', 'll-log-viewer.exe'),
          StringStruct('ProductName',      'Android Log Viewer'),
          StringStruct('ProductVersion',   '6.8.1.0'),
          StringStruct('Comments',         'Real-time ADB logcat viewer'),
        ],
      )
    ]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
  ],
)
