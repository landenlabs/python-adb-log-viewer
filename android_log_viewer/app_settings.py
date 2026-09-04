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

# Settings file schema version. Bumped only on INCOMPATIBLE layout changes
# (key renames, restructuring, semantic shifts). Additive changes do NOT
# require a bump — the "key in data" check in from_dict already handles
# those. Migrators are chained in _migrate_settings; each upgrades data
# in place from version N to N+1.
#
# History:
#   1 = initial format (single exclude_rules list)
#   2 = exclude_rules split into global + profile_exclude_rules
SETTINGS_SCHEMA_VERSION = 2

# Color profile (.json) schema version. Independent from settings — a saved
# profile file from any earlier version of the app should still load.
PROFILE_SCHEMA_VERSION = 1

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


# ----------------------------------------------------------------------
# Schema migrations
# ----------------------------------------------------------------------

def _migrate_settings_v1_to_v2(data: dict) -> None:
    """v1 had a single 'exclude_rules' list shared by the Settings and
    Colors dialogs. v2 splits per-project rules into 'profile_exclude_rules'.
    Duplicate the existing rules into the new list so nothing is dropped;
    the user can prune either side independently afterwards."""
    if "profile_exclude_rules" not in data:
        data["profile_exclude_rules"] = list(data.get("exclude_rules", []))


# Ordered chain of (from_version, migrator) — each migrator upgrades data
# in place from version N to N+1. Add new migrators here as the schema
# evolves; the loop in _migrate_settings will pick them up automatically.
_SETTINGS_MIGRATORS = [
    (1, _migrate_settings_v1_to_v2),
]


def _migrate_settings(data: dict) -> dict:
    """Upgrade a settings-file dict from whatever version it claims to be
    up to SETTINGS_SCHEMA_VERSION, applying each migrator in turn. A
    missing 'schema_version' key is treated as v1 (the original format).
    Files claiming a newer version than this build understands are read
    best-effort with a warning — never silently downgraded, since that
    would clobber data the newer build added."""
    version = data.get("schema_version", 1)
    if not isinstance(version, int) or version < 1:
        version = 1
    if version > SETTINGS_SCHEMA_VERSION:
        import sys
        print(
            f"[android_log_viewer] settings.json schema_version={version} "
            f"is newer than this build (max {SETTINGS_SCHEMA_VERSION}); "
            "reading best-effort. Save will rewrite at the older version.",
            file=sys.stderr,
        )
        return data
    for from_v, migrate in _SETTINGS_MIGRATORS:
        if version == from_v:
            migrate(data)
            version = from_v + 1
    data["schema_version"] = SETTINGS_SCHEMA_VERSION
    return data


# ----------------------------------------------------------------------
# Color profile (.json) migration + headless loader
# ----------------------------------------------------------------------

# Ordered chain of (from_version, migrator) for color profile files. Empty
# for now; bump PROFILE_SCHEMA_VERSION and append a migrator here when an
# incompatible change ships.
_PROFILE_MIGRATORS: list = []


def _migrate_profile(data: dict) -> dict:
    """Upgrade a profile dict to PROFILE_SCHEMA_VERSION. Missing
    'schema_version' is treated as v1 (the original format). Future-version
    files are read best-effort with a warning."""
    data = dict(data)
    version = data.get("schema_version", 1)
    if not isinstance(version, int) or version < 1:
        version = 1
    if version > PROFILE_SCHEMA_VERSION:
        import sys
        print(
            f"[android_log_viewer] color profile schema_version={version} "
            f"is newer than this build (max {PROFILE_SCHEMA_VERSION}); "
            "reading best-effort.",
            file=sys.stderr,
        )
        return data
    for from_v, migrate in _PROFILE_MIGRATORS:
        if version == from_v:
            migrate(data)
            version = from_v + 1
    data["schema_version"] = PROFILE_SCHEMA_VERSION
    return data


