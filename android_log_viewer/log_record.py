from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QColor


@dataclass
class LogRecord:
    row_id: int       # sequential id assigned on append
    timestamp: str    # "MM-DD HH:MM:SS.mmm"
    pid: str
    tid: str
    level: str        # V D I W E F
    tag: str
    message: str

    # Transient cache for performance (not persisted)
    _cached_bg: Optional["QColor"] = field(default=None, repr=False)
    _cached_fg: Optional["QColor"] = field(default=None, repr=False)
    # {column_index: [spans]}
    _cached_highlights: Optional[dict[int, list]] = field(default=None, repr=False)
