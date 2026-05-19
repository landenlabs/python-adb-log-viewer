from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .icons import app_icon
from .log_model import LogFilterProxy, LogModel
from .log_record import LogRecord


class BookmarksDialog(QDialog):
    """List of bookmarked rows. Clicking jumps the main table to the row."""

    jump_to_row_id = Signal(int)

    def __init__(
        self,
        model: LogModel,
        proxy: LogFilterProxy,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bookmarks")
        self.setWindowIcon(app_icon("bookmarks"))
        self.resize(380, 480)
        self._model = model
        self._proxy = proxy

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        desc = QLabel(
            "Bookmarks add a visual divider in the log and let you jump back to a "
            "position. Toggle on the selected row with <b>Ctrl+B</b> or via the log "
            "right-click menu. Bookmarks bypass <b>Tag</b>, <b>Message</b>, "
            "<b>PID</b>, and <b>Exclude</b> filters — Level and time range still "
            "apply. The bookmark color is set in the <b>Colors</b> dialog."
        )
        desc.setWordWrap(True)
        desc.setTextFormat(Qt.RichText)
        desc.setObjectName("BookmarksDesc")
        desc.setStyleSheet(
            "QLabel#BookmarksDesc {"
            "  color: palette(text);"
            "  background-color: palette(alternate-base);"
            "  border: 1px solid palette(mid);"
            "  border-radius: 4px;"
            "  padding: 8px 10px;"
            "  font-size: 11px;"
            "}"
        )
        root.addWidget(desc)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Plain)
        divider.setFixedHeight(1)
        divider.setStyleSheet("background-color: palette(mid); border: none;")
        root.addWidget(divider)

        self._lbl_count = QLabel("0 bookmarks")
        count_font = self._lbl_count.font()
        count_font.setBold(True)
        self._lbl_count.setFont(count_font)
        root.addWidget(self._lbl_count)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setUniformItemSizes(True)
        self._list.itemActivated.connect(self._on_activated)
        self._list.itemClicked.connect(self._on_activated)
        root.addWidget(self._list, stretch=1)

        btn_row = QHBoxLayout()
        self._btn_remove = QPushButton("Remove Selected")
        self._btn_remove.clicked.connect(self._on_remove)
        btn_row.addWidget(self._btn_remove)

        self._btn_clear = QPushButton("Clear All")
        self._btn_clear.clicked.connect(self._on_clear)
        btn_row.addWidget(self._btn_clear)

        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ================================================================== refresh
    def refresh(self) -> None:
        self._list.clear()
        recs = self._model.bookmarked_records()
        self._lbl_count.setText(f"{len(recs)} bookmark{'s' if len(recs) != 1 else ''}")
        for rec in recs:
            self._list.addItem(self._make_item(rec))

    def _make_item(self, rec: LogRecord) -> QListWidgetItem:
        visible = self._proxy.accepts_for_timeline(rec)
        suffix = "" if visible else "   (hidden by filter)"
        text = f"{rec.timestamp}  B  (bookmark){suffix}"
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, rec.row_id)
        if not visible:
            item.setForeground(Qt.gray)
        return item

    # ================================================================== actions
    def _on_activated(self, item: QListWidgetItem) -> None:
        row_id = item.data(Qt.UserRole)
        if isinstance(row_id, int):
            self.jump_to_row_id.emit(row_id)

    def _on_remove(self) -> None:
        for item in list(self._list.selectedItems()):
            row_id = item.data(Qt.UserRole)
            if isinstance(row_id, int):
                self._model.remove_bookmark_row(row_id)
        self.refresh()

    def _on_clear(self) -> None:
        self._model.clear_bookmark_rows()
        self.refresh()
