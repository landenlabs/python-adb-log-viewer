"""Central icon registry. Maps logical button/dialog names to Qt standard
icons so a toolbar button and the dialog it opens share the same glyph.

Qt's standard icon set is small, so some entries map to the closest fit
(e.g. ``bookmarks`` -> apply-check, ``stats`` -> detailed-view). Buttons or
dialogs without a sensible mapping are omitted; callers should treat a
missing entry as "no icon".
"""

from __future__ import annotations

from typing import Dict

from PySide6.QtGui import QIcon
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
    "about":     QStyle.SP_MessageBoxQuestion,
}


def app_icon(name: str) -> QIcon:
    """Return the QIcon for *name*, or an empty QIcon if unmapped."""
    sp = _ICON_MAP.get(name)
    if sp is None:
        return QIcon()
    app = QApplication.instance()
    if app is None:
        return QIcon()
    return app.style().standardIcon(sp)
