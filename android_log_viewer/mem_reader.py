from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, Signal


# Single shell command: walk /proc, emit a "START_PID" delimiter before each
# process's status snippet.  VmRSS is missing for kernel threads (rss = 0).
_SHELL_CMD = (
    "for p in /proc/[0-9]*; do "
    '[ -d "$p" ] && echo "START_PID" && '
    'cat "$p/status" 2>/dev/null | grep -E "^(Name|Pid|VmRSS):"; '
    "done"
)


class MemReader(QThread):
    """Single-shot background thread: fetch RSS for all /proc processes via adb."""

    stats_ready    = Signal(list)   # List[Dict[str, Any]], sorted rss desc
    error_occurred = Signal(str)

    def __init__(self, device: Optional[str] = None, parent=None) -> None:
        super().__init__(parent)
        self.device = device

    def run(self) -> None:
        cmd = ["adb"]
        if self.device:
            cmd += ["-s", self.device]
        cmd += ["shell", _SHELL_CMD]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            self.stats_ready.emit(_parse(result.stdout))
        except subprocess.TimeoutExpired:
            self.error_occurred.emit("Memory poll timed out (30 s).")
        except FileNotFoundError:
            self.error_occurred.emit(
                "Could not find 'adb'. Install Android SDK Platform Tools."
            )
        except Exception as exc:
            self.error_occurred.emit(str(exc))


def _parse(output: str) -> List[Dict[str, Any]]:
    procs: List[Dict[str, Any]] = []
    for chunk in output.split("START_PID"):
        if not chunk.strip():
            continue
        name = re.search(r"Name:\s+(.*)", chunk)
        pid  = re.search(r"Pid:\s+(\d+)", chunk)
        rss  = re.search(r"VmRSS:\s+(\d+)", chunk)
        if name and pid:
            procs.append({
                "name": name.group(1).strip(),
                "pid":  pid.group(1),
                "rss":  int(rss.group(1)) if rss else 0,
            })
    return sorted(procs, key=lambda x: x["rss"], reverse=True)
