from __future__ import annotations
from PySide6.QtGui import QColor

LEVELS: list[str] = ["V", "D", "I", "W", "E", "F"]

LEVEL_BG: dict[str, QColor] = {
    "V": QColor("#EEEEEE"),
    "D": QColor("#E3F2FD"),
    "I": QColor("#FFFFFF"),
    "W": QColor("#FFF8E1"),
    "E": QColor("#FFEBEE"),
    "F": QColor("#B71C1C"),
}

LEVEL_FG: dict[str, QColor] = {
    "V": QColor("#757575"),
    "D": QColor("#0D47A1"),
    "I": QColor("#212121"),
    "W": QColor("#E65100"),
    "E": QColor("#B71C1C"),
    "F": QColor("#FFFFFF"),
}

LEVEL_NAMES: dict[str, str] = {
    "V": "Verbose",
    "D": "Debug",
    "I": "Info",
    "W": "Warning",
    "E": "Error",
    "F": "Fatal/Assert",
}

LEVEL_SEVERITY: dict[str, int] = {
    "V": 0,
    "D": 1,
    "I": 2,
    "W": 3,
    "E": 4,
    "F": 5,
}

# Maximum number of records to keep in memory/DB.
# When this is exceeded, the oldest PRUNE_SIZE records are removed.
MAX_RECORDS = 100_000
PRUNE_SIZE = 10_000

TIMELINE_BAR_COLORS: dict[str, QColor] = {
    "V": QColor("#9E9E9E"),
    "D": QColor("#42A5F5"),
    "I": QColor("#66BB6A"),
    "W": QColor("#FFA726"),
    "E": QColor("#EF5350"),
    "F": QColor("#AB47BC"),
}