def resolve_color_profile_path(name_or_path: str) -> Path:
    """Resolve a --colors CLI argument to a Path.

    Rules:
      • Contains a path separator OR ends with '.json'  → treat as a path
        (relative paths resolve against the current working directory).
      • Anything else                                   → treat as a bare
        profile name and look up '<profiles_dir>/<name>.json'.

    The returned Path is NOT checked for existence — that's the caller's
    job, so the caller can surface a useful error.
    """
    raw = name_or_path.strip()
    has_sep = "/" in raw or "\\" in raw
    looks_like_file = raw.lower().endswith(".json")
    if has_sep or looks_like_file:
        return Path(raw).expanduser()
    return _profiles_dir() / f"{raw}.json"


def load_color_profile_into_settings(settings: "AppSettings", path: Path) -> None:
    """Read a color profile .json from `path` and apply every field to
    `settings` in place. Mirrors the commit half of ColorsDialog._load_profile
    but writes directly to settings instead of dialog widgets — used by the
    --colors CLI flag and any other headless entry point.

    Raises FileNotFoundError, json.JSONDecodeError, or OSError on failure.
    The caller is responsible for surfacing those to the user.
    """
    from .color_rules import ColorRule, DEFAULT_LEVEL_FG, DEFAULT_LEVEL_BG

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data = _migrate_profile(data)

    startup_tag = data.get("startup_tag", "")
    startup_text = data.get("startup_text", "")
    if isinstance(startup_tag, str):
        settings.startup_tag = startup_tag
    if isinstance(startup_text, str):
        settings.startup_text = startup_text

    raw_level_fg = data.get("level_fg") if isinstance(data.get("level_fg"), dict) else {}
    raw_level_bg = data.get("level_bg") if isinstance(data.get("level_bg"), dict) else {}
    if raw_level_fg:
        settings.level_fg = {**DEFAULT_LEVEL_FG, **{k: v for k, v in raw_level_fg.items() if isinstance(v, str)}}
    if raw_level_bg:
        settings.level_bg = {**DEFAULT_LEVEL_BG, **{k: v for k, v in raw_level_bg.items() if isinstance(v, str)}}

    if isinstance(data.get("search_fg"), str):
        settings.search_fg = data["search_fg"]
    if isinstance(data.get("search_bg"), str):
        settings.search_bg = data["search_bg"]

    settings.color_rules = [
        ColorRule.from_dict(r)
        for r in data.get("color_rules", [])
        if isinstance(r, dict)
    ]
    settings.profile_exclude_rules = [
        ExcludeRule(
            pattern=r.get("pattern", ""),
            field=r.get("field", "TAG"),
            enabled=bool(r.get("enabled", True)),
        )
        for r in data.get("exclude_rules", [])
        if isinstance(r, dict)
    ]

    settings.last_profile_name = path.stem


