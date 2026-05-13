from __future__ import annotations
from dataclasses import dataclass


@dataclass
class LogRecord:
    row_id: int       # sequential id assigned on append
    timestamp: str    # "MM-DD HH:MM:SS.mmm"
    pid: str
    tid: str
    level: str        # V D I W E F
    tag: str
    message: str
