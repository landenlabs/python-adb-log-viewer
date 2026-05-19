from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor

from .log_model import make_mono_font
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QComboBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .icons import app_icon
from .net_reader import NetReader

COL_IFACE    = 0
COL_RX_RATE  = 1
COL_TX_RATE  = 2
COL_RX_TOTAL = 3
COL_TX_TOTAL = 4
COL_RX_DELTA = 5
COL_TX_DELTA = 6

_DELTA_ACTIVE = QColor("#1565C0")   # blue  — traffic accumulated since grab
_DELTA_ZERO   = QColor("#9E9E9E")   # gray  — no change since grab
_RATE_ACTIVE  = QColor("#2E7D32")   # green — non-zero rate

_MONO = make_mono_font(10)

_INTERVALS = [("2 s", 2_000), ("5 s", 5_000), ("10 s", 10_000), ("30 s", 30_000)]
_DEFAULT_IDX = 1   # 5 s


# ------------------------------------------------------------------ formatting

def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.2f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


def _fmt_rate(bps: float) -> str:
    return f"{_fmt_bytes(int(bps))}/s"


# ------------------------------------------------------------------ table item helpers

class _NumItem(QTableWidgetItem):
    """QTableWidgetItem with integer sort key."""

    def __init__(self, text: str, sort_key: int) -> None:
        super().__init__(text)
        self._sort_key = sort_key
        self.setFlags(self.flags() & ~Qt.ItemIsEditable)
        self.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, _NumItem):
            return self._sort_key < other._sort_key
        return self.text() < other.text()


