from __future__ import annotations

import subprocess
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, Signal


# Single awk invocation over all /proc/*/status files — much faster than a
# shell loop that spawns cat+grep per process.  Output: "pid name rss_kb\n".
# VmRSS is absent for kernel threads; r defaults to 0 so they still appear.
_SHELL_CMD = (
    "awk '"
    "FNR==1{if(p)printf \"%s %s %d\\n\",p,n,r;n=\"?\";p=\"\";r=0}"
    "/^Name:/{n=$2}/^Pid:/{p=$2}/^VmRSS:/{r=$2}"
    "END{if(p)printf \"%s %s %d\\n\",p,n,r}'"
    " /proc/[0-9]*/status 2>/dev/null"
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
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            try:
                procs.append({
                    "pid":  parts[0],
                    "name": parts[1],
                    "rss":  int(parts[2]),
                })
            except ValueError:
                pass
    return sorted(procs, key=lambda x: x["rss"], reverse=True)
