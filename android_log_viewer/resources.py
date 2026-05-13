from __future__ import annotations

import sys
from pathlib import Path


def resource_path(filename: str) -> Path:
    """Resolve a data file for both PyInstaller bundles and source runs.

    PyInstaller --onefile extracts data to sys._MEIPASS at runtime.
    When running from source, data files live at the project root (one level
    above this package directory).
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).parent.parent
    return base / filename
