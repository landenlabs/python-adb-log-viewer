from __future__ import annotations

import subprocess
from typing import List, Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .app_settings import (
    BUFFER_INFO,
    EXCLUDE_FIELDS,
    MAX_RECORDS_MAX,
    MAX_RECORDS_MIN,
    STATS_TOP_N_MAX,
    STATS_TOP_N_MIN,
    AppSettings,
    ExcludeRule,
    _settings_path,
)
from .colors_dialog import CollapsibleBox


class FlowLayout(QLayout):
    """Lays out child widgets left-to-right and wraps to a new row when the
    available width runs out. Used so the Appearance section's checkboxes
    reflow instead of clipping when the dialog is narrow."""

    def __init__(self, parent: QWidget | None = None, hspacing: int = 12, vspacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list = []
        self._hspace = hspacing
        self._vspace = vspacing
        if parent is not None:
            self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            wid = item.sizeHint()
            next_x = x + wid.width() + self._hspace
            if next_x - self._hspace > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + self._vspace
                next_x = x + wid.width() + self._hspace
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), wid))
            x = next_x
            line_height = max(line_height, wid.height())
        return y + line_height - rect.y() + m.bottom()


class SettingsDialog(QDialog):
    """
    Non-modal settings dialog with auto-save semantics.

    Every input change is immediately written to AppSettings, persisted to
    settings.json, and broadcast via settings_applied — no Apply / OK
    button is required. The Close button simply hides the dialog. A final
    auto-save runs in closeEvent as a safety net for in-progress edits
    (e.g. an open table editor that hasn't lost focus yet).
    """

    settings_applied = Signal()
    # Emitted as soon as the user picks a theme in the combo, so the main
    # window can apply the new stylesheet without going through the
    # heavier settings_applied path.
    theme_changed = Signal(str)

    def __init__(
        self,
        settings: AppSettings,
        device: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(760, 680)
        self.setModal(False)
        self._settings = settings
        self._device = device
        # Suppress _auto_save while _load() populates widgets — otherwise
        # every setText / setChecked / setValue would round-trip the value
        # back through save() during the very first paint.
        self._suppress_autosave = False
        self._build_ui()
        self._load()
        self._wire_autosave()

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
        scroll_layout.addWidget(self._build_adb_group())

        self._buffers_box = CollapsibleBox("ADB Log Buffers")
        self._buffers_box.add_widget(self._build_buffers_content())
        scroll_layout.addWidget(self._buffers_box)

        self._rules_box = CollapsibleBox(
            "Exclusion Rules  –  matching rows are hidden from the log view"
        )
        self._rules_box.add_widget(self._build_rules_content())
        scroll_layout.addWidget(self._rules_box)

        self._limits_box = CollapsibleBox("Limits")
        self._limits_box.add_widget(self._build_limits_content())
        self._limits_box.toggle()  # start collapsed
        scroll_layout.addWidget(self._limits_box)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        root.addWidget(scroll)

        # Auto-save semantics — no Apply / OK. Close just hides the window;
        # every edit was already persisted by _auto_save.
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.hide)

        # Left-aligned helper button: opens the settings.json's folder in the
        # platform file manager (Finder / Explorer / xdg).
        btn_row = QHBoxLayout()
        self._btn_open_settings_dir = QPushButton("Open Settings Folder")
        self._btn_open_settings_dir.setToolTip(
            f"Reveal the settings folder:\n{_settings_path().parent}"
        )
        self._btn_open_settings_dir.clicked.connect(self._open_settings_folder)
        btn_row.addWidget(self._btn_open_settings_dir)
        btn_row.addStretch()
        btn_row.addWidget(btns)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------ appearance

    def _build_appearance_group(self) -> QWidget:
        grp = QGroupBox("Appearance")
        layout = FlowLayout(grp, hspacing=16, vspacing=6)

        # Theme label + combo travel together so they don't wrap apart.
        theme_box = QWidget()
        theme_row = QHBoxLayout(theme_box)
        theme_row.setContentsMargins(0, 0, 0, 0)
        theme_row.setSpacing(6)
        theme_row.addWidget(QLabel("Theme:"))
        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["Light", "Dark"])
        self._theme_combo.currentTextChanged.connect(self._on_theme_combo_changed)
        theme_row.addWidget(self._theme_combo)
        layout.addWidget(theme_box)

        self._merge_cb = QCheckBox("Merge same-time + tag messages")
        self._merge_cb.setToolTip(
            "Merge consecutive log entries that share the same timestamp (second) and tag\n"
            "into a single row.  Double-click the row to expand its lines in place."
        )
        layout.addWidget(self._merge_cb)

        self._timeline_filter_cb = QCheckBox("Timeline follows filter")
        self._timeline_filter_cb.setToolTip(
            "When level, tag, or text filters are active, the timeline shows\n"
            "only bars for records that pass those filters.\n"
            "Clearing all filters restores the full timeline."
        )
        layout.addWidget(self._timeline_filter_cb)

        self._compact_rows_cb = QCheckBox("Compact rows")
        self._compact_rows_cb.setToolTip(
            "Tighter vertical spacing in the log table for more rows on screen."
        )
        layout.addWidget(self._compact_rows_cb)

        self._wrap_messages_cb = QCheckBox("Wrap long messages")
        self._wrap_messages_cb.setToolTip(
            "When on, long message lines wrap to fit the viewer width and\n"
            "the row grows to show all wrapped lines (no horizontal scroll).\n"
            "When off, messages stay on one line and a horizontal scroll bar\n"
            "appears as needed."
        )
        layout.addWidget(self._wrap_messages_cb)
        return grp

    # ------------------------------------------------------------------ adb executable

    def _build_adb_group(self) -> QWidget:
        from .adb_reader import default_adb_exe
        grp = QGroupBox("ADB Executable")
        layout = QVBoxLayout(grp)
        layout.setSpacing(6)

        # Error banner — hidden until show_adb_error() is called or path check fails
        self._adb_error_banner = QLabel()
        self._adb_error_banner.setWordWrap(True)
        self._adb_error_banner.setStyleSheet(
            "background-color: #B71C1C; color: #FFFFFF;"
            "padding: 6px 10px; border-radius: 4px; font-weight: bold;"
        )
        self._adb_error_banner.setVisible(False)
        layout.addWidget(self._adb_error_banner)

        # Path row
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Path:"))
        self._adb_path_edit = QLineEdit()
        self._adb_path_edit.setPlaceholderText(
            f"Leave empty to use default ({default_adb_exe()})"
        )
        self._adb_path_edit.setToolTip(
            "Full path to the adb executable, or leave empty to search PATH.\n"
            "Example: /usr/local/bin/adb  or  C:\\platform-tools\\adb.exe"
        )
        self._adb_path_edit.textChanged.connect(self._on_adb_path_changed)
        path_row.addWidget(self._adb_path_edit, stretch=1)

        self._adb_browse_btn = QPushButton("Browse…")
        self._adb_browse_btn.setToolTip("Pick the adb executable from the filesystem")
        self._adb_browse_btn.clicked.connect(self._on_browse_adb)
        path_row.addWidget(self._adb_browse_btn)

        self._adb_test_btn = QPushButton("Test")
        self._adb_test_btn.setToolTip("Run 'adb version' to verify the executable works")
        self._adb_test_btn.clicked.connect(self._on_test_adb)
        path_row.addWidget(self._adb_test_btn)

        layout.addLayout(path_row)

        # Status row
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self._adb_status_lbl = QLabel("—")
        self._adb_status_lbl.setStyleSheet("font-size: 11px;")
        status_row.addWidget(self._adb_status_lbl, stretch=1)
        layout.addLayout(status_row)

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

        scope_hint = QLabel(
            "Global excludes — combined with the per-profile list from the "
            "Colors dialog when filtering."
        )
        scope_hint.setProperty("hint", True)
        scope_hint.setContentsMargins(0, 0, 0, 4)
        scope_hint.setWordWrap(True)
        layout.addWidget(scope_hint)

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

    # ------------------------------------------------------------------ limits

    def _build_limits_content(self) -> QWidget:
        container = QWidget()
        form = QFormLayout(container)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        self._stats_top_n_spin = QSpinBox()
        self._stats_top_n_spin.setRange(STATS_TOP_N_MIN, STATS_TOP_N_MAX)
        self._stats_top_n_spin.setSingleStep(10)
        self._stats_top_n_spin.setToolTip(
            f"Maximum number of PIDs / Tags shown in the Log Statistics dialog.\n"
            f"Range: {STATS_TOP_N_MIN}–{STATS_TOP_N_MAX}."
        )
        form.addRow("Statistics top N:", self._stats_top_n_spin)

        self._max_records_spin = QSpinBox()
        self._max_records_spin.setRange(MAX_RECORDS_MIN, MAX_RECORDS_MAX)
        self._max_records_spin.setSingleStep(1000)
        self._max_records_spin.setGroupSeparatorShown(True)
        self._max_records_spin.setToolTip(
            f"Maximum log records kept in memory / database before the\n"
            f"oldest entries are pruned. Range: {MAX_RECORDS_MIN:,}–{MAX_RECORDS_MAX:,}."
        )
        form.addRow("Main scroller limit:", self._max_records_spin)

        return container

    # ================================================================== load / save

    def _load(self) -> None:
        self._suppress_autosave = True
        try:
            self._theme_combo.blockSignals(True)
            self._theme_combo.setCurrentText(self._settings.theme.capitalize())
            self._theme_combo.blockSignals(False)
            self._merge_cb.setChecked(self._settings.merge_same_time_tag)
            self._timeline_filter_cb.setChecked(self._settings.timeline_follows_filter)
            self._compact_rows_cb.setChecked(self._settings.compact_rows)
            self._wrap_messages_cb.setChecked(self._settings.wrap_messages)

            # ADB path — block signal so _on_adb_path_changed fires once at the end
            self._adb_path_edit.blockSignals(True)
            self._adb_path_edit.setText(self._settings.adb_path)
            self._adb_path_edit.blockSignals(False)
            self._on_adb_path_changed(self._settings.adb_path)

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

            self._stats_top_n_spin.setValue(self._settings.stats_top_n)
            self._max_records_spin.setValue(self._settings.max_records)
        finally:
            self._suppress_autosave = False

        self._update_command_preview()

    # ================================================================== auto-save

    def _wire_autosave(self) -> None:
        """Connect every user-edit signal to _auto_save. Programmatic
        population in _load() is protected by self._suppress_autosave."""
        # Theme is handled by _on_theme_combo_changed (fast path + save)
        # — don't double-wire it here.
        self._merge_cb.toggled.connect(self._auto_save)
        self._timeline_filter_cb.toggled.connect(self._auto_save)
        self._compact_rows_cb.toggled.connect(self._auto_save)
        self._wrap_messages_cb.toggled.connect(self._auto_save)
        # ADB path: editingFinished, not textChanged — saving on every
        # keystroke would thrash settings.json (and validation already
        # runs live via _on_adb_path_changed).
        self._adb_path_edit.editingFinished.connect(self._auto_save)
        for cb in self._buffer_cbs.values():
            cb.toggled.connect(self._auto_save)
        self._rules_table.itemChanged.connect(self._auto_save)
        # Add / Delete don't go through itemChanged; wire them directly.
        self._btn_add.clicked.connect(self._auto_save)
        self._btn_delete.clicked.connect(self._auto_save)
        self._stats_top_n_spin.valueChanged.connect(self._auto_save)
        self._max_records_spin.valueChanged.connect(self._auto_save)

    def _auto_save(self, *_args) -> None:
        """Snapshot all widget values into AppSettings, write settings.json,
        and notify MainWindow to re-apply. Called from every input's change
        signal — cheap enough to run unconditionally."""
        if self._suppress_autosave:
            return
        self._settings.theme = self._theme_combo.currentText().lower()
        self._settings.merge_same_time_tag = self._merge_cb.isChecked()
        self._settings.timeline_follows_filter = self._timeline_filter_cb.isChecked()
        self._settings.compact_rows = self._compact_rows_cb.isChecked()
        self._settings.wrap_messages = self._wrap_messages_cb.isChecked()
        self._settings.adb_path = self._adb_path_edit.text().strip()
        chosen = {name for name, cb in self._buffer_cbs.items() if cb.isChecked()}
        self._settings.buffers = chosen if chosen else {"main"}
        self._settings.exclude_rules = self._collect_rules()
        self._settings.stats_top_n = self._stats_top_n_spin.value()
        self._settings.max_records = self._max_records_spin.value()
        self._settings.save()
        self._adb_error_banner.setVisible(False)
        self.settings_applied.emit()

    def closeEvent(self, event) -> None:
        # Safety net: an in-progress table editor or a focused QLineEdit
        # may not have fired its commit signal yet when the user clicks
        # the window's [X]. Do one last sweep so nothing slips away.
        self._auto_save()
        super().closeEvent(event)

    def _open_settings_folder(self) -> None:
        """Reveal the directory that holds settings.json in the OS file manager."""
        folder = _settings_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _on_theme_combo_changed(self, text: str) -> None:
        """Apply theme the moment the user picks it — and persist, so closing
        with Cancel still keeps the new theme. The user asked for "immediate"
        and intuitively expects the visible choice to stick."""
        theme = text.lower()
        if theme == self._settings.theme:
            return
        self._settings.theme = theme
        self._settings.save()
        self.theme_changed.emit(theme)

    def set_device(self, device: Optional[str]) -> None:
        self._device = device
        self._update_command_preview()

    # ================================================================== adb helpers

    def _on_adb_path_changed(self, text: str = "") -> None:
        from .adb_reader import check_adb
        ok, msg = check_adb(text)
        if ok:
            self._adb_status_lbl.setText(f"✓  {msg}")
            self._adb_status_lbl.setStyleSheet("color: green; font-size: 11px;")
            # Auto-hide the error banner once the path resolves successfully
            self._adb_error_banner.setVisible(False)
        else:
            self._adb_status_lbl.setText(f"✗  {msg}")
            self._adb_status_lbl.setStyleSheet("color: #C62828; font-size: 11px;")
        self._update_command_preview()

    def _on_browse_adb(self) -> None:
        import os
        start_dir = ""
        current = self._adb_path_edit.text().strip()
        if current and os.path.isabs(current):
            start_dir = os.path.dirname(current)

        if os.name == "nt":
            file_filter = "Executables (*.exe);;All files (*)"
        else:
            file_filter = "All files (*)"

        path, _ = QFileDialog.getOpenFileName(
            self, "Select adb executable", start_dir, file_filter
        )
        if path:
            self._adb_path_edit.setText(path)

    def _on_test_adb(self) -> None:
        from .adb_reader import NO_WINDOW_FLAGS, resolve_adb
        exe = resolve_adb(self._adb_path_edit.text())
        try:
            result = subprocess.run(
                [exe, "version"],
                capture_output=True,
                text=True,
                timeout=5,
                **NO_WINDOW_FLAGS,
            )
            output = (result.stdout or result.stderr or "").strip()
            first_line = output.splitlines()[0] if output else "(no output)"
            self._adb_status_lbl.setText(f"✓  {first_line}")
            self._adb_status_lbl.setStyleSheet("color: green; font-size: 11px;")
            self._adb_error_banner.setVisible(False)
        except FileNotFoundError:
            self._adb_status_lbl.setText(f"✗  '{exe}' not found")
            self._adb_status_lbl.setStyleSheet("color: #C62828; font-size: 11px;")
        except subprocess.TimeoutExpired:
            self._adb_status_lbl.setText(f"✗  '{exe}' timed out")
            self._adb_status_lbl.setStyleSheet("color: #C62828; font-size: 11px;")
        except Exception as exc:
            self._adb_status_lbl.setText(f"✗  {exc}")
            self._adb_status_lbl.setStyleSheet("color: #C62828; font-size: 11px;")

    def show_adb_error(self, message: str) -> None:
        """Show a prominent error banner and bring the dialog to the front.
        Called from MainWindow when adb cannot be found at startup."""
        self._adb_error_banner.setText(f"⚠  ADB not found — {message}")
        self._adb_error_banner.setVisible(True)
        self.show()
        self.raise_()
        self.activateWindow()

    # ================================================================== row helpers

    def _add_row(self, pattern: str = "", field: str = "TAG", enabled: bool = True) -> None:
        from .colors_dialog import _make_cell_checkbox
        row = self._rules_table.rowCount()
        self._rules_table.insertRow(row)

        # Col 0: enabled — real QCheckBox (item check states render as black
        # squares under the dark stylesheet).
        self._rules_table.setCellWidget(row, 0, _make_cell_checkbox(enabled))

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
        from .colors_dialog import _cell_checked
        rules: List[ExcludeRule] = []
        for row in range(self._rules_table.rowCount()):
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
                enabled=_cell_checked(self._rules_table.cellWidget(row, 0)),
            ))
        return rules

    # ================================================================== command preview

    def _update_command_preview(self) -> None:
        from .adb_reader import build_adb_command, resolve_adb
        buffers = {name for name, cb in self._buffer_cbs.items() if cb.isChecked()} or {"main"}
        rules = self._collect_rules()
        exe = resolve_adb(self._adb_path_edit.text())
        cmd = build_adb_command(self._device, buffers, rules, adb_exe=exe)
        self._cmd_preview.setText(" ".join(cmd))
