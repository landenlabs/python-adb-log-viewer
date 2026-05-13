from __future__ import annotations

import subprocess
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, Signal


# Single-read of /proc/net/dev — one file, minimal overhead.
# Fall back to sysfs if the proc file is unreadable (locked-down production builds).
_PROC_CMD = "cat /proc/net/dev 2>/dev/null"

# Sysfs fallback: one awk pass over all interface stats files.
# Output format: "iface rx_bytes tx_bytes rx_packets tx_packets"
_SYSFS_CMD = (
    "awk 'FNR==1{"
    "  f=FILENAME;"
    "  sub(\".*/net/\",\"\",f); sub(\"/statistics.*\",\"\",f);"
    "  if(FILENAME~/rx_bytes/)rx[f]=$1+0;"
    "  else if(FILENAME~/tx_bytes/)tx[f]=$1+0;"
    "  else if(FILENAME~/rx_packets/)rxp[f]=$1+0;"
    "  else if(FILENAME~/tx_packets/)txp[f]=$1+0"
    "}"
    "END{for(n in rx)printf \"%s %d %d %d %d\\n\",n,rx[n],tx[n],rxp[n]+0,txp[n]+0}'"
    " /sys/class/net/*/statistics/rx_bytes"
    " /sys/class/net/*/statistics/tx_bytes"
    " /sys/class/net/*/statistics/rx_packets"
    " /sys/class/net/*/statistics/tx_packets"
    " 2>/dev/null"
)


class NetReader(QThread):
    """Single-shot background thread: fetch per-interface network counters via adb."""

    stats_ready    = Signal(list)   # List[Dict[str, Any]]
    error_occurred = Signal(str)

    def __init__(self, device: Optional[str] = None, parent=None) -> None:
        super().__init__(parent)
        self.device = device

    def run(self) -> None:
        try:
            out = self._shell(_PROC_CMD)
            ifaces = _parse_proc(out)
            if not ifaces:
                out = self._shell(_SYSFS_CMD)
                ifaces = _parse_sysfs(out)
            self.stats_ready.emit(ifaces)
        except subprocess.TimeoutExpired:
            self.error_occurred.emit("Network poll timed out (10 s).")
        except FileNotFoundError:
            self.error_occurred.emit(
                "Could not find 'adb'. Install Android SDK Platform Tools."
            )
        except Exception as exc:
            self.error_occurred.emit(str(exc))

    def _shell(self, cmd: str) -> str:
        base = ["adb"]
        if self.device:
            base += ["-s", self.device]
        result = subprocess.run(
            base + ["shell", cmd],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout


def _parse_proc(output: str) -> List[Dict[str, Any]]:
    """Parse /proc/net/dev output.

    Format after header lines:
      iface: rx_bytes rx_pkts rx_errs rx_drop rx_fifo rx_frame rx_comp rx_mcast
             tx_bytes tx_pkts tx_errs tx_drop tx_fifo tx_colls tx_carr tx_comp
    """
    ifaces = []
    for line in output.splitlines():
        line = line.strip()
        if ':' not in line:
            continue
        colon = line.index(':')
        iface = line[:colon].strip()
        fields = line[colon + 1:].split()
        if len(fields) < 9:
            continue
        try:
            ifaces.append({
                "iface":      iface,
                "rx_bytes":   int(fields[0]),
                "rx_packets": int(fields[1]),
                "tx_bytes":   int(fields[8]),
                "tx_packets": int(fields[9]) if len(fields) > 9 else 0,
            })
        except (ValueError, IndexError):
            pass
    return sorted(ifaces, key=lambda x: x["rx_bytes"] + x["tx_bytes"], reverse=True)


def _parse_sysfs(output: str) -> List[Dict[str, Any]]:
    """Parse output from the sysfs awk fallback command."""
    ifaces = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            ifaces.append({
                "iface":      parts[0],
                "rx_bytes":   int(parts[1]),
                "tx_bytes":   int(parts[2]),
                "rx_packets": int(parts[3]) if len(parts) > 3 else 0,
                "tx_packets": int(parts[4]) if len(parts) > 4 else 0,
            })
        except (ValueError, IndexError):
            pass
    return sorted(ifaces, key=lambda x: x["rx_bytes"] + x["tx_bytes"], reverse=True)
