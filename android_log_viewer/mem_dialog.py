from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .mem_reader import MemReader

# Column indices
COL_PID   = 0
COL_NAME  = 1
COL_MEM   = 2
COL_GRAB  = 3
COL_DELTA = 4

_DELTA_GROW    = QColor("#C62828")  # red    — grew since grab
_DELTA_SHRINK  = QColor("#2E7D32")  # green  — shrank since grab
_DELTA_NEUTRAL = QColor("#9E9E9E")  # gray   — unchanged

_MONO = QFont("Courier New", 9)

_INTERVALS = [("2 s", 2_000), ("5 s", 5_000), ("10 s", 10_000), ("30 s", 30_000)]
_DEFAULT_IDX = 1   # 5 s


# ------------------------------------------------------------------ item helpers

class _NumItem(QTableWidgetItem):
    """QTableWidgetItem with integer sort key."""

    def __init__(self, text: str, sort_key: int) -> None:
        super().__init__(text)
        self._sort_key = sort_key
        self.setFlags(self.flags() & ~Qt.ItemIsEditable)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, _NumItem):
            return self._sort_key < other._sort_key
        return super().__lt__(other)


def _ro(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def _num(val: int, right_align: bool = True) -> _NumItem:
    item = _NumItem(f"{val:,}", val)
    if right_align:
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return item


def _delta_item(delta: int) -> _NumItem:
    text = "0" if delta == 0 else f"{delta:+,}"
    item = _NumItem(text, delta)
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if delta > 0:
        item.setForeground(_DELTA_GROW)
    elif delta < 0:
        item.setForeground(_DELTA_SHRINK)
    else:
        item.setForeground(_DELTA_NEUTRAL)
    return item


# ------------------------------------------------------------------ dialog

class MemDialog(QDialog):
    """
    Non-modal dialog: live per-process RSS memory monitor.

    - Auto-refreshes at a configurable interval via MemReader (background QThread).
    - "Grab" snapshots the current Memory column; the Delta column then shows
      byte-for-byte change from that baseline on every subsequent refresh.
    - Processes can be filtered by name regex.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Memory Monitor")
        self.resize(820, 580)
        self.setModal(False)

        self._device: Optional[str] = None
        self._reader: Optional[MemReader] = None
        self._grab_data: Dict[str, int] = {}   # pid -> grabbed rss (KB)
        self._last_stats: List[Dict[str, Any]] = []

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._start_refresh)

        self._build_ui()

    # ================================================================== build

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # ---- control row ----
        ctrl = QHBoxLayout()

        ctrl.addWidget(QLabel("Refresh:"))
        self._interval_combo = QComboBox()
        for label, _ in _INTERVALS:
            self._interval_combo.addItem(label)
        self._interval_combo.setCurrentIndex(_DEFAULT_IDX)
        self._interval_combo.currentIndexChanged.connect(self._on_interval_changed)
        ctrl.addWidget(self._interval_combo)

        self._btn_refresh = QPushButton("Refresh Now")
        self._btn_refresh.setToolTip("Trigger an immediate memory poll")
        self._btn_refresh.clicked.connect(self._start_refresh)
        ctrl.addWidget(self._btn_refresh)

        ctrl.addSpacing(16)

        self._btn_grab = QPushButton("Grab")
        self._btn_grab.setToolTip(
            "Snapshot current Memory values into the Grab column.\n"
            "The Delta column will track changes from this baseline."
        )
        self._btn_grab.clicked.connect(self._on_grab)
        ctrl.addWidget(self._btn_grab)

        self._btn_clear_grab = QPushButton("Clear Grab")
        self._btn_clear_grab.setToolTip("Clear the Grab baseline and Delta column.")
        self._btn_clear_grab.clicked.connect(self._on_clear_grab)
        ctrl.addWidget(self._btn_clear_grab)

        ctrl.addStretch()

        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.hide)
        ctrl.addWidget(self._btn_close)

        root.addLayout(ctrl)

        # ---- filter row ----
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Process name regex…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._apply_filter)
        frow.addWidget(self._filter_edit)
        self._filter_lbl = QLabel("")
        self._filter_lbl.setStyleSheet("color: gray; min-width: 60px;")
        frow.addWidget(self._filter_lbl)
        root.addLayout(frow)

        # ---- table ----
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["PID", "Process Name", "Memory (KB)", "Grab (KB)", "Delta (KB)"]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(20)
        self._table.setShowGrid(False)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setSortIndicatorShown(True)
        self._table.setFont(_MONO)

        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(COL_PID,   QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_NAME,  QHeaderView.Stretch)
        hh.setSectionResizeMode(COL_MEM,   QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_GRAB,  QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_DELTA, QHeaderView.Fixed)
        self._table.setColumnWidth(COL_PID,    65)
        self._table.setColumnWidth(COL_MEM,   110)
        self._table.setColumnWidth(COL_GRAB,  110)
        self._table.setColumnWidth(COL_DELTA, 110)

        # Default sort: Memory descending
        self._table.horizontalHeader().setSortIndicator(COL_MEM, Qt.DescendingOrder)

        root.addWidget(self._table, stretch=1)

        # ---- status ----
        self._lbl_status = QLabel("Not yet refreshed")
        self._lbl_status.setStyleSheet("color: gray; font-size: 11px;")
        root.addWidget(self._lbl_status)

    # ================================================================== lifecycle

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._start_refresh()
        self._timer.start(self._interval_ms())

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def shutdown(self) -> None:
        """Terminate background thread. Must be called before the widget is destroyed."""
        self._timer.stop()
        reader = self._reader
        if reader and reader.isRunning():
            reader.terminate()
            reader.wait(500)

    # ================================================================== public API

    def set_device(self, device: Optional[str]) -> None:
        self._device = device

    # ================================================================== refresh

    def _interval_ms(self) -> int:
        return _INTERVALS[self._interval_combo.currentIndex()][1]

    def _on_interval_changed(self) -> None:
        if self._timer.isActive():
            self._timer.setInterval(self._interval_ms())

    def _start_refresh(self) -> None:
        if self._reader and self._reader.isRunning():
            return
        self._btn_refresh.setEnabled(False)
        self._btn_refresh.setText("…")
        self._reader = MemReader(device=self._device, parent=self)
        self._reader.stats_ready.connect(self._on_stats)
        self._reader.error_occurred.connect(self._on_error)
        self._reader.finished.connect(self._on_reader_done)
        self._reader.start()

    def _on_reader_done(self) -> None:
        self._btn_refresh.setEnabled(True)
        self._btn_refresh.setText("Refresh Now")

    def _on_stats(self, stats: List[Dict[str, Any]]) -> None:
        self._last_stats = stats
        self._populate(stats)

    def _on_error(self, msg: str) -> None:
        self._lbl_status.setText(f"Error: {msg}")
        self._lbl_status.setStyleSheet("color: #C62828; font-size: 11px;")

    # ================================================================== populate

    def _populate(self, stats: List[Dict[str, Any]]) -> None:
        # Preserve scroll position and sort state across refreshes
        vsb = self._table.verticalScrollBar()
        scroll_val = vsb.value()
        hh = self._table.horizontalHeader()
        sort_col   = hh.sortIndicatorSection()
        sort_order = hh.sortIndicatorOrder()

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(stats))

        for row, proc in enumerate(stats):
            pid  = proc["pid"]
            rss  = proc["rss"]
            grab = self._grab_data.get(pid)

            pid_item = _NumItem(pid, int(pid))
            pid_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._table.setItem(row, COL_PID,  pid_item)
            self._table.setItem(row, COL_NAME, _ro(proc["name"]))
            self._table.setItem(row, COL_MEM,  _num(rss))

            if grab is not None:
                self._table.setItem(row, COL_GRAB,  _num(grab))
                self._table.setItem(row, COL_DELTA, _delta_item(rss - grab))
            else:
                self._table.setItem(row, COL_GRAB,  _ro(""))
                self._table.setItem(row, COL_DELTA, _ro(""))

        self._table.setSortingEnabled(True)
        self._table.sortByColumn(sort_col, sort_order)
        vsb.setValue(scroll_val)

        self._apply_filter()

        ts = datetime.now().strftime("%H:%M:%S")
        n  = len(stats)
        self._lbl_status.setText(f"Last refresh: {ts}   |   {n:,} processes")
        self._lbl_status.setStyleSheet("color: gray; font-size: 11px;")

    # ================================================================== filter

    def _apply_filter(self) -> None:
        text = self._filter_edit.text().strip()
        try:
            rx = re.compile(text, re.IGNORECASE) if text else None
        except re.error:
            rx = re.compile(re.escape(text), re.IGNORECASE)

        visible = 0
        total = self._table.rowCount()
        for row in range(total):
            item = self._table.item(row, COL_NAME)
            show = rx is None or bool(rx.search(item.text() if item else ""))
            self._table.setRowHidden(row, not show)
            if show:
                visible += 1

        self._filter_lbl.setText(f"{visible}/{total}" if rx is not None else "")

    # ================================================================== grab

    def _on_grab(self) -> None:
        """Snapshot the current Memory column as the Grab baseline."""
        self._table.setSortingEnabled(False)
        for row in range(self._table.rowCount()):
            pid_item = self._table.item(row, COL_PID)
            mem_item = self._table.item(row, COL_MEM)
            if pid_item and isinstance(mem_item, _NumItem):
                pid = pid_item.text()
                rss = mem_item._sort_key
                self._grab_data[pid] = rss
                self._table.setItem(row, COL_GRAB,  _num(rss))
                self._table.setItem(row, COL_DELTA, _delta_item(0))
        self._table.setSortingEnabled(True)

    def _on_clear_grab(self) -> None:
        """Remove all Grab baselines and clear the Grab / Delta columns."""
        self._grab_data.clear()
        self._table.setSortingEnabled(False)
        for row in range(self._table.rowCount()):
            self._table.setItem(row, COL_GRAB,  _ro(""))
            self._table.setItem(row, COL_DELTA, _ro(""))
        self._table.setSortingEnabled(True)
