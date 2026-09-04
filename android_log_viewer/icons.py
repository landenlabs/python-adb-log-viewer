"""Central icon registry. Maps logical button/dialog names to Qt standard
icons so a toolbar button and the dialog it opens share the same glyph.

Qt's standard icon set is small, so some entries map to the closest fit
(e.g. ``bookmarks`` -> apply-check, ``stats`` -> detailed-view). Buttons or
dialogs without a sensible mapping are omitted; callers should treat a
missing entry as "no icon".
"""

from __future__ import annotations

from typing import Callable, Dict

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QStyle

# Logical name -> StandardPixmap. Add here once; toolbar and dialogs read
# from the same table so they cannot drift apart.
_ICON_MAP: Dict[str, QStyle.StandardPixmap] = {
    "connect":   QStyle.SP_ComputerIcon,
    "refresh":   QStyle.SP_BrowserReload,
    "clear":     QStyle.SP_DialogResetButton,
    "save":      QStyle.SP_DialogSaveButton,
    "open":      QStyle.SP_DialogOpenButton,
    "stats":     QStyle.SP_FileDialogDetailedView,
    "memory":    QStyle.SP_DriveHDIcon,
    "network":   QStyle.SP_DriveNetIcon,
    "packages":  QStyle.SP_DirIcon,
    "view":      QStyle.SP_FileDialogContentsView,
    "bookmarks": QStyle.SP_DialogApplyButton,
    "crashes":   QStyle.SP_MessageBoxCritical,
    "about":     QStyle.SP_MessageBoxQuestion,
}

# Rainbow wedge colors, drawn clockwise starting at 12 o'clock.
_RAINBOW = [
    "#E53935",  # red
    "#FB8C00",  # orange
    "#FDD835",  # yellow
    "#43A047",  # green
    "#1E88E5",  # blue
    "#8E24AA",  # violet
]


def _make_colors_icon() -> QIcon:
    """Draw a rainbow-wedge circle for the Colors button — no standard Qt
    icon fits "color rules", so this one is painted directly."""
    size = 32
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    margin = 2
    rect = pm.rect().adjusted(margin, margin, -margin, -margin)
    span = 360 * 16 // len(_RAINBOW)
    start = 90 * 16  # 12 o'clock, Qt angles are counter-clockwise
    for color in _RAINBOW:
        painter.setBrush(QColor(color))
        painter.drawPie(rect, start, -span)
        start -= span
    painter.end()
    return QIcon(pm)


# Logical name -> generator producing a fully custom-painted QIcon. Checked
# before _ICON_MAP so an entry here always wins.
_GENERATED_ICONS: Dict[str, Callable[[], QIcon]] = {
    "colors": _make_colors_icon,
}


def app_icon(name: str) -> QIcon:
    """Return the QIcon for *name*, or an empty QIcon if unmapped."""
    generator = _GENERATED_ICONS.get(name)
    if generator is not None:
        return generator()
    sp = _ICON_MAP.get(name)
    if sp is None:
        return QIcon()
    app = QApplication.instance()
    if app is None:
        return QIcon()
    return app.style().standardIcon(sp)