def _ro(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def _bytes_item(n: int) -> _NumItem:
    return _NumItem(_fmt_bytes(n), n)


def _rate_item(bps: float, has_prev: bool) -> _NumItem:
    if not has_prev:
        item = _NumItem("—", -1)
        return item
    item = _NumItem(_fmt_rate(bps), int(bps))
    if bps > 0:
        item.setForeground(_RATE_ACTIVE)
    return item


def _delta_item(delta: int) -> _NumItem:
    if delta <= 0:
        item = _NumItem("0", 0)
        item.setForeground(_DELTA_ZERO)
    else:
        item = _NumItem(f"+{_fmt_bytes(delta)}", delta)
        item.setForeground(_DELTA_ACTIVE)
    return item


# ------------------------------------------------------------------ dialog

class NetDialog(QDialog):
    """
    Non-modal dialog: live per-interface network traffic monitor.

    - Polls /proc/net/dev (or /sys/class/net/*/statistics/ fallback) via adb.
    - RX/TX Rate columns show throughput between the last two polls.
    - RX/TX Total columns show cumulative bytes since device boot.
    - Grab snapshots the current totals; Δ RX / Δ TX then track bytes
      transferred since that grab point.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Network Monitor")
        self.setWindowIcon(app_icon("network"))
        self.resize(860, 420)
        self.setModal(False)

        self._device: Optional[str] = None
        self._adb_exe: str = "adb"
        self._reader: Optional[NetReader] = None
        self._grab_data: Dict[str, Dict[str, int]] = {}   # iface -> {rx, tx}
        self._grab_time: Optional[float] = None
        self._prev_poll: Dict[str, Dict[str, int]] = {}   # iface -> {rx, tx}
        self._prev_time: float = 0.0
        self._last_stats: List[Dict[str, Any]] = []

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._start_refresh)

        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(200)
        self._countdown_timer.timeout.connect(self._on_countdown_tick)
        self._tick_start: float = 0.0

        self._grab_elapsed_timer = QTimer(self)
        self._grab_elapsed_timer.setInterval(1000)
        self._grab_elapsed_timer.timeout.connect(self._update_grab_elapsed)

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
        self._btn_refresh.setToolTip("Trigger an immediate network poll")
        self._btn_refresh.clicked.connect(self._start_refresh)
        fm = self._btn_refresh.fontMetrics()
        self._btn_refresh.setFixedWidth(
            max(fm.horizontalAdvance("Refresh Now"), fm.horizontalAdvance("Refreshing")) + 36
        )
        ctrl.addWidget(self._btn_refresh)

        ctrl.addSpacing(16)

        self._btn_grab = QPushButton("Grab")
        self._btn_grab.setToolTip(
            "Snapshot the current RX/TX totals.\n"
            "Δ RX / Δ TX will then show bytes transferred since this point."
        )
        self._btn_grab.clicked.connect(self._on_grab)
        ctrl.addWidget(self._btn_grab)

        self._btn_clear_grab = QPushButton("Clear Grab")
        self._btn_clear_grab.setToolTip("Remove the Grab baseline and clear the Δ columns.")
        self._btn_clear_grab.clicked.connect(self._on_clear_grab)
        ctrl.addWidget(self._btn_clear_grab)

        self._lbl_grab_elapsed = QLabel("")
        self._lbl_grab_elapsed.setStyleSheet(
            "color: #1565C0; font-weight: bold; margin-left: 8px;"
        )
        ctrl.addWidget(self._lbl_grab_elapsed)

        ctrl.addStretch()

        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.hide)
        ctrl.addWidget(self._btn_close)

        root.addLayout(ctrl)

        # ---- progress bar ----
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        root.addWidget(self._progress_bar)

        # ---- filter row ----
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Interface name regex…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._apply_filter)
        frow.addWidget(self._filter_edit)
        self._filter_lbl = QLabel("")
        self._filter_lbl.setStyleSheet("color: gray; min-width: 60px;")
        frow.addWidget(self._filter_lbl)
        root.addLayout(frow)

        # ---- table ----
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            ["Interface", "RX Rate", "TX Rate", "RX Total", "TX Total", "Δ RX", "Δ TX"]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.setShowGrid(False)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setSortIndicatorShown(True)
        self._table.setFont(_MONO)

        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(COL_IFACE,    QHeaderView.Stretch)
        for col in (COL_RX_RATE, COL_TX_RATE, COL_RX_TOTAL, COL_TX_TOTAL,
                    COL_RX_DELTA, COL_TX_DELTA):
            hh.setSectionResizeMode(col, QHeaderView.Fixed)
            self._table.setColumnWidth(col, 110)

        self._table.horizontalHeader().setSortIndicator(COL_RX_RATE, Qt.DescendingOrder)

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
        if self._grab_time is not None:
            self._grab_elapsed_timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()
        self._grab_elapsed_timer.stop()

    def shutdown(self) -> None:
        """Terminate background thread. Must be called before the widget is destroyed."""
        self._timer.stop()
        self._countdown_timer.stop()
        self._grab_elapsed_timer.stop()
        reader = self._reader
        if reader and reader.isRunning():
            reader.terminate()
            reader.wait(500)

    # ================================================================== public API

    def set_device(self, device: Optional[str]) -> None:
        self._device = device
        title = "Network Monitor"
        if device:
            title += f" — {device}"
        self.setWindowTitle(title)

    def set_adb_exe(self, exe: str) -> None:
        self._adb_exe = exe

    # ================================================================== refresh

    def _interval_ms(self) -> int:
        return _INTERVALS[self._interval_combo.currentIndex()][1]

    def _on_interval_changed(self) -> None:
        if self._timer.isActive():
            self._timer.setInterval(self._interval_ms())
        if self._countdown_timer.isActive():
            self._tick_start = time.monotonic()

    def _on_countdown_tick(self) -> None:
        elapsed_ms = (time.monotonic() - self._tick_start) * 1000
        pct = min(100, int(elapsed_ms / self._interval_ms() * 100))
        self._progress_bar.setValue(pct)

    def _start_refresh(self) -> None:
        if self._reader and self._reader.isRunning():
            return
        self._countdown_timer.stop()
        self._progress_bar.setRange(0, 0)
        self._btn_refresh.setEnabled(False)
        self._btn_refresh.setText("Refreshing")
        self._reader = NetReader(device=self._device, adb_exe=self._adb_exe, parent=self)
        self._reader.stats_ready.connect(self._on_stats)
        self._reader.error_occurred.connect(self._on_error)
        self._reader.finished.connect(self._on_reader_done)
        self._reader.start()

    def _on_reader_done(self) -> None:
        self._btn_refresh.setEnabled(True)
        self._btn_refresh.setText("Refresh Now")
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._tick_start = time.monotonic()
        self._countdown_timer.start()

    def _on_stats(self, stats: List[Dict[str, Any]]) -> None:
        now = time.monotonic()
        self._last_stats = stats
        self._populate(stats, now)
        self._prev_poll = {
            s["iface"]: {"rx": s["rx_bytes"], "tx": s["tx_bytes"]} for s in stats
        }
        self._prev_time = now

    def _on_error(self, msg: str) -> None:
        self._lbl_status.setText(f"Error: {msg}")
        self._lbl_status.setStyleSheet("color: #C62828; font-size: 11px;")

    # ================================================================== populate

    def _populate(self, stats: List[Dict[str, Any]], now: float) -> None:
        elapsed = now - self._prev_time if self._prev_time else 0.0
        has_rate = elapsed > 0.1  # need at least one prior poll for a meaningful rate

        vsb = self._table.verticalScrollBar()
        scroll_val = vsb.value()
        hh = self._table.horizontalHeader()
        sort_col   = hh.sortIndicatorSection()
        sort_order = hh.sortIndicatorOrder()

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(stats))

        for row, s in enumerate(stats):
            iface    = s["iface"]
            rx_bytes = s["rx_bytes"]
            tx_bytes = s["tx_bytes"]

            prev = self._prev_poll.get(iface)
            if has_rate and prev is not None:
                rx_rate = max(0.0, (rx_bytes - prev["rx"]) / elapsed)
                tx_rate = max(0.0, (tx_bytes - prev["tx"]) / elapsed)
                have_prev = True
            else:
                rx_rate = tx_rate = 0.0
                have_prev = False

            grab = self._grab_data.get(iface)

            self._table.setItem(row, COL_IFACE,    _ro(iface))
            self._table.setItem(row, COL_RX_RATE,  _rate_item(rx_rate, have_prev))
            self._table.setItem(row, COL_TX_RATE,  _rate_item(tx_rate, have_prev))
            self._table.setItem(row, COL_RX_TOTAL, _bytes_item(rx_bytes))
            self._table.setItem(row, COL_TX_TOTAL, _bytes_item(tx_bytes))

            if grab is not None:
                self._table.setItem(row, COL_RX_DELTA, _delta_item(max(0, rx_bytes - grab["rx"])))
                self._table.setItem(row, COL_TX_DELTA, _delta_item(max(0, tx_bytes - grab["tx"])))
            else:
                self._table.setItem(row, COL_RX_DELTA, _ro(""))
                self._table.setItem(row, COL_TX_DELTA, _ro(""))

        self._table.setSortingEnabled(True)
        self._table.sortByColumn(sort_col, sort_order)
        vsb.setValue(scroll_val)

        self._apply_filter()

        ts = datetime.now().strftime("%H:%M:%S")
        self._lbl_status.setText(
            f"Last refresh: {ts}   |   {len(stats)} interface{'s' if len(stats) != 1 else ''}"
        )
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
            item = self._table.item(row, COL_IFACE)
            show = rx is None or bool(rx.search(item.text() if item else ""))
            self._table.setRowHidden(row, not show)
            if show:
                visible += 1

        self._filter_lbl.setText(f"{visible}/{total}" if rx is not None else "")

    # ================================================================== grab

    def _on_grab(self) -> None:
        """Snapshot current totals; Δ columns will track bytes since this point."""
        self._grab_data = {
            s["iface"]: {"rx": s["rx_bytes"], "tx": s["tx_bytes"]}
            for s in self._last_stats
        }

        self._table.setSortingEnabled(False)
        for row in range(self._table.rowCount()):
            self._table.setItem(row, COL_RX_DELTA, _delta_item(0))
            self._table.setItem(row, COL_TX_DELTA, _delta_item(0))
        self._table.setSortingEnabled(True)

        self._grab_time = time.monotonic()
        self._update_grab_elapsed()
        self._grab_elapsed_timer.start()

    def _on_clear_grab(self) -> None:
        self._grab_data.clear()
        self._table.setSortingEnabled(False)
        for row in range(self._table.rowCount()):
            self._table.setItem(row, COL_RX_DELTA, _ro(""))
            self._table.setItem(row, COL_TX_DELTA, _ro(""))
        self._table.setSortingEnabled(True)

        self._grab_time = None
        self._lbl_grab_elapsed.setText("")
        self._grab_elapsed_timer.stop()

    def _update_grab_elapsed(self) -> None:
        if self._grab_time is None:
            self._lbl_grab_elapsed.setText("")
            return
        elapsed = int(time.monotonic() - self._grab_time)
        m, s = divmod(elapsed, 60)
        h, m = divmod(m, 60)
        if h > 0:
            text = f"Grabbed {h}h {m}m {s}s ago"
        elif m > 0:
            text = f"Grabbed {m}m {s}s ago"
        else:
            text = f"Grabbed {s}s ago"
        self._lbl_grab_elapsed.setText(text)