class AppSettings:
    def __init__(self) -> None:
        self.buffers: Set[str] = {"main"}
        # Global exclusion list — edited via the Settings dialog. Not affected
        # by color-profile load/save.
        self.exclude_rules: List[ExcludeRule] = []
        # Per-project exclusion list — edited via the Colors dialog and
        # saved/loaded as part of named color profiles. Persisted in
        # settings.json so it survives a restart between profile saves.
        self.profile_exclude_rules: List[ExcludeRule] = []
        self.theme: str = "light"
        # Persist level filter checkbox states
        self.level_filters: Set[str] = {"D", "I", "W", "E", "B"}
        # Level colors
        from .color_rules import DEFAULT_LEVEL_FG, DEFAULT_LEVEL_BG, ColorRule
        self.level_fg: dict[str, str] = dict(DEFAULT_LEVEL_FG)
        self.level_bg: dict[str, str] = dict(DEFAULT_LEVEL_BG)
        self.color_rules: List[ColorRule] = []
        self.last_profile_name: str = ""
        # Runtime-only: True when in-memory color/exclude state differs from
        # the named profile file on disk. Not persisted across launches.
        self.profile_dirty: bool = False
        self.merge_same_time_tag: bool = False
        self.timeline_follows_filter: bool = True
        self.timeline_visible: bool = True
        self.compact_rows: bool = False
        self.wrap_messages: bool = False
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
        # Index into MainWindow._ZOOM_SIZES; the default base index is applied
        # by MainWindow if not provided in settings.
        self.zoom_idx: int = -1
        # Crash detection.
        # Start regex begins a pending trace on match; follow regex matches the
        # 2nd and following messages with identical (Time, PID, TID, Level).
        # A pending trace is promoted to the visible crash list once it has
        # at least crash_min_count records.
        self.crash_capture_enabled: bool = True
        self.crash_start_regex: str = r"CrashReporter|java\.lang\.[A-Za-z]+Exception"
        self.crash_follow_regex: str = r"[ ]*at +"
        self.crash_min_count: int = 2

    # ------------------------------------------------------------------ persistence

    def to_dict(self) -> dict:
        from .color_rules import ColorRule
        return {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "buffers": sorted(self.buffers),
            "theme": self.theme,
            "level_filters": sorted(list(self.level_filters)),
            "exclude_rules": [
                {"pattern": r.pattern, "field": r.field, "enabled": r.enabled}
                for r in self.exclude_rules
            ],
            "profile_exclude_rules": [
                {"pattern": r.pattern, "field": r.field, "enabled": r.enabled}
                for r in self.profile_exclude_rules
            ],
            "level_fg": self.level_fg,
            "level_bg": self.level_bg,
            "color_rules": [r.to_dict() for r in self.color_rules],
            "last_profile_name": self.last_profile_name,
            "merge_same_time_tag": self.merge_same_time_tag,
            "timeline_follows_filter": self.timeline_follows_filter,
            "timeline_visible": self.timeline_visible,
            "compact_rows": self.compact_rows,
            "wrap_messages": self.wrap_messages,
            "adb_path": self.adb_path,
            "startup_tag": self.startup_tag,
            "startup_text": self.startup_text,
            "recent_tags": self.recent_tags[:RECENT_CAP],
            "recent_texts": self.recent_texts[:RECENT_CAP],
            "search_fg": self.search_fg,
            "search_bg": self.search_bg,
            "stats_top_n": self.stats_top_n,
            "max_records": self.max_records,
            "zoom_idx": self.zoom_idx,
            "crash_capture_enabled": self.crash_capture_enabled,
            "crash_start_regex": self.crash_start_regex,
            "crash_follow_regex": self.crash_follow_regex,
            "crash_min_count": self.crash_min_count,
        }

    def save(self) -> None:
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        from .color_rules import ColorRule, DEFAULT_LEVEL_FG, DEFAULT_LEVEL_BG
        # Upgrade the dict in place to the current schema before reading
        # any keys. Each migrator is responsible for renames / restructures
        # so the loop below can stay simple "is this key present?" checks.
        data = _migrate_settings(dict(data))
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
        if "profile_exclude_rules" in data:
            s.profile_exclude_rules = [
                ExcludeRule(
                    pattern=r.get("pattern", ""),
                    field=r.get("field", "TAG"),
                    enabled=bool(r.get("enabled", True)),
                )
                for r in data["profile_exclude_rules"]
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
        if "timeline_visible" in data:
            s.timeline_visible = bool(data["timeline_visible"])
        if "compact_rows" in data:
            s.compact_rows = bool(data["compact_rows"])
        if "wrap_messages" in data:
            s.wrap_messages = bool(data["wrap_messages"])
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
        if "zoom_idx" in data:
            try:
                s.zoom_idx = int(data["zoom_idx"])
            except (TypeError, ValueError):
                pass
        if "crash_capture_enabled" in data:
            s.crash_capture_enabled = bool(data["crash_capture_enabled"])
        # Legacy single-field migration → use as start regex.
        if "crash_regex" in data and isinstance(data["crash_regex"], str):
            s.crash_start_regex = data["crash_regex"]
        if "crash_start_regex" in data and isinstance(data["crash_start_regex"], str):
            s.crash_start_regex = data["crash_start_regex"]
        if "crash_follow_regex" in data and isinstance(data["crash_follow_regex"], str):
            s.crash_follow_regex = data["crash_follow_regex"]
        if "crash_min_count" in data:
            try:
                s.crash_min_count = max(1, int(data["crash_min_count"]))
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
