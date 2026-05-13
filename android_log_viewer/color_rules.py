from __future__ import annotations

from dataclasses import dataclass

COLOR_RULE_FIELDS = ("TAG", "MESSAGE")

# Default colors for D, I, W, E levels (V and F stay in constants.py)
DEFAULT_LEVEL_FG: dict[str, str] = {
    "D": "#0D47A1",
    "I": "#212121",
    "W": "#E65100",
    "E": "#B71C1C",
}

DEFAULT_LEVEL_BG: dict[str, str] = {
    "D": "#E3F2FD",
    "I": "#FFFFFF",
    "W": "#FFF8E1",
    "E": "#FFEBEE",
}


@dataclass
class ColorRule:
    pattern: str
    field: str          # "TAG" or "MESSAGE"
    fg: str = ""        # hex "#RRGGBB" or "" = no override
    bg: str = ""        # hex "#RRGGBB" or "" = no override
    entire_row: bool = True
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "field": self.field,
            "fg": self.fg,
            "bg": self.bg,
            "entire_row": self.entire_row,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ColorRule":
        return cls(
            pattern=d.get("pattern", ""),
            field=d.get("field", "TAG"),
            fg=d.get("fg", ""),
            bg=d.get("bg", ""),
            entire_row=bool(d.get("entire_row", True)),
            enabled=bool(d.get("enabled", True)),
        )
