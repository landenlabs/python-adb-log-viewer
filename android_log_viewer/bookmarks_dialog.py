from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

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
        self.resize(720, 480)
        self._model = model
        self._proxy = proxy

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self._lbl_count = QLabel("0 bookmarks")
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
        msg_head = rec.message.split("\n", 1)[0]
        if len(msg_head) > 200:
            msg_head = msg_head[:200] + "…"
        visible = self._proxy.accepts_for_timeline(rec)
        suffix = "" if visible else "   (hidden by filter)"
        text = f"{rec.timestamp}  {rec.level}  {rec.tag}: {msg_head}{suffix}"
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
                self._model.remove_bookmark(row_id)
        self.refresh()

    def _on_clear(self) -> None:
        self._model.clear_bookmarks()
        self.refresh()
