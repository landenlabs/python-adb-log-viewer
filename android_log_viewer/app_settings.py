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
        self.level_filters: Set[str] = {"D", "I", "W", "E"}
        # Level colors
        from .color_rules import DEFAULT_LEVEL_FG, DEFAULT_LEVEL_BG, ColorRule
        self.level_fg: dict[str, str] = dict(DEFAULT_LEVEL_FG)
        self.level_bg: dict[str, str] = dict(DEFAULT_LEVEL_BG)
        self.color_rules: List[ColorRule] = []
        self.last_profile_name: str = ""
        self.merge_same_time_tag: bool = False
        self.timeline_follows_filter: bool = True
        self.adb_path: str = ""   # empty = use platform default (adb / adb.exe)

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
            "adb_path": self.adb_path,
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
        if "adb_path" in data and isinstance(data["adb_path"], str):
            s.adb_path = data["adb_path"]
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
