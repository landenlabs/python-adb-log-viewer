from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTableWidgetSelectionRange,
    QVBoxLayout,
    QWidget,
)

from .ps_reader import PsReader
from .stats import StatsTracker


# ------------------------------------------------------------------ item types

class _NumItem(QTableWidgetItem):
    """QTableWidgetItem that sorts numerically (for Count and PID columns)."""

    def __init__(self, text: str, sort_key: int) -> None:
        super().__init__(text)
        self._sort_key = sort_key
        self.setFlags(self.flags() & ~Qt.ItemIsEditable)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, _NumItem):
            return self._sort_key < other._sort_key
        try:
            return int(self.text()) < int(other.text())
        except (ValueError, TypeError):
            return super().__lt__(other)


def _ro(text: str) -> QTableWidgetItem:
    """Read-only plain text item."""
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


# ------------------------------------------------------------------ dialog

class StatsDialog(QDialog):
    """
    Non-modal dialog with two side-by-side tables (PID stats, Tag stats).
    Emits filter_applied(pids, tags) when the user clicks 'Filter Selected'.
    Empty sets mean "no restriction on that dimension".
    """

    filter_applied = Signal(set, set)   # (Set[str] pids, Set[str] tags)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Log Statistics")
        self.resize(1100, 540)
        self.setModal(False)

        self._proc_names: Dict[str, str] = {}
        self._active_pids: Set[str] = set()
        self._active_tags: Set[str] = set()
        self._tracker: Optional[StatsTracker] = None
        self._ps_reader: Optional[PsReader] = None
        self._device: Optional[str] = None
        self._adb_exe: str = "adb"

        self._build_ui()

    # ================================================================== build

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_pid_panel())
        splitter.addWidget(self._build_tag_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setHandleWidth(2)
        root.addWidget(splitter, stretch=1)

        # ---- button row ----
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._btn_refresh_stats = QPushButton("⟳ Stats")
        self._btn_refresh_stats.setToolTip("Repopulate tables from current data")
        btn_row.addWidget(self._btn_refresh_stats)

        self._btn_refresh_names = QPushButton("⟳ Names")
        self._btn_refresh_names.setToolTip("Resolve PIDs to process names via 'adb ps'")
        btn_row.addWidget(self._btn_refresh_names)

        btn_row.addSpacing(16)

        self._btn_filter = QPushButton("Apply Filter")
        self._btn_filter.setToolTip("Filter main log list by selected PIDs/Tags")
        self._btn_filter.setMinimumWidth(100)
        btn_row.addWidget(self._btn_filter)

        self._btn_clear_filter = QPushButton("Clear Filter")
        btn_row.addWidget(self._btn_clear_filter)

        btn_row.addStretch()

        self._btn_close = QPushButton("Close")
        btn_row.addWidget(self._btn_close)

        root.addLayout(btn_row)

        # ---- status line ----
        self._lbl_status = QLabel("No filter active")
        self._lbl_status.setStyleSheet("color: gray; padding-left: 2px;")
        root.addWidget(self._lbl_status)

        # ---- wire ----
        self._btn_refresh_stats.clicked.connect(self._on_refresh_stats)
        self._btn_refresh_names.clicked.connect(self._on_refresh_names)
        self._btn_filter.clicked.connect(self._on_filter_clicked)
        self._btn_clear_filter.clicked.connect(self._on_clear_filter_clicked)
        self._btn_close.clicked.connect(self.hide)

    def _build_pid_panel(self) -> QWidget:
        grp = QGroupBox("PIDs  –  top 100 by message count")
        layout = QVBoxLayout(grp)
        layout.setContentsMargins(4, 8, 4, 4)

        # Filter bar
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Filter:"))
        self._pid_filter = QLineEdit()
        self._pid_filter.setPlaceholderText("Process Name regex…")
        self._pid_filter.setClearButtonEnabled(True)
        self._pid_filter.setToolTip(
            "Show only rows whose Process Name matches.\n"
            "Click 'Refresh Names (adb ps)' first to populate the Process Name column."
        )
        frow.addWidget(self._pid_filter)
        self._pid_filter_lbl = QLabel("")
        self._pid_filter_lbl.setStyleSheet("color: gray; min-width: 52px;")
        frow.addWidget(self._pid_filter_lbl)
        layout.addLayout(frow)

        self._pid_table = QTableWidget()
        self._pid_table.setColumnCount(5)
        self._pid_table.setHorizontalHeaderLabels(
            ["PID", "Count", "First Log", "Last Log", "Process Name"]
        )
        _setup_table(self._pid_table)
        self._pid_table.setColumnWidth(0, 65)
        self._pid_table.setColumnWidth(1, 65)
        self._pid_table.setColumnWidth(2, 148)
        self._pid_table.setColumnWidth(3, 148)
        self._pid_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._pid_table)

        self._pid_filter.textChanged.connect(self._apply_pid_filter)
        return grp

    def _build_tag_panel(self) -> QWidget:
        grp = QGroupBox("Tags  –  top 100 by message count")
        layout = QVBoxLayout(grp)
        layout.setContentsMargins(4, 8, 4, 4)

        # Filter bar
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Filter:"))
        self._tag_filter = QLineEdit()
        self._tag_filter.setPlaceholderText("Tag regex…")
        self._tag_filter.setClearButtonEnabled(True)
        self._tag_filter.setToolTip("Show only rows whose Tag matches this regex.")
        frow.addWidget(self._tag_filter)
        self._tag_filter_lbl = QLabel("")
        self._tag_filter_lbl.setStyleSheet("color: gray; min-width: 52px;")
        frow.addWidget(self._tag_filter_lbl)
        layout.addLayout(frow)

        self._tag_table = QTableWidget()
        self._tag_table.setColumnCount(4)
        self._tag_table.setHorizontalHeaderLabels(
            ["Tag", "Count", "First Log", "Last Log"]
        )
        _setup_table(self._tag_table)
        self._tag_table.setColumnWidth(0, 200)
        self._tag_table.setColumnWidth(1, 65)
        self._tag_table.setColumnWidth(2, 148)
        self._tag_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._tag_table)

        self._tag_filter.textChanged.connect(self._apply_tag_filter)
        return grp

    # ================================================================== populate

    def refresh(self, tracker: StatsTracker) -> None:
        """Repopulate both tables from the latest snapshot. Preserves selection."""
        self._tracker = tracker
        self._populate_pid_table(tracker.pid_stats())
        self._populate_tag_table(tracker.tag_stats())

    def _populate_pid_table(self, stats: List[Tuple[str, int, str, str]]) -> None:
        prev_sel = self._selected_pids()

        self._pid_table.setSortingEnabled(False)
        self._pid_table.setRowCount(len(stats))

        for row, (pid, count, first, last) in enumerate(stats):
            self._pid_table.setItem(row, 0, _NumItem(pid, int(pid) if pid.isdigit() else 0))
            self._pid_table.setItem(row, 1, _NumItem(str(count), count))
            self._pid_table.setItem(row, 2, _ro(first))
            self._pid_table.setItem(row, 3, _ro(last))
            self._pid_table.setItem(row, 4, _ro(self._proc_names.get(pid, "")))

        self._pid_table.setSortingEnabled(True)
        _restore_selection(self._pid_table, prev_sel, col=0)
        self._apply_pid_filter()

    def _populate_tag_table(self, stats: List[Tuple[str, int, str, str]]) -> None:
        prev_sel = self._selected_tags()

        self._tag_table.setSortingEnabled(False)
        self._tag_table.setRowCount(len(stats))

        for row, (tag, count, first, last) in enumerate(stats):
            self._tag_table.setItem(row, 0, _ro(tag))
            self._tag_table.setItem(row, 1, _NumItem(str(count), count))
            self._tag_table.setItem(row, 2, _ro(first))
            self._tag_table.setItem(row, 3, _ro(last))

        self._tag_table.setSortingEnabled(True)
        _restore_selection(self._tag_table, prev_sel, col=0)
        self._apply_tag_filter()

    # ================================================================== selection helpers

    def _selected_pids(self) -> Set[str]:
        return _selected_values(self._pid_table, col=0)

    def _selected_tags(self) -> Set[str]:
        return _selected_values(self._tag_table, col=0)

    # ================================================================== table filters

    def _apply_pid_filter(self) -> None:
        """Hide PID rows whose Process Name doesn't match the filter regex."""
        rx = _compile_rx(self._pid_filter.text().strip())
        visible = 0
        total = self._pid_table.rowCount()
        for row in range(total):
            name_item = self._pid_table.item(row, 4)
            name = name_item.text() if name_item else ""
            show = rx is None or bool(rx.search(name))
            self._pid_table.setRowHidden(row, not show)
            if show:
                visible += 1
        self._pid_filter_lbl.setText(f"{visible}/{total}" if rx is not None else "")

    def _apply_tag_filter(self) -> None:
        """Hide Tag rows whose Tag doesn't match the filter regex."""
        rx = _compile_rx(self._tag_filter.text().strip())
        visible = 0
        total = self._tag_table.rowCount()
        for row in range(total):
            tag_item = self._tag_table.item(row, 0)
            tag = tag_item.text() if tag_item else ""
            show = rx is None or bool(rx.search(tag))
            self._tag_table.setRowHidden(row, not show)
            if show:
                visible += 1
        self._tag_filter_lbl.setText(f"{visible}/{total}" if rx is not None else "")

    # ================================================================== lifecycle

    def shutdown(self) -> None:
        """Terminate background thread. Must be called before the widget is destroyed."""
        reader = self._ps_reader
        if reader and reader.isRunning():
            reader.terminate()
            reader.wait(500)

    # ================================================================== button handlers

    def _on_refresh_stats(self) -> None:
        if self._tracker:
            self.refresh(self._tracker)

    def _on_refresh_names(self) -> None:
        if self._ps_reader and self._ps_reader.isRunning():
            return
        self._ps_reader = PsReader(device=self._device, adb_exe=self._adb_exe, parent=self)
        self._ps_reader.names_ready.connect(self._on_names_ready)
        self._ps_reader.start()
        self._btn_refresh_names.setEnabled(False)
        self._btn_refresh_names.setText("Loading…")

    def _on_names_ready(self, names: Dict[str, str]) -> None:
        self._proc_names.update(names)
        self._btn_refresh_names.setEnabled(True)
        self._btn_refresh_names.setText("Refresh Names  (adb ps)")
        # Update the Process Name column in-place without a full repopulate
        for row in range(self._pid_table.rowCount()):
            pid_item = self._pid_table.item(row, 0)
            if pid_item:
                self._pid_table.setItem(row, 4, _ro(self._proc_names.get(pid_item.text(), "")))
        self._apply_pid_filter()   # re-evaluate now that names are filled in

    def _on_filter_clicked(self) -> None:
        self._active_pids = self._selected_pids()
        self._active_tags = self._selected_tags()
        self.filter_applied.emit(self._active_pids, self._active_tags)
        self._update_status_label()

    def _on_clear_filter_clicked(self) -> None:
        self._pid_table.clearSelection()
        self._tag_table.clearSelection()
        self._active_pids = set()
        self._active_tags = set()
        self.filter_applied.emit(set(), set())
        self._update_status_label()

    def _update_status_label(self) -> None:
        parts: List[str] = []
        if self._active_pids:
            parts.append(f"{len(self._active_pids)} PID(s)")
        if self._active_tags:
            parts.append(f"{len(self._active_tags)} Tag(s)")
        if parts:
            self._lbl_status.setText(f"Filter active: {', '.join(parts)}")
            self._lbl_status.setStyleSheet("color: #E65100; font-weight: bold; padding-left: 2px;")
        else:
            self._lbl_status.setText("No filter active")
            self._lbl_status.setStyleSheet("color: gray; padding-left: 2px;")

    # ================================================================== public API

    def set_device(self, device: Optional[str]) -> None:
        self._device = device

    def set_adb_exe(self, exe: str) -> None:
        self._adb_exe = exe

    @property
    def active_pids(self) -> Set[str]:
        return self._active_pids

    @property
    def active_tags(self) -> Set[str]:
        return self._active_tags


# ================================================================== helpers

def _compile_rx(pattern: str) -> Optional[re.Pattern]:
    """Return a compiled pattern, falling back to literal match on invalid regex."""
    if not pattern:
        return None
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)


def _setup_table(t: QTableWidget) -> None:
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.ExtendedSelection)
    t.setSortingEnabled(True)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(20)
    t.setShowGrid(False)
    t.setSortingEnabled(True)
    t.horizontalHeader().setSortIndicatorShown(True)


def _selected_values(table: QTableWidget, col: int) -> Set[str]:
    rows = {idx.row() for idx in table.selectedIndexes()}
    result: Set[str] = set()
    for row in rows:
        item = table.item(row, col)
        if item:
            result.add(item.text())
    return result


def _restore_selection(table: QTableWidget, values: Set[str], col: int) -> None:
    if not values:
        return
    n_cols = table.columnCount()
    for row in range(table.rowCount()):
        item = table.item(row, col)
        if item and item.text() in values:
            table.setRangeSelected(
                QTableWidgetSelectionRange(row, 0, row, n_cols - 1), True
            )
