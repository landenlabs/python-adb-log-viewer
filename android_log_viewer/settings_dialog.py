from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .app_settings import BUFFER_INFO, EXCLUDE_FIELDS, AppSettings, ExcludeRule
from .colors_dialog import CollapsibleBox


class SettingsDialog(QDialog):
    """
    Non-modal settings dialog.
    Writes to AppSettings in-place and emits settings_applied on OK.
    Cancel / close simply hides the window without saving.
    """

    settings_applied = Signal()

    def __init__(
        self,
        settings: AppSettings,
        device: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(760, 640)
        self.setModal(False)
        self._settings = settings
        self._device = device
        self._build_ui()
        self._load()

    # ================================================================== build

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(10)

        scroll_layout.addWidget(self._build_appearance_group())

        self._buffers_box = CollapsibleBox("ADB Log Buffers")
        self._buffers_box.add_widget(self._build_buffers_content())
        scroll_layout.addWidget(self._buffers_box)

        self._rules_box = CollapsibleBox(
            "Exclusion Rules  –  matching rows are hidden from the log view"
        )
        self._rules_box.add_widget(self._build_rules_content())
        scroll_layout.addWidget(self._rules_box)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        root.addWidget(scroll)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.hide)
        root.addWidget(btns)

    # ------------------------------------------------------------------ appearance

    def _build_appearance_group(self) -> QWidget:
        grp = QGroupBox("Appearance")
        layout = QHBoxLayout(grp)
        layout.addWidget(QLabel("Theme:"))
        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["Light", "Dark"])
        layout.addWidget(self._theme_combo)
        layout.addSpacing(16)
        self._merge_cb = QCheckBox("Merge same-time + tag messages")
        self._merge_cb.setToolTip(
            "Merge consecutive log entries that share the same timestamp (second) and tag\n"
            "into a single row.  Double-click the row to expand its lines in place."
        )
        layout.addWidget(self._merge_cb)
        layout.addSpacing(16)
        self._timeline_filter_cb = QCheckBox("Timeline follows filter")
        self._timeline_filter_cb.setToolTip(
            "When level, tag, or text filters are active, the timeline shows\n"
            "only bars for records that pass those filters.\n"
            "Clearing all filters restores the full timeline."
        )
        layout.addWidget(self._timeline_filter_cb)
        layout.addStretch()
        return grp

    # ------------------------------------------------------------------ buffers

    def _build_buffers_content(self) -> QWidget:
        container = QWidget()
        layout = QGridLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setColumnStretch(1, 1)
        layout.setHorizontalSpacing(10)

        self._buffer_cbs: dict[str, QCheckBox] = {}
        for row, (name, desc) in enumerate(BUFFER_INFO):
            cb = QCheckBox(name)
            cb.setToolTip(desc)
            cb.toggled.connect(self._update_command_preview)
            self._buffer_cbs[name] = cb
            layout.addWidget(cb, row, 0)

            lbl = QLabel(desc)
            lbl.setStyleSheet("font-size: 11px;")
            layout.addWidget(lbl, row, 1)

        note = QLabel("  ⚠  Buffer changes take effect on the next Connect.")
        note.setStyleSheet("font-style: italic; padding-top: 4px;")
        layout.addWidget(note, len(BUFFER_INFO), 0, 1, 2)

        return container

    # ------------------------------------------------------------------ rules

    def _build_rules_content(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self._rules_table = QTableWidget()
        self._rules_table.setColumnCount(3)
        self._rules_table.setHorizontalHeaderLabels(["On", "Pattern  (regex, case-insensitive)", "Apply To"])
        self._rules_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._rules_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._rules_table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self._rules_table.verticalHeader().setVisible(False)
        self._rules_table.verticalHeader().setDefaultSectionSize(26)
        self._rules_table.setShowGrid(True)
        self._rules_table.setSortingEnabled(False)
        self._rules_table.setAlternatingRowColors(True)
        self._rules_table.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        self._rules_table.itemChanged.connect(self._update_command_preview)

        hh = self._rules_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        self._rules_table.setColumnWidth(0, 36)
        self._rules_table.setColumnWidth(2, 115)

        layout.addWidget(self._rules_table)

        btn_row = QHBoxLayout()
        self._btn_add = QPushButton("+ Add Rule")
        self._btn_add.setToolTip("Append a new blank exclusion rule")
        self._btn_delete = QPushButton("Delete Selected")
        self._btn_delete.setToolTip("Remove the selected rule(s)")
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_delete)
        btn_row.addStretch()

        hint = QLabel("Tip: double-click a Pattern cell to edit it.")
        hint.setStyleSheet("font-size: 10px;")
        btn_row.addWidget(hint)

        layout.addLayout(btn_row)

        self._btn_add.clicked.connect(lambda: self._add_row())
        self._btn_delete.clicked.connect(self._delete_selected)

        # ---- ADB command preview ----
        preview_row = QHBoxLayout()
        preview_lbl = QLabel("ADB command:")
        preview_lbl.setStyleSheet("font-size: 11px;")
        preview_row.addWidget(preview_lbl)

        self._cmd_preview = QLineEdit()
        self._cmd_preview.setReadOnly(True)
        self._cmd_preview.setFont(QFont("Courier New", 9))
        self._cmd_preview.setToolTip(
            "The exact adb logcat command that will run on Connect.\n"
            "Simple literal TAG rules become TAG:S filters here; "
            "regex/PID/MESSAGE rules are applied client-side."
        )
        preview_row.addWidget(self._cmd_preview, stretch=1)

        layout.addLayout(preview_row)

        return container

    # ================================================================== load / save

    def _load(self) -> None:
        self._theme_combo.setCurrentText(self._settings.theme.capitalize())
        self._merge_cb.setChecked(self._settings.merge_same_time_tag)
        self._timeline_filter_cb.setChecked(self._settings.timeline_follows_filter)
        # Block signals during rebuild to avoid spurious _update_command_preview calls
        for name, cb in self._buffer_cbs.items():
            cb.blockSignals(True)
            cb.setChecked(name in self._settings.buffers)
            cb.blockSignals(False)

        self._rules_table.blockSignals(True)
        self._rules_table.setRowCount(0)
        for rule in self._settings.exclude_rules:
            self._add_row(rule.pattern, rule.field, rule.enabled)
        self._rules_table.blockSignals(False)

        self._update_command_preview()

    def _on_accept(self) -> None:
        self._settings.theme = self._theme_combo.currentText().lower()
        self._settings.merge_same_time_tag = self._merge_cb.isChecked()
        self._settings.timeline_follows_filter = self._timeline_filter_cb.isChecked()
        chosen = {name for name, cb in self._buffer_cbs.items() if cb.isChecked()}
        self._settings.buffers = chosen if chosen else {"main"}
        self._settings.exclude_rules = self._collect_rules()
        self.settings_applied.emit()
        self.hide()

    def set_device(self, device: Optional[str]) -> None:
        self._device = device
        self._update_command_preview()

    # ================================================================== row helpers

    def _add_row(self, pattern: str = "", field: str = "TAG", enabled: bool = True) -> None:
        row = self._rules_table.rowCount()
        self._rules_table.insertRow(row)

        # Col 0: enabled checkbox (checkable item, no text)
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
        chk.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
        chk.setTextAlignment(Qt.AlignCenter)
        self._rules_table.setItem(row, 0, chk)

        # Col 1: pattern — editable text
        pat = QTableWidgetItem(pattern)
        self._rules_table.setItem(row, 1, pat)

        # Col 2: field selector — QComboBox widget
        combo = QComboBox()
        combo.addItems(list(EXCLUDE_FIELDS))
        combo.setCurrentText(field if field in EXCLUDE_FIELDS else "TAG")
        combo.setFrame(False)
        combo.currentTextChanged.connect(self._update_command_preview)
        self._rules_table.setCellWidget(row, 2, combo)

        # Scroll to the new row and start editing the pattern immediately
        self._rules_table.scrollToBottom()
        if not pattern:
            self._rules_table.editItem(self._rules_table.item(row, 1))

    def _delete_selected(self) -> None:
        rows = sorted(
            {idx.row() for idx in self._rules_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self._rules_table.removeRow(row)
        self._update_command_preview()

    def _collect_rules(self) -> List[ExcludeRule]:
        rules: List[ExcludeRule] = []
        for row in range(self._rules_table.rowCount()):
            chk = self._rules_table.item(row, 0)
            pat = self._rules_table.item(row, 1)
            combo: Optional[QComboBox] = self._rules_table.cellWidget(row, 2)
            if pat is None or combo is None:
                continue
            text = pat.text().strip()
            if not text:
                continue
            rules.append(ExcludeRule(
                pattern=text,
                field=combo.currentText(),
                enabled=(chk.checkState() == Qt.Checked) if chk else True,
            ))
        return rules

    # ================================================================== command preview

    def _update_command_preview(self) -> None:
        from .adb_reader import build_adb_command
        buffers = {name for name, cb in self._buffer_cbs.items() if cb.isChecked()} or {"main"}
        rules = self._collect_rules()
        cmd = build_adb_command(self._device, buffers, rules)
        self._cmd_preview.setText(" ".join(cmd))
