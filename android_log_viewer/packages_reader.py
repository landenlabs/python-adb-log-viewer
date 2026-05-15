from __future__ import annotations

import subprocess
from typing import List, Optional

from PySide6.QtCore import QThread, Signal


class PackagesReader(QThread):
    """Runs 'adb shell pm list packages -3' in a background thread."""

    packages_ready = Signal(list)        # List[str]
    error_occurred = Signal(str)

    def __init__(
        self,
        device: Optional[str] = None,
        adb_exe: str = "adb",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.device = device
        self.adb_exe = adb_exe

    def run(self) -> None:
        cmd = [self.adb_exe]
        if self.device:
            cmd += ["-s", self.device]
        cmd += ["shell", "pm", "list", "packages", "-3"]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                self.error_occurred.emit(result.stderr.strip() or "adb command failed")
                return
            pkgs: List[str] = []
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("package:"):
                    pkgs.append(line[len("package:"):])
            pkgs.sort()
            self.packages_ready.emit(pkgs)
        except Exception as exc:
            self.error_occurred.emit(str(exc))
