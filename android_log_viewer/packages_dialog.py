from __future__ import annotations

import re
import subprocess
from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .icons import app_icon
from .log_model import make_mono_font
from .packages_reader import PackagesReader

_MONO = make_mono_font(10)

COL_PACKAGE = 0


def _ro(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


class PackagesDialog(QDialog):
    """Non-modal dialog listing installed 3rd-party packages on the device.

    Allows uninstall / clear cache / force stop / info for a selected package.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Packages")
        self.setWindowIcon(app_icon("packages"))
        self.resize(720, 540)
        self.setModal(False)

        self._device: Optional[str] = None
        self._adb_exe: str = "adb"
        self._reader: Optional[PackagesReader] = None
        self._packages: List[str] = []

        self._build_ui()

    # ================================================================== build
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # ---- action row ----
        actions = QHBoxLayout()

        self._btn_uninstall = QPushButton("Uninstall")
        self._btn_uninstall.setToolTip("Uninstall the selected package")
        self._btn_uninstall.clicked.connect(self._on_uninstall)
        actions.addWidget(self._btn_uninstall)

        self._btn_clear_cache = QPushButton("Clear cache")
        self._btn_clear_cache.setToolTip("Clear app data and cache for the selected package")
        self._btn_clear_cache.clicked.connect(self._on_clear_cache)
        actions.addWidget(self._btn_clear_cache)

        self._btn_force_stop = QPushButton("Force stop")
        self._btn_force_stop.setToolTip("Force-stop the selected package")
        self._btn_force_stop.clicked.connect(self._on_force_stop)
        actions.addWidget(self._btn_force_stop)

        self._btn_info = QPushButton("Info")
        self._btn_info.setToolTip("Open the system app-details screen for the selected package")
        self._btn_info.clicked.connect(self._on_info)
        actions.addWidget(self._btn_info)

        actions.addStretch()

        self._btn_refresh = QPushButton("Refresh")
        self._btn_refresh.clicked.connect(self._start_refresh)
        actions.addWidget(self._btn_refresh)

        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.hide)
        actions.addWidget(self._btn_close)

        root.addLayout(actions)

        # ---- filter row ----
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Package name regex…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._apply_filter)
        frow.addWidget(self._filter_edit)
        self._filter_lbl = QLabel("")
        self._filter_lbl.setStyleSheet("color: gray; min-width: 60px;")
        frow.addWidget(self._filter_lbl)
        root.addLayout(frow)

        # ---- table ----
        self._table = QTableWidget()
        self._table.setColumnCount(1)
        self._table.setHorizontalHeaderLabels(["Package"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        self._table.setShowGrid(False)
        self._table.setSortingEnabled(True)
        self._table.setFont(_MONO)
        self._table.horizontalHeader().setSectionResizeMode(COL_PACKAGE, QHeaderView.Stretch)
        self._table.itemSelectionChanged.connect(self._update_button_state)
        root.addWidget(self._table, stretch=1)

        # ---- status ----
        self._lbl_status = QLabel("Loading packages…")
        self._lbl_status.setStyleSheet("color: gray; font-size: 11px;")
        root.addWidget(self._lbl_status)

        self._update_button_state()

    # ================================================================== lifecycle
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._start_refresh()

    def shutdown(self) -> None:
        reader = self._reader
        if reader and reader.isRunning():
            reader.terminate()
            reader.wait(500)

    # ================================================================== public API
    def set_device(self, device: Optional[str]) -> None:
        self._device = device
        title = "Packages"
        if device:
            title += f" — {device}"
        self.setWindowTitle(title)

    def set_adb_exe(self, exe: str) -> None:
        self._adb_exe = exe

    # ================================================================== refresh
    def _start_refresh(self) -> None:
        if self._reader and self._reader.isRunning():
            return
        self._btn_refresh.setEnabled(False)
        self._btn_refresh.setText("Loading…")
        self._lbl_status.setText("Loading packages…")
        self._lbl_status.setStyleSheet("color: gray; font-size: 11px;")
        self._reader = PackagesReader(
            device=self._device, adb_exe=self._adb_exe, parent=self
        )
        self._reader.packages_ready.connect(self._on_packages_ready)
        self._reader.error_occurred.connect(self._on_error)
        self._reader.finished.connect(self._on_reader_done)
        self._reader.start()

    def _on_reader_done(self) -> None:
        self._btn_refresh.setEnabled(True)
        self._btn_refresh.setText("Refresh")

    def _on_packages_ready(self, pkgs: List[str]) -> None:
        self._packages = pkgs
        self._populate(pkgs)

    def _on_error(self, msg: str) -> None:
        self._lbl_status.setText(f"Error: {msg}")
        self._lbl_status.setStyleSheet("color: #C62828; font-size: 11px;")

    # ================================================================== populate
    def _populate(self, pkgs: List[str]) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(pkgs))
        for row, pkg in enumerate(pkgs):
            self._table.setItem(row, COL_PACKAGE, _ro(pkg))
        self._table.setSortingEnabled(True)
        self._apply_filter()

        ts = datetime.now().strftime("%H:%M:%S")
        self._lbl_status.setText(f"Last refresh: {ts}   |   {len(pkgs):,} packages")
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
            item = self._table.item(row, COL_PACKAGE)
            show = rx is None or bool(rx.search(item.text() if item else ""))
            self._table.setRowHidden(row, not show)
            if show:
                visible += 1

        self._filter_lbl.setText(f"{visible}/{total}" if rx is not None else "")

    # ================================================================== selection
    def _selected_package(self) -> Optional[str]:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self._table.item(rows[0].row(), COL_PACKAGE)
        return item.text() if item else None

    def _update_button_state(self) -> None:
        has = self._selected_package() is not None
        self._btn_uninstall.setEnabled(has)
        self._btn_clear_cache.setEnabled(has)
        self._btn_force_stop.setEnabled(has)
        self._btn_info.setEnabled(has)

    # ================================================================== actions
    def _run_adb(self, args: List[str]) -> tuple[int, str, str]:
        cmd = [self._adb_exe]
        if self._device:
            cmd += ["-s", self._device]
        cmd += args
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except Exception as exc:
            return -1, "", str(exc)

    def _report(self, action: str, pkg: str, rc: int, out: str, err: str) -> None:
        msg = out or err or ("OK" if rc == 0 else "(no output)")
        if rc == 0:
            self._lbl_status.setText(f"{action} {pkg}: {msg}")
            self._lbl_status.setStyleSheet("color: gray; font-size: 11px;")
        else:
            self._lbl_status.setText(f"{action} {pkg} failed: {msg}")
            self._lbl_status.setStyleSheet("color: #C62828; font-size: 11px;")

    def _on_uninstall(self) -> None:
        pkg = self._selected_package()
        if not pkg:
            return
        reply = QMessageBox.question(
            self,
            "Uninstall package",
            f"Uninstall {pkg} from the device?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        rc, out, err = self._run_adb(["shell", "pm", "uninstall", pkg])
        self._report("Uninstall", pkg, rc, out, err)
        if rc == 0:
            self._start_refresh()

    def _on_clear_cache(self) -> None:
        pkg = self._selected_package()
        if not pkg:
            return
        rc, out, err = self._run_adb(["shell", "pm", "clear", "-a", pkg])
        self._report("Clear cache", pkg, rc, out, err)

    def _on_force_stop(self) -> None:
        pkg = self._selected_package()
        if not pkg:
            return
        rc, out, err = self._run_adb(["shell", "am", "force-stop", "all", pkg])
        self._report("Force stop", pkg, rc, out, err)

    def _on_info(self) -> None:
        pkg = self._selected_package()
        if not pkg:
            return
        rc, out, err = self._run_adb([
            "shell", "am", "start",
            "-a", "android.settings.APPLICATION_DETAILS_SETTINGS",
            "-d", f"package:{pkg}",
        ])
        self._report("Info", pkg, rc, out, err)
