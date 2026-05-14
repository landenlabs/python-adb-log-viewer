from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QModelIndex, QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .app_settings import AppSettings
from .constants import LEVEL_NAMES, LEVELS
from .log_model import COL_MSG, COL_TAG, HighlightDelegate, LogFilterProxy, LogModel
from .log_record import LogRecord
from .timeline_widget import TimelineWidget


class FilterViewDialog(QDialog):
    """Secondary filtered log view that shares the main LogModel.

    Owns its own LogFilterProxy so its level/tag/text filters are
    completely independent from the main window's filters.
    """

    def __init__(
        self,
        model: LogModel,
        settings: AppSettings,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Filter View")
        self.resize(1200, 700)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowMinimizeButtonHint
        )

        self._model = model
        self._settings = settings
        self._auto_scroll = True

        self._proxy = LogFilterProxy()
        self._proxy.setSourceModel(model)
        self._proxy.set_exclude_rules(settings.exclude_rules)

        self._timeline_filter_timer = QTimer(self)
        self._timeline_filter_timer.setSingleShot(True)
        self._timeline_filter_timer.setInterval(300)
        self._timeline_filter_timer.timeout.connect(self._rebuild_timeline_filter)

        self._build_ui()
        self._wire_signals()

        levels = {lvl for lvl, cb in self._level_cbs.items() if cb.isChecked()}
        self._proxy.set_levels(levels)

        existing = model.all_records()
        if existing:
            self._timeline.reset(existing)
        self._update_status()

    # ================================================================== build

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        filter_container = QWidget()
        filter_container.setObjectName("filter_container")
        fc_layout = QVBoxLayout(filter_container)
        fc_layout.setContentsMargins(8, 4, 8, 4)
        fc_layout.addWidget(self._build_filter_bar())
        layout.addWidget(filter_container)

        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(1)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QTableView.SelectRows)
        self._table.setSelectionMode(QTableView.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        self._table.verticalHeader().setVisible(False)
        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(True)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.resizeSection(0, 180)
        hh.resizeSection(1, 60)
        hh.resizeSection(2, 60)
        hh.resizeSection(3, 40)
        hh.resizeSection(4, 180)
        vh = self._table.verticalHeader()
        vh.setDefaultSectionSize(20)
        vh.setSectionResizeMode(QHeaderView.Fixed)
        self._highlight_delegate = HighlightDelegate(self._table)
        self._table.setItemDelegateForColumn(COL_TAG, self._highlight_delegate)
        self._table.setItemDelegateForColumn(COL_MSG, self._highlight_delegate)
        splitter.addWidget(self._table)

        self._timeline = TimelineWidget()
        splitter.addWidget(self._timeline)
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

        status_bar = QFrame()
        status_bar.setFrameShape(QFrame.StyledPanel)
        sb_layout = QHBoxLayout(status_bar)
        sb_layout.setContentsMargins(8, 2, 8, 2)
        sb_layout.setSpacing(12)
        self._chk_autoscroll = QCheckBox("Scroll")
        self._chk_autoscroll.setChecked(True)
        self._chk_autoscroll.setToolTip("Auto-scroll to latest")
        sb_layout.addWidget(self._chk_autoscroll)
        sb_layout.addStretch()
        self._lbl_shown = QLabel("0 shown")
        self._lbl_shown.setMinimumWidth(90)
        self._lbl_total = QLabel("0 records")
        self._lbl_total.setMinimumWidth(100)
        sb_layout.addWidget(self._lbl_shown)
        sb_layout.addWidget(self._lbl_total)
        layout.addWidget(status_bar)

    def _build_filter_bar(self) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        row = QHBoxLayout(frame)
        row.setContentsMargins(6, 3, 6, 3)
        row.setSpacing(6)

        row.addWidget(QLabel("Level:"))
        self._level_cbs: dict[str, QCheckBox] = {}
        for lvl in LEVELS:
            cb = QCheckBox(lvl)
            cb.setChecked(True)
            cb.setToolTip(LEVEL_NAMES[lvl])
            self._level_cbs[lvl] = cb
            row.addWidget(cb)

        row.addSpacing(12)

        row.addWidget(QLabel("Tag:"))
        self._tag_edit = QLineEdit()
        self._tag_edit.setPlaceholderText("regex…")
        self._tag_edit.setClearButtonEnabled(True)
        self._tag_edit.setToolTip("Filter by tag name (regex, case-insensitive)")
        row.addWidget(self._tag_edit, stretch=1)

        row.addWidget(QLabel("Text:"))
        self._text_edit = QLineEdit()
        self._text_edit.setPlaceholderText("regex…")
        self._text_edit.setClearButtonEnabled(True)
        self._text_edit.setToolTip("Filter by message text or tag (regex, case-insensitive)")
        row.addWidget(self._text_edit, stretch=1)

        self._btn_clear_filters = QPushButton("✕ Filters")
        self._btn_clear_filters.setToolTip("Reset all level, tag, and text filters")
        row.addWidget(self._btn_clear_filters)

        return frame

    # ================================================================== wiring

    def _wire_signals(self) -> None:
        for cb in self._level_cbs.values():
            cb.toggled.connect(self._on_filter_changed)

        self._tag_edit.textChanged.connect(self._proxy.set_tag_filter)
        self._tag_edit.textChanged.connect(self._update_status)
        self._tag_edit.textChanged.connect(self._schedule_timeline_filter_rebuild)
        self._text_edit.textChanged.connect(self._proxy.set_text_filter)
        self._text_edit.textChanged.connect(self._update_status)
        self._text_edit.textChanged.connect(self._schedule_timeline_filter_rebuild)

        self._btn_clear_filters.clicked.connect(self._clear_filters)
        self._chk_autoscroll.toggled.connect(self._on_autoscroll_toggled)

        self._proxy.rowsInserted.connect(self._on_proxy_rows_inserted)
        self._proxy.modelReset.connect(self._update_status)

        self._table.verticalScrollBar().sliderMoved.connect(self._on_manual_scroll)
        self._table.selectionModel().currentChanged.connect(self._on_table_row_selected)

        self._timeline.timestamp_selected.connect(self._jump_to_timestamp)

    # ================================================================== public API

    def set_device(self, device: Optional[str]) -> None:
        title = "Filter View"
        if device:
            title += f" — {device}"
        self.setWindowTitle(title)

    def add_records(self, records: List[LogRecord]) -> None:
        """Feed a new batch of records into the timeline.  Called by main_window."""
        self._timeline.add_records(records)
        if self._settings.timeline_follows_filter and self._timeline._filtered_buckets is not None:
            filtered_new = [r for r in records if self._proxy.accepts_for_timeline(r)]
            if filtered_new:
                self._timeline.add_filtered_records(filtered_new)
        self._update_status()

    def reset_timeline(self, records: List[LogRecord]) -> None:
        """Full timeline rebuild — called after main model clear or file open."""
        self._timeline.reset(records)
        self._rebuild_timeline_filter()
        self._update_status()

    def apply_exclude_rules(self, rules) -> None:
        """Propagate global exclude-rule changes from the settings dialog."""
        self._proxy.set_exclude_rules(rules)
        self._rebuild_timeline_filter()
        self._update_status()

    # ================================================================== filters

    def _on_filter_changed(self) -> None:
        levels = {lvl for lvl, cb in self._level_cbs.items() if cb.isChecked()}
        self._proxy.set_levels(levels)
        self._update_status()
        self._schedule_timeline_filter_rebuild()

    def _clear_filters(self) -> None:
        for cb in self._level_cbs.values():
            cb.setChecked(True)
        self._tag_edit.clear()
        self._text_edit.clear()

    # ================================================================== timeline

    def _schedule_timeline_filter_rebuild(self) -> None:
        if not self._timeline_filter_timer.isActive():
            self._timeline_filter_timer.start()

    def _has_active_filter(self) -> bool:
        all_levels = {"V", "D", "I", "W", "E", "F"}
        p = self._proxy
        return bool(
            p._allowed != all_levels
            or p._tag_rx is not None
            or p._text_rx is not None
        )

    def _rebuild_timeline_filter(self) -> None:
        if not self._settings.timeline_follows_filter or not self._has_active_filter():
            self._timeline.set_filtered_records(None)
            return
        filtered = [
            r for r in self._model.all_records()
            if not r.is_sub_row and self._proxy.accepts_for_timeline(r)
        ]
        self._timeline.set_filtered_records(filtered)

    # ================================================================== scroll / table

    def _on_autoscroll_toggled(self, checked: bool) -> None:
        self._auto_scroll = checked

    def _on_manual_scroll(self) -> None:
        sb = self._table.verticalScrollBar()
        if sb.value() < sb.maximum():
            self._auto_scroll = False
            self._chk_autoscroll.blockSignals(True)
            self._chk_autoscroll.setChecked(False)
            self._chk_autoscroll.blockSignals(False)

    def _on_proxy_rows_inserted(self) -> None:
        self._update_status()
        if self._auto_scroll:
            self._table.scrollToBottom()

    def _jump_to_timestamp(self, ts: str) -> None:
        src_row = self._model.find_row_for_timestamp(ts)
        src_idx = self._model.index(src_row, 0)
        proxy_idx = self._proxy.mapFromSource(src_idx)
        if proxy_idx.isValid():
            self._table.scrollTo(proxy_idx, QTableView.PositionAtTop)
            self._table.selectRow(proxy_idx.row())

    def _on_table_row_selected(self, current: QModelIndex, previous: QModelIndex) -> None:
        if not current.isValid():
            return
        rec = self._proxy.data(self._proxy.index(current.row(), 0), Qt.UserRole)
        if rec:
            self._timeline.set_cursor_key(rec.timestamp[:17])

    # ================================================================== status

    def _update_status(self) -> None:
        total = self._model.rowCount()
        shown = self._proxy.rowCount()
        self._lbl_total.setText(f"{total:,} records")
        self._lbl_shown.setText(f"{shown:,} shown")
