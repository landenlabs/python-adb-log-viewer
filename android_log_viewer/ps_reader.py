from __future__ import annotations

import subprocess
from typing import Dict, Optional

from PySide6.QtCore import QThread, Signal


class PsReader(QThread):
    """
    Runs 'adb shell ps -A' in a background thread and returns a
    {pid_str: process_name} mapping via the names_ready signal.
    """

    names_ready = Signal(dict)   # Dict[str, str]

    def __init__(self, device: Optional[str] = None, adb_exe: str = "adb", parent=None) -> None:
        super().__init__(parent)
        self.device = device
        self.adb_exe = adb_exe

    def run(self) -> None:
        cmd = [self.adb_exe]
        if self.device:
            cmd += ["-s", self.device]
        cmd += ["shell", "ps", "-A"]

        try:
            from .adb_reader import NO_WINDOW_FLAGS
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15, **NO_WINDOW_FLAGS
            )
            names: Dict[str, str] = {}
            lines = result.stdout.splitlines()
            if not lines:
                self.names_ready.emit(names)
                return

            # Detect header to find PID and NAME column positions.
            # Typical header: "USER  PID  PPID  VSZ  RSS  WCHAN  ADDR  S  NAME"
            # NAME is always the last token; PID is always index 1.
            for line in lines[1:]:   # skip header row
                parts = line.split()
                if len(parts) >= 4:
                    pid = parts[1]
                    name = parts[-1]
                    names[pid] = name

            self.names_ready.emit(names)
        except Exception:
            self.names_ready.emit({})
