from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Set

# (name, one-line description) — order matches the UI checkbox list
BUFFER_INFO: List[tuple] = [
    ("main",   "App and framework Log.* calls — the standard logcat view"),
    ("system", "OS / framework internals"),
    ("crash",  "ANR and crash stack traces"),
    ("events", "Binary activity metrics  (structured format, not plain text)"),
    ("radio",  "Telephony and modem"),
    ("kernel", "Linux kernel"),
]

BUFFER_NAMES: List[str] = [name for name, _ in BUFFER_INFO]

EXCLUDE_FIELDS = ("PID", "TAG", "MESSAGE")

RECENT_CAP = 20

# Limits — defaults and valid ranges for the user-configurable limits in
# Settings > Limits.
DEFAULT_STATS_TOP_N = 200
STATS_TOP_N_MIN = 10
STATS_TOP_N_MAX = 500

DEFAULT_MAX_RECORDS = 100_000
MAX_RECORDS_MIN = 1_000
MAX_RECORDS_MAX = 200_000


@dataclass
class ExcludeRule:
    pattern: str
    field: str      # one of EXCLUDE_FIELDS
    enabled: bool = True


def _settings_path() -> Path:
    if os.name == "nt":  # Windows — use %APPDATA%
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:               # macOS / Linux
        base = Path.home() / ".config"
    return base / "android_log_viewer" / "settings.json"


def _profiles_dir() -> Path:
    return _settings_path().parent / "color_profiles"


class AppSettings:
    def __init__(self) -> None:
        self.buffers: Set[str] = {"main"}
        self.exclude_rules: List[ExcludeRule] = []
        self.theme: str = "light"
        # Persist level filter checkbox states
        self.level_filters: Set[str] = {"D", "I", "W", "E", "B"}
        # Level colors
        from .color_rules import DEFAULT_LEVEL_FG, DEFAULT_LEVEL_BG, ColorRule
        self.level_fg: dict[str, str] = dict(DEFAULT_LEVEL_FG)
        self.level_bg: dict[str, str] = dict(DEFAULT_LEVEL_BG)
        self.color_rules: List[ColorRule] = []
        self.last_profile_name: str = ""
        self.merge_same_time_tag: bool = False
        self.timeline_follows_filter: bool = True
        self.compact_rows: bool = False
        self.adb_path: str = ""   # empty = use platform default (adb / adb.exe)
        self.startup_tag: str = ""
        self.startup_text: str = ""
        # Recently used filter patterns, newest first. Capped at RECENT_CAP.
        self.recent_tags: List[str] = []
        self.recent_texts: List[str] = []
        # Live search-field highlight colors (applied to message-column matches).
        self.search_fg: str = "#000000"
        self.search_bg: str = "#FFEB3B"
        # Limits
        self.stats_top_n: int = DEFAULT_STATS_TOP_N
        self.max_records: int = DEFAULT_MAX_RECORDS

    # ------------------------------------------------------------------ persistence

    def to_dict(self) -> dict:
        from .color_rules import ColorRule
        return {
            "buffers": sorted(self.buffers),
            "theme": self.theme,
            "level_filters": sorted(list(self.level_filters)),
            "exclude_rules": [
                {"pattern": r.pattern, "field": r.field, "enabled": r.enabled}
                for r in self.exclude_rules
            ],
            "level_fg": self.level_fg,
            "level_bg": self.level_bg,
            "color_rules": [r.to_dict() for r in self.color_rules],
            "last_profile_name": self.last_profile_name,
            "merge_same_time_tag": self.merge_same_time_tag,
            "timeline_follows_filter": self.timeline_follows_filter,
            "compact_rows": self.compact_rows,
            "adb_path": self.adb_path,
            "startup_tag": self.startup_tag,
            "startup_text": self.startup_text,
            "recent_tags": self.recent_tags[:RECENT_CAP],
            "recent_texts": self.recent_texts[:RECENT_CAP],
            "search_fg": self.search_fg,
            "search_bg": self.search_bg,
            "stats_top_n": self.stats_top_n,
            "max_records": self.max_records,
        }

    def save(self) -> None:
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        from .color_rules import ColorRule, DEFAULT_LEVEL_FG, DEFAULT_LEVEL_BG
        s = cls()
        if "buffers" in data:
            parsed = set(data["buffers"])
            s.buffers = parsed if parsed else {"main"}
        if "theme" in data and data["theme"] in ("light", "dark"):
            s.theme = data["theme"]
        if "level_filters" in data:
            s.level_filters = set(data["level_filters"])
            s.level_filters.add("B")  # always show bookmarks by default
        if "exclude_rules" in data:
            s.exclude_rules = [
                ExcludeRule(
                    pattern=r.get("pattern", ""),
                    field=r.get("field", "TAG"),
                    enabled=bool(r.get("enabled", True)),
                )
                for r in data["exclude_rules"]
                if isinstance(r, dict)
            ]
        if "level_fg" in data and isinstance(data["level_fg"], dict):
            s.level_fg = {**DEFAULT_LEVEL_FG, **data["level_fg"]}
        if "level_bg" in data and isinstance(data["level_bg"], dict):
            s.level_bg = {**DEFAULT_LEVEL_BG, **data["level_bg"]}
        if "color_rules" in data and isinstance(data["color_rules"], list):
            s.color_rules = [
                ColorRule.from_dict(r)
                for r in data["color_rules"]
                if isinstance(r, dict)
            ]
        if "last_profile_name" in data and isinstance(data["last_profile_name"], str):
            s.last_profile_name = data["last_profile_name"]
        if "merge_same_time_tag" in data:
            s.merge_same_time_tag = bool(data["merge_same_time_tag"])
        if "timeline_follows_filter" in data:
            s.timeline_follows_filter = bool(data["timeline_follows_filter"])
        if "compact_rows" in data:
            s.compact_rows = bool(data["compact_rows"])
        if "adb_path" in data and isinstance(data["adb_path"], str):
            s.adb_path = data["adb_path"]
        if "startup_tag" in data and isinstance(data["startup_tag"], str):
            s.startup_tag = data["startup_tag"]
        if "startup_text" in data and isinstance(data["startup_text"], str):
            s.startup_text = data["startup_text"]
        if "recent_tags" in data and isinstance(data["recent_tags"], list):
            s.recent_tags = [str(x) for x in data["recent_tags"] if isinstance(x, str)][:RECENT_CAP]
        if "recent_texts" in data and isinstance(data["recent_texts"], list):
            s.recent_texts = [str(x) for x in data["recent_texts"] if isinstance(x, str)][:RECENT_CAP]
        if "search_fg" in data and isinstance(data["search_fg"], str):
            s.search_fg = data["search_fg"]
        if "search_bg" in data and isinstance(data["search_bg"], str):
            s.search_bg = data["search_bg"]
        if "stats_top_n" in data:
            try:
                s.stats_top_n = max(STATS_TOP_N_MIN, min(STATS_TOP_N_MAX, int(data["stats_top_n"])))
            except (TypeError, ValueError):
                pass
        if "max_records" in data:
            try:
                s.max_records = max(MAX_RECORDS_MIN, min(MAX_RECORDS_MAX, int(data["max_records"])))
            except (TypeError, ValueError):
                pass
        return s

    @classmethod
    def load(cls) -> "AppSettings":
        path = _settings_path()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    return cls.from_dict(json.load(fh))
            except Exception:
                pass
        return cls()
