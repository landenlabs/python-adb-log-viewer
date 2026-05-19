from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QImageReader, QMovie, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .icons import app_icon
from .resources import resource_path
from .version import __version__


def _gif_path() -> Path:
    return resource_path("landen_labs_about_400.gif")


def _build_date() -> str:
    target = Path(__file__).parent / "version.py"
    try:
        return datetime.fromtimestamp(os.path.getmtime(target)).strftime("%Y-%m-%d")
    except OSError:
        return "unknown"


def _bold_label(text: str) -> QLabel:
    lbl = QLabel(text)
    f = lbl.font()
    f.setBold(True)
    lbl.setFont(f)
    return lbl


_DIALOG_WIDTH = 420
_GIF_MAX_W = _DIALOG_WIDTH - 32   # content width after margins


def _gif_display_size(gif: Path) -> QSize:
    """Return display size that preserves the GIF's native aspect ratio."""
    native = QImageReader(str(gif)).size()
    if not native.isValid() or native.width() == 0:
        return QSize(_GIF_MAX_W, _GIF_MAX_W)
    scale = min(1.0, _GIF_MAX_W / native.width())
    return QSize(int(native.width() * scale), int(native.height() * scale))


class AboutDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About Android Log Viewer")
        self.setWindowIcon(app_icon("about"))
        self.setModal(True)
        self.setFixedWidth(_DIALOG_WIDTH)

        self._movie: Optional[QMovie] = None

        self._build_ui()

    # ================================================================== build

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 16)

        gif = _gif_path()

        # --- animated GIF (plays once, freezes on last frame) ---
        self._gif_label = QLabel()
        self._gif_label.setAlignment(Qt.AlignCenter)

        if gif.exists():
            display_size = _gif_display_size(gif)
            self._gif_label.setFixedSize(display_size)
            self._movie = QMovie(str(gif))
            self._movie.setScaledSize(display_size)
            self._gif_label.setMovie(self._movie)
        else:
            self._gif_label.setFixedHeight(80)
            self._gif_label.setText("(logo not found)")

        root.addWidget(self._gif_label, alignment=Qt.AlignCenter)

        # --- icon + app name ---
        name_row = QHBoxLayout()
        name_row.setSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(36, 36)
        icon_png = resource_path("log-viewer.png")
        if icon_png.exists():
            px = QPixmap(str(icon_png)).scaled(
                36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            icon_lbl.setPixmap(px)
        name_row.addWidget(icon_lbl)

        name_font = QFont()
        name_font.setPointSize(15)
        name_font.setBold(True)
        name_lbl = QLabel("Android Log Viewer")
        name_lbl.setFont(name_font)
        name_row.addWidget(name_lbl)
        name_row.addStretch()
        root.addLayout(name_row)

        # --- description ---
        desc = QLabel(
            f"v{__version__}  —  Real-time ADB logcat viewer with live "
            "filtering, color-coding, regex search, and SQLite persistence."
        )
        desc.setWordWrap(True)
        root.addWidget(desc)

        root.addSpacing(4)

        # --- info rows ---
        form = QFormLayout()
        form.setSpacing(5)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        form.addRow(_bold_label("Author:"), QLabel("Dennis Lang"))
        form.addRow(_bold_label("Built:"),  QLabel(_build_date()))
        form.addRow(QLabel(""), QLabel("Created by LanDen Labs (2026)"))

        link = QLabel(
            '<a href="https://github.com/landenlabs/python-adb-log-viewer">'
            "https://github.com/landenlabs/python-adb-log-viewer</a>"
        )
        link.setOpenExternalLinks(True)
        link.setTextFormat(Qt.RichText)
        form.addRow(_bold_label("GitHub:"), link)

        root.addLayout(form)

        root.addSpacing(6)

        # --- close button ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ================================================================== playback

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._movie:
            self._movie.start()
