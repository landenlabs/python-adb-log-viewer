from __future__ import annotations

import re
import subprocess
import time
from typing import List, Optional, Set

from PySide6.QtCore import QThread, Signal

from .app_settings import ExcludeRule
from .log_record import LogRecord

# adb logcat -v threadtime line format:
#   MM-DD HH:MM:SS.mmm  PID  TID LEVEL TAG  : message
_PATTERN = re.compile(
    r"^(\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+(\d+)\s+(\d+)\s+"
    r"([VDIWEF])\s+(.*?)\s*:\s*(.*)$"
)

# Matches plain tag names (no regex metacharacters): letters, digits, dots,
# underscores, hyphens, forward-slashes, and spaces — all valid in Android tags.
_SIMPLE_TAG = re.compile(r"^[\w.\-/ ]+$")


def _is_simple_tag(pattern: str) -> bool:
    """True when the pattern is a bare tag name with no regex metacharacters."""
    return bool(_SIMPLE_TAG.match(pattern))


def build_adb_command(
    device: Optional[str],
    buffers: Set[str],
    exclude_rules: Optional[List[ExcludeRule]] = None,
) -> List[str]:
    """Return the full adb command list that AdbReader will execute.

    Simple literal TAG exclusion rules are translated to TAG:S filters so that
    adb itself suppresses those tags before any data crosses the pipe.
    All other exclusion (regex, PID, MESSAGE) is handled client-side by
    LogFilterProxy and is not reflected here.
    """
    cmd = ["adb"]
    if device:
        cmd += ["-s", device]
    cmd += ["logcat", "-v", "threadtime"]
    for buf in sorted(buffers):
        cmd += ["-b", buf]

    if exclude_rules:
        silent = [
            r.pattern for r in exclude_rules
            if r.enabled and r.field == "TAG" and r.pattern and _is_simple_tag(r.pattern)
        ]
        if silent:
            for tag in silent:
                cmd.append(f"{tag}:S")
            cmd.append("*:V")

    return cmd


def parse_line(line: str) -> Optional[LogRecord]:
    m = _PATTERN.match(line)
    if m:
        return LogRecord(
            row_id=0,
            timestamp=m.group(1),
            pid=m.group(2),
            tid=m.group(3),
            level=m.group(4),
            tag=m.group(5).strip(),
            message=m.group(6),
        )
    return None


def list_devices() -> List[str]:
    """Return serial numbers of connected adb devices."""
    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        devices: List[str] = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices
    except Exception:
        return []


class AdbReader(QThread):
    """Streams adb logcat in a background QThread, emitting batches of LogRecord."""

    records_ready = Signal(list)   # List[LogRecord]
    error_occurred = Signal(str)
    started_reading = Signal()
    stopped_reading = Signal()

    _BATCH_SIZE = 100          # also the DB-write batch size in MainWindow
    _FLUSH_SECS = 0.05         # max age of any line before it is emitted (50 ms)

    def __init__(
        self,
        device: Optional[str] = None,
        buffers: Optional[Set[str]] = None,
        exclude_rules: Optional[List[ExcludeRule]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.device = device
        self.buffers: Set[str] = buffers if buffers else {"main"}
        self.exclude_rules: List[ExcludeRule] = exclude_rules or []
        self._stop_flag = False
        self._process: Optional[subprocess.Popen] = None

    # ------------------------------------------------------------------
    def run(self) -> None:
        cmd = build_adb_command(self.device, self.buffers, self.exclude_rules)

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # Binary mode: we decode each line ourselves so that stray
                # non-UTF-8 bytes (e.g. UTF-16 BOM 0xFE/0xFF from some apps)
                # are replaced with ? rather than raising UnicodeDecodeError.
            )
            self.started_reading.emit()

            batch: List[LogRecord] = []
            last_flush = time.monotonic()
            assert self._process.stdout is not None
            for raw_bytes in self._process.stdout:
                raw = raw_bytes.decode("utf-8", errors="replace").rstrip()
                if self._stop_flag:
                    break
                record = parse_line(raw)
                if record is not None:
                    batch.append(record)

                now = time.monotonic()
                if batch and (
                    len(batch) >= self._BATCH_SIZE          # count trigger (DB batch)
                    or now - last_flush >= self._FLUSH_SECS  # time trigger  (UI latency)
                ):
                    self.records_ready.emit(batch)
                    batch = []          # rebind — old list is safe in the signal queue
                    last_flush = now
            if batch:
                self.records_ready.emit(batch)

        except FileNotFoundError:
            self.error_occurred.emit(
                "Could not find 'adb'.\n\n"
                "Install Android SDK Platform Tools and make sure 'adb' is on PATH."
            )
        except Exception as exc:
            if not self._stop_flag:
                self.error_occurred.emit(str(exc))
        finally:
            self._process = None
            self.stopped_reading.emit()

    def stop(self) -> None:
        self._stop_flag = True
        if self._process:
            self._process.terminate()
