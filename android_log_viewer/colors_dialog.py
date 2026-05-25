from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QCheckBox,
    QColorDialog,
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
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .app_settings import (
    EXCLUDE_FIELDS,
    PROFILE_SCHEMA_VERSION,
    AppSettings,
    ExcludeRule,
    _migrate_profile,
    _profiles_dir,
)
from .color_rules import COLOR_RULE_FIELDS, ColorRule

_LEVEL_LABELS = {
    "V": "V  Verbose",
    "D": "D  Debug",
    "I": "I  Info",
    "W": "W  Warning",
    "E": "E  Error",
    "F": "F  Fatal",
    "B": "B  Bookmark",
}


class ColorButton(QPushButton):
    """Button that displays a color swatch and opens QColorDialog on click."""

    color_changed = Signal(str)

    def __init__(self, hex_color: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color = hex_color
        self.setFixedSize(80, 22)
        self._refresh_style()
        self.clicked.connect(self._pick_color)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)

    # ------------------------------------------------------------------ public

    def color(self) -> str:
        return self._color

    def set_color(self, hex_color: str) -> None:
        self._color = hex_color
        self._refresh_style()

    # ------------------------------------------------------------------ private

    def _refresh_style(self) -> None:
        if self._color:
            c = QColor(self._color)
            lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
            text_col = "#000000" if lum > 128 else "#FFFFFF"
            self.setStyleSheet(
                f"QPushButton {{ background-color: {self._color}; color: {text_col};"
                f"  border: 1px solid #888; border-radius: 2px; font-size: 10px; }}"
                f"QPushButton:hover {{ border: 1px solid #333; }}"
            )
            self.setText(self._color)
        else:
            self.setStyleSheet(
                "QPushButton { background-color: #f0f0f0; color: #999;"
                "  border: 1px solid #ccc; border-radius: 2px; font-size: 10px; }"
                "QPushButton:hover { border: 1px solid #888; }"
            )
            self.setText("(none)")

    def _pick_color(self) -> None:
        initial = QColor(self._color) if self._color else QColor("#FFFFFF")
        color = QColorDialog.getColor(initial, self, "Choose Color")
        if color.isValid():
            self._color = color.name()
            self._refresh_style()
            self.color_changed.emit(self._color)

    def _context_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction("Clear (no override)", self._clear)
        menu.exec(self.mapToGlobal(pos))

    def _clear(self) -> None:
        self._color = ""
        self._refresh_style()
        self.color_changed.emit("")


def _make_cell_checkbox(checked: bool, tooltip: str = "") -> QWidget:
    """Real QCheckBox centered in a wrapper widget. Item-style check states
    (setCheckState on QTableWidgetItem) render as featureless squares under
    the dark stylesheet — using a real checkbox picks up theme styling."""
    wrap = QWidget()
    layout = QHBoxLayout(wrap)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setAlignment(Qt.AlignCenter)
    cb = QCheckBox()
    cb.setChecked(checked)
    if tooltip:
        cb.setToolTip(tooltip)
        wrap.setToolTip(tooltip)
    layout.addWidget(cb)
    return wrap


def _cell_checked(wrap: Optional[QWidget]) -> bool:
    if wrap is None:
        return False
    cb = wrap.findChild(QCheckBox)
    return bool(cb and cb.isChecked())


class _ReorderableTable(QTableWidget):
    """QTableWidget that supports drag-and-drop row reordering.
    Emits row_moved(src, dest) so the host can rebuild rows — Qt's built-in
    InternalMove drops cell widgets (combos, color buttons, checkboxes)
    behind in their original positions, so we don't let it touch the data."""

    row_moved = Signal(int, int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDragDropOverwriteMode(False)
        self.setDropIndicatorShown(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        # Multi-row drag would need batched moves; keep it single-row for now.
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.verticalHeader().setSectionsMovable(False)
        # Number of rows pinned at the top that cannot be dragged or
        # dropped onto. The host (ColorsDialog) sets this when it inserts
        # the "Search highlight" row.
        self.pinned_top = 0

    def dropEvent(self, event) -> None:
        if event.source() is not self:
            super().dropEvent(event)
            return
        sel = self.selectionModel().selectedRows()
        if not sel:
            event.ignore()
            return
        src_row = sel[0].row()
        if src_row < self.pinned_top:
            event.ignore()
            return

        # Resolve drop position to a target row index.
        pos = event.position().toPoint()
        idx = self.indexAt(pos)
        if idx.isValid():
            rect = self.visualRect(idx)
            dest_row = idx.row()
            if pos.y() > rect.top() + rect.height() / 2:
                dest_row += 1
        else:
            dest_row = self.rowCount()

        # Removing the source row shifts everything below it up by one;
        # adjust the destination accordingly so the user's visual intent
        # is preserved.
        if dest_row > src_row:
            dest_row -= 1
        # Never let a draggable row land in (or above) the pinned area.
        dest_row = max(self.pinned_top, dest_row)

        # Accept the event so Qt doesn't also try to perform its own move
        # (which would orphan our cell widgets), but only emit the signal
        # if the row actually changes position.
        event.setDropAction(Qt.IgnoreAction)
        event.accept()
        if dest_row != src_row and 0 <= dest_row <= self.rowCount() - 1:
            self.row_moved.emit(src_row, dest_row)


class CollapsibleBox(QWidget):
    """A simple collapsible container with a title and a toggle button."""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._is_expanded = True
        self._title = title

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self._header = QPushButton()
        self._header.setObjectName("collapsible_header")
        self._header.setCheckable(True)
        self._header.setChecked(True)
        self._header.clicked.connect(self.toggle)
        self._layout.addWidget(self._header)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(4, 8, 4, 8)
        self._layout.addWidget(self._content)

        self._refresh_header()

    def toggle(self) -> None:
        self._is_expanded = not self._is_expanded
        self._content.setVisible(self._is_expanded)
        self._content.setMaximumHeight(16777215 if self._is_expanded else 0)
        self._refresh_header()

    def set_title(self, title: str) -> None:
        self._title = title
        self._refresh_header()

    def _refresh_header(self) -> None:
        arrow = "▼" if self._is_expanded else "▶"
        self._header.setText(f"{arrow}  {self._title}")

    def add_widget(self, widget: QWidget) -> None:
        self._content_layout.addWidget(widget)


class ColorsDialog(QDialog):
    """
    Non-modal Colors dialog.

    Level Colors section: configure fg/bg for all log levels.
    Color Rules section:  regex-based row or text highlighting with save/load profiles.
    Exclusion Rules section: matching rows are hidden.
    """

    colors_applied = Signal()
    profile_dirty_changed = Signal(bool)

    def __init__(
        self,
        settings: AppSettings,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Colors & Rules")
        self.resize(850, 750)
        self.setModal(False)
        self._settings = settings
        # Refs to the pinned "Search highlight" row's color buttons (set when
        # _add_search_highlight_row runs during _load).
        self._search_fg_btn: Optional[ColorButton] = None
        self._search_bg_btn: Optional[ColorButton] = None
        # Skip _mark_dirty during programmatic widget population in _load() /
        # _load_profile() / row-rebuilds — itemChanged etc. fire for those too.
        self._suppress_dirty = False
        self._build_ui()
        self._load()
        # Render whatever dirty state was already on settings (e.g. a
        # right-click rule add before the dialog was first opened).
        self._update_profile_label()
        self._refresh_dirty_buttons()

    # ================================================================== build

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # Profile name banner — match the filter row's Tag/Text/Level
        # styling: theme-default font and color, no inline overrides. The
        # "Unsaved changes" tail in _update_profile_label is the only
        # element that escapes the theme (dark red, by design).
        self._profile_name_label = QLabel()
        self._profile_name_label.setObjectName("profile_name_label")
        self._profile_name_label.setTextFormat(Qt.RichText)
        root.addWidget(self._profile_name_label)

        # Scroll area for the collapsible sections
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll_content = QWidget()
        self._scroll_layout = QVBoxLayout(scroll_content)
        self._scroll_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll_layout.setSpacing(10)

        # 0. Startup Patterns — applied to the Tag/Text filter on app launch
        self._startup_box = CollapsibleBox("Startup Patterns")
        self._startup_box.add_widget(self._build_startup_group())
        self._scroll_layout.addWidget(self._startup_box)

        # 1. Level Colors
        self._level_box = CollapsibleBox("Log Level Colors")
        self._level_box.add_widget(self._build_level_group())
        self._scroll_layout.addWidget(self._level_box)

        # 2. Color Rules
        self._rules_box = CollapsibleBox("Color Rules")
        self._rules_box.add_widget(self._build_rules_group())
        self._scroll_layout.addWidget(self._rules_box)

        # 3. Exclusion Rules
        self._exclude_box = CollapsibleBox("Exclusion Rules")
        self._exclude_box.add_widget(self._build_exclude_group())
        self._scroll_layout.addWidget(self._exclude_box)

        self._scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        root.addWidget(scroll)

        # Bottom row: [ Load | Save ]   [ Apply | Close ]
        bottom_row = QHBoxLayout()
        
        self._btn_load_profile = QPushButton("Load Profile…")
        self._btn_save_profile = QPushButton("Save Profile…")
        # Save / Apply are styled by _refresh_dirty_buttons() — red+bold when
        # there are uncommitted edits, plain otherwise. Earlier the Save
        # button was unconditionally red; that's the bug being fixed here.
        bottom_row.addWidget(self._btn_load_profile)
        bottom_row.addWidget(self._btn_save_profile)

        bottom_row.addStretch()

        btns = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        self._btn_apply = btns.button(QDialogButtonBox.Apply)
        self._btn_apply.clicked.connect(self._on_apply)
        btns.button(QDialogButtonBox.Close).clicked.connect(self.hide)
        bottom_row.addWidget(btns)
        
        root.addLayout(bottom_row)

        self._btn_save_profile.clicked.connect(self._save_profile)
        self._btn_load_profile.clicked.connect(self._load_profile)

        self._install_dirty_hooks()

        # Keep the section titles in sync with table row counts ("Color
        # Rules [N]" / "Exclusion Rules [N]"). Model signals fire for every
        # path that adds or removes rows — Add/Delete buttons, _load(),
        # _load_profile(), drag-reorder — so no per-callsite updates are
        # needed.
        rules_model = self._rules_table.model()
        rules_model.rowsInserted.connect(self._update_rules_title)
        rules_model.rowsRemoved.connect(self._update_rules_title)
        exclude_model = self._exclude_table.model()
        exclude_model.rowsInserted.connect(self._update_exclude_title)
        exclude_model.rowsRemoved.connect(self._update_exclude_title)
        self._update_rules_title()
        self._update_exclude_title()

    def _update_rules_title(self) -> None:
        # Pinned "Search highlight" row isn't a user-defined rule.
        count = self._rules_table.rowCount() - self._rules_table.pinned_top
        self._rules_box.set_title(f"Color Rules [{count}]")

    def _update_exclude_title(self) -> None:
        self._exclude_box.set_title(
            f"Exclusion Rules [{self._exclude_table.rowCount()}]"
        )

    # ------------------------------------------------------------------ dirty wiring

    def _install_dirty_hooks(self) -> None:
        """Connect every user-edit signal that should flag the profile as
        having unsaved changes. Programmatic widget population in _load() is
        protected by self._suppress_dirty."""
        self._startup_tag_edit.textEdited.connect(self._mark_dirty)
        self._startup_text_edit.textEdited.connect(self._mark_dirty)

        for btn in self._level_fg_btns.values():
            btn.color_changed.connect(self._mark_dirty)
        for btn in self._level_bg_btns.values():
            btn.color_changed.connect(self._mark_dirty)

        # Tables: itemChanged catches pattern edits. Per-row cell widgets
        # (checkboxes / combos / color buttons) are wired in _add_rule_row
        # and _add_exclude_row below. Reorder + add/delete also dirty.
        self._rules_table.itemChanged.connect(self._mark_dirty)
        self._rules_table.row_moved.connect(self._mark_dirty)
        self._btn_add.clicked.connect(self._mark_dirty)
        self._btn_delete_rules.clicked.connect(self._mark_dirty)

        self._exclude_table.itemChanged.connect(self._mark_dirty)
        self._btn_add_ex.clicked.connect(self._mark_dirty)
        self._btn_delete_ex.clicked.connect(self._mark_dirty)

    # ------------------------------------------------------------------ startup patterns

    def _build_startup_group(self) -> QWidget:
        container = QWidget()
        form = QFormLayout(container)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(4)

        self._startup_tag_edit = QLineEdit()
        self._startup_tag_edit.setPlaceholderText("regex applied to Tag filter on startup")
        self._startup_tag_edit.setClearButtonEnabled(True)
        form.addRow("Tag:", self._startup_tag_edit)

        self._startup_text_edit = QLineEdit()
        self._startup_text_edit.setPlaceholderText("regex applied to Text filter on startup")
        self._startup_text_edit.setClearButtonEnabled(True)
        form.addRow("Text:", self._startup_text_edit)

        hint = QLabel(
            "Applied to the Tag / Text filter when the app launches. "
            "Saved with the color profile. CLI --tag / --text override these."
        )
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        form.addRow("", hint)

        return container

    # ------------------------------------------------------------------ level colors

    def _build_level_group(self) -> QWidget:
        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)

        self._level_fg_btns: dict[str, ColorButton] = {}
        self._level_bg_btns: dict[str, ColorButton] = {}

        grid.addWidget(QLabel("Level"), 0, 0)
        fg_hdr = QLabel("Foreground")
        fg_hdr.setAlignment(Qt.AlignCenter)
        grid.addWidget(fg_hdr, 0, 1)
        bg_hdr = QLabel("Background")
        bg_hdr.setAlignment(Qt.AlignCenter)
        grid.addWidget(bg_hdr, 0, 2)

        for row_i, (lvl, label) in enumerate(_LEVEL_LABELS.items(), start=1):
            lbl = QLabel(label)
            lbl.setMinimumWidth(100)
            grid.addWidget(lbl, row_i, 0)

            fg_btn = ColorButton()
            self._level_fg_btns[lvl] = fg_btn
            grid.addWidget(fg_btn, row_i, 1, Qt.AlignLeft)

            bg_btn = ColorButton()
            self._level_bg_btns[lvl] = bg_btn
            grid.addWidget(bg_btn, row_i, 2, Qt.AlignLeft)

        grid.setColumnStretch(3, 1)
        return container

    # ------------------------------------------------------------------ color rules

    def _build_rules_group(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        # Table (custom subclass so rules can be reordered via drag-drop;
        # rule priority is first-match-wins so order is meaningful)
        self._rules_table = _ReorderableTable()
        self._rules_table.setColumnCount(6)
        self._rules_table.setHorizontalHeaderLabels(
            ["On", "Pattern  (regex, case-insensitive)", "Field", "Foreground", "Background", "Row"]
        )
        self._rules_table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self._rules_table.setToolTip(
            "Color rules apply in order — the first match wins.\n"
            "Drag a row by its non-editable cells to change priority."
        )
        self._rules_table.row_moved.connect(self._on_rule_row_moved)
        self._rules_table.verticalHeader().setVisible(True)
        self._rules_table.verticalHeader().setDefaultSectionSize(28)
        self._rules_table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self._rules_table.verticalHeader().setFixedWidth(24)
        self._rules_table.setShowGrid(True)
        self._rules_table.setSortingEnabled(False)
        self._rules_table.setAlternatingRowColors(True)
        self._rules_table.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)

        hh = self._rules_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.Fixed)
        hh.setSectionResizeMode(4, QHeaderView.Fixed)
        hh.setSectionResizeMode(5, QHeaderView.Fixed)
        self._rules_table.setColumnWidth(0, 36)   # On
        self._rules_table.setColumnWidth(2, 100)  # Field
        self._rules_table.setColumnWidth(3, 90)   # FG
        self._rules_table.setColumnWidth(4, 90)   # BG
        self._rules_table.setColumnWidth(5, 50)   # Row

        layout.addWidget(self._rules_table)

        # Buttons row
        btn_row = QHBoxLayout()
        self._btn_add = QPushButton("+ Add Rule")
        self._btn_delete_rules = QPushButton("Delete Selected")
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_delete_rules)
        btn_row.addStretch()

        hint = QLabel(
            "Tip: double-click Pattern to edit.  Right-click a color button to clear it."
        )
        hint.setProperty("hint", True)
        layout.addLayout(btn_row)
        layout.addWidget(hint)

        self._btn_add.clicked.connect(lambda: self._add_rule_row())
        self._btn_delete_rules.clicked.connect(self._delete_selected_rules)

        return container

    def _build_exclude_group(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        scope_hint = QLabel(
            "Per-profile excludes — combined with the global Settings list "
            "when filtering."
        )
        scope_hint.setProperty("hint", True)
        scope_hint.setContentsMargins(0, 0, 0, 4)
        scope_hint.setWordWrap(True)
        layout.addWidget(scope_hint)

        self._exclude_table = QTableWidget()
        self._exclude_table.setColumnCount(3)
        self._exclude_table.setHorizontalHeaderLabels(["On", "Pattern  (regex)", "Apply To"])
        self._exclude_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._exclude_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._exclude_table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self._exclude_table.verticalHeader().setVisible(False)
        self._exclude_table.verticalHeader().setDefaultSectionSize(26)
        self._exclude_table.setShowGrid(True)
        self._exclude_table.setAlternatingRowColors(True)
        self._exclude_table.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)

        hh = self._exclude_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        self._exclude_table.setColumnWidth(0, 36)
        self._exclude_table.setColumnWidth(2, 115)

        layout.addWidget(self._exclude_table)

        btn_row = QHBoxLayout()
        self._btn_add_ex = QPushButton("+ Add Rule")
        self._btn_delete_ex = QPushButton("Delete Selected")
        btn_row.addWidget(self._btn_add_ex)
        btn_row.addWidget(self._btn_delete_ex)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._btn_add_ex.clicked.connect(lambda: self._add_exclude_row())
        self._btn_delete_ex.clicked.connect(self._delete_selected_excludes)

        return container

    # ================================================================== load / save

    def _update_profile_label(self) -> None:
        name = self._settings.last_profile_name or "(none)"
        # No inline color — let the theme paint it like Tag/Text/Level.
        base = f"Profile: {name}"
        if self._settings.profile_dirty:
            base += (
                "&nbsp;&nbsp;&nbsp;"
                "<span style='color:#B00020; font-weight:bold;'>"
                "Unsaved changes</span>"
            )
        self._profile_name_label.setText(base)

    # ------------------------------------------------------------------ dirty API

    def is_profile_dirty(self) -> bool:
        return self._settings.profile_dirty

    def set_profile_dirty(self, dirty: bool) -> None:
        """Update profile-dirty state, refresh the label and the Save/Apply
        button highlights, and notify listeners (MainWindow updates its
        title bar)."""
        if self._settings.profile_dirty == dirty:
            return
        self._settings.profile_dirty = dirty
        self._update_profile_label()
        self._refresh_dirty_buttons()
        self.profile_dirty_changed.emit(dirty)

    def _refresh_dirty_buttons(self) -> None:
        """Highlight Save Profile and Apply in dark-red bold when there are
        uncommitted edits; restore default styling when clean."""
        style = (
            "QPushButton { color: #B00020; font-weight: bold; }"
            if self._settings.profile_dirty else ""
        )
        self._btn_save_profile.setStyleSheet(style)
        self._btn_apply.setStyleSheet(style)

    def _mark_dirty(self, *_args) -> None:
        """Slot wired to every user-edit signal in the dialog. Ignored while
        _load() / row-rebuilds are populating widgets programmatically."""
        if self._suppress_dirty:
            return
        if not self._settings.profile_dirty:
            self.set_profile_dirty(True)

    def _load(self) -> None:
        self._suppress_dirty = True
        try:
            self._startup_tag_edit.setText(self._settings.startup_tag)
            self._startup_text_edit.setText(self._settings.startup_text)

            for lvl, btn in self._level_fg_btns.items():
                btn.set_color(self._settings.level_fg.get(lvl, ""))
            for lvl, btn in self._level_bg_btns.items():
                btn.set_color(self._settings.level_bg.get(lvl, ""))

            self._rules_table.setRowCount(0)
            self._add_search_highlight_row()
            for rule in self._settings.color_rules:
                self._add_rule_row(rule)

            self._exclude_table.setRowCount(0)
            for rule in self._settings.profile_exclude_rules:
                self._add_exclude_row(rule)
        finally:
            self._suppress_dirty = False

        # _load() just rebuilds the widgets to match current settings — it
        # does NOT change the dirty flag. Save Profile and Load Profile own
        # the clean transitions; everything else stays dirty if it was.
        self._update_profile_label()

    def _on_apply(self) -> None:
        self._settings.startup_tag = self._startup_tag_edit.text()
        self._settings.startup_text = self._startup_text_edit.text()

        # Level colors
        for lvl, btn in self._level_fg_btns.items():
            self._settings.level_fg[lvl] = btn.color()
        for lvl, btn in self._level_bg_btns.items():
            self._settings.level_bg[lvl] = btn.color()

        # Pinned "Search highlight" row — its fg/bg live in dedicated
        # settings keys rather than alongside the user-defined rules.
        if self._search_fg_btn is not None:
            self._settings.search_fg = self._search_fg_btn.color()
        if self._search_bg_btn is not None:
            self._settings.search_bg = self._search_bg_btn.color()

        self._settings.color_rules = self._collect_color_rules()
        # Per-project list — global self._settings.exclude_rules is untouched
        # and is owned exclusively by the Settings dialog.
        self._settings.profile_exclude_rules = self._collect_exclude_rules()
        self._settings.save()
        # Apply commits the dialog's edits to settings.json — clear the
        # uncommitted-edits highlight on Save / Apply / title-bar. (Note:
        # the named profile FILE may still be stale if the user wants those
        # changes persisted there too — they'd hit Save Profile for that.)
        self.set_profile_dirty(False)
        self.colors_applied.emit()

    # ================================================================== row helpers

    def _add_search_highlight_row(self) -> None:
        """Insert the pinned 'Search highlight' row at index 0.
        Pattern/Field are read-only; only fg/bg are user-editable. Drag-drop
        is blocked by ReorderableTable.pinned_top."""
        self._rules_table.insertRow(0)
        self._rules_table.pinned_top = 1

        # Col 0: On — show a static glyph (no checkbox; highlight is implicit
        # whenever the search field has text).
        marker = QLabel("★")
        marker.setAlignment(Qt.AlignCenter)
        marker.setToolTip("Pinned: highlight colors for the filter row's Search field.")
        self._rules_table.setCellWidget(0, 0, marker)

        # Col 1: Pattern — read-only italic label.
        pat = QTableWidgetItem("Search highlight")
        pat_font = pat.font()
        pat_font.setItalic(True)
        pat.setFont(pat_font)
        pat.setFlags(Qt.ItemIsEnabled)
        pat.setToolTip(
            "Live highlight colors for matches of the filter row's Search field.\n"
            "Pattern comes from the Search field; only the foreground/background\n"
            "colors are configured here."
        )
        self._rules_table.setItem(0, 1, pat)

        # Col 2: Field — fixed to MESSAGE.
        fld = QTableWidgetItem("MESSAGE")
        fld.setFlags(Qt.ItemIsEnabled)
        self._rules_table.setItem(0, 2, fld)

        # Col 3: Foreground — bound to settings.search_fg
        self._search_fg_btn = ColorButton(self._settings.search_fg)
        self._search_fg_btn.color_changed.connect(
            lambda _c: self._refresh_search_preview()
        )
        self._search_fg_btn.color_changed.connect(self._mark_dirty)
        self._rules_table.setCellWidget(0, 3, self._search_fg_btn)

        # Col 4: Background — bound to settings.search_bg
        self._search_bg_btn = ColorButton(self._settings.search_bg)
        self._search_bg_btn.color_changed.connect(
            lambda _c: self._refresh_search_preview()
        )
        self._search_bg_btn.color_changed.connect(self._mark_dirty)
        self._rules_table.setCellWidget(0, 4, self._search_bg_btn)

        # Col 5: Row — empty placeholder (search is span-based, not row-based).
        empty = QLabel("—")
        empty.setAlignment(Qt.AlignCenter)
        empty.setEnabled(False)
        self._rules_table.setCellWidget(0, 5, empty)

        # Mark the row in the vertical header.
        hdr = QTableWidgetItem("★")
        hdr.setToolTip("Pinned row — drag/delete are disabled.")
        self._rules_table.setVerticalHeaderItem(0, hdr)

        self._refresh_search_preview()

    def _refresh_search_preview(self) -> None:
        """Paint the pinned row's Pattern cell with its fg/bg as a preview."""
        if self._rules_table.rowCount() < 1 or self._rules_table.pinned_top < 1:
            return
        pat = self._rules_table.item(0, 1)
        if pat is None:
            return
        fg = self._search_fg_btn.color() if self._search_fg_btn else ""
        bg = self._search_bg_btn.color() if self._search_bg_btn else ""
        if fg:
            pat.setForeground(QColor(fg))
        else:
            pat.setData(Qt.ForegroundRole, None)
        if bg:
            pat.setBackground(QColor(bg))
        else:
            pat.setData(Qt.BackgroundRole, None)

    def _add_rule_row(self, rule: Optional[ColorRule] = None) -> None:
        row = self._rules_table.rowCount()
        self._rules_table.insertRow(row)

        # Col 0: On — real QCheckBox (item check-states render as featureless
        # squares under our dark stylesheet).
        on_wrap = _make_cell_checkbox(rule is None or rule.enabled)
        self._rules_table.setCellWidget(row, 0, on_wrap)
        on_cb = on_wrap.findChild(QCheckBox)
        if on_cb:
            on_cb.toggled.connect(self._mark_dirty)

        # Col 1: Pattern — editable text, painted with the rule's colors as a preview.
        pat = QTableWidgetItem(rule.pattern if rule else "")
        self._rules_table.setItem(row, 1, pat)

        # Col 2: Field — combo
        combo = QComboBox()
        combo.addItems(list(COLOR_RULE_FIELDS))
        if rule and rule.field in COLOR_RULE_FIELDS:
            combo.setCurrentText(rule.field)
        combo.setFrame(False)
        combo.currentTextChanged.connect(self._mark_dirty)
        self._rules_table.setCellWidget(row, 2, combo)

        # Col 3: Foreground — ColorButton (refresh preview on change)
        fg_btn = ColorButton(rule.fg if rule else "")
        fg_btn.color_changed.connect(
            lambda _c, b=fg_btn: self._refresh_rule_preview_for_btn(b)
        )
        fg_btn.color_changed.connect(self._mark_dirty)
        self._rules_table.setCellWidget(row, 3, fg_btn)

        # Col 4: Background — ColorButton (refresh preview on change)
        bg_btn = ColorButton(rule.bg if rule else "")
        bg_btn.color_changed.connect(
            lambda _c, b=bg_btn: self._refresh_rule_preview_for_btn(b)
        )
        bg_btn.color_changed.connect(self._mark_dirty)
        self._rules_table.setCellWidget(row, 4, bg_btn)

        # Col 5: Entire Row — real QCheckBox
        row_wrap = _make_cell_checkbox(
            rule is None or rule.entire_row,
            tooltip=(
                "Checked: apply colors to the entire row.\n"
                "Unchecked: highlight only the matching text in the Tag or Message column."
            ),
        )
        self._rules_table.setCellWidget(row, 5, row_wrap)
        row_cb = row_wrap.findChild(QCheckBox)
        if row_cb:
            row_cb.toggled.connect(self._mark_dirty)

        self._refresh_rule_preview(row)
        self._rules_table.scrollToBottom()
        if rule is None:
            self._rules_table.editItem(self._rules_table.item(row, 1))

    def _add_exclude_row(self, rule: Optional[ExcludeRule] = None) -> None:
        row = self._exclude_table.rowCount()
        self._exclude_table.insertRow(row)

        on_wrap = _make_cell_checkbox(rule is None or rule.enabled)
        self._exclude_table.setCellWidget(row, 0, on_wrap)
        on_cb = on_wrap.findChild(QCheckBox)
        if on_cb:
            on_cb.toggled.connect(self._mark_dirty)

        pat = QTableWidgetItem(rule.pattern if rule else "")
        self._exclude_table.setItem(row, 1, pat)

        from .app_settings import EXCLUDE_FIELDS
        combo = QComboBox()
        combo.addItems(list(EXCLUDE_FIELDS))
        combo.setCurrentText(rule.field if rule and rule.field in EXCLUDE_FIELDS else "TAG")
        combo.setFrame(False)
        combo.currentTextChanged.connect(self._mark_dirty)
        self._exclude_table.setCellWidget(row, 2, combo)

        self._exclude_table.scrollToBottom()
        if rule is None:
            self._exclude_table.editItem(self._exclude_table.item(row, 1))

    # ---------- drag-drop reorder ----------
    def _snapshot_color_rules(self) -> List[ColorRule]:
        """Capture every row's data verbatim, including blank-pattern rows,
        so a reorder doesn't drop in-progress entries. Skips the pinned
        search-highlight row."""
        rules: List[ColorRule] = []
        for row in range(self._rules_table.pinned_top, self._rules_table.rowCount()):
            pat = self._rules_table.item(row, 1)
            combo: Optional[QComboBox] = self._rules_table.cellWidget(row, 2)
            fg_btn: Optional[ColorButton] = self._rules_table.cellWidget(row, 3)
            bg_btn: Optional[ColorButton] = self._rules_table.cellWidget(row, 4)
            rules.append(ColorRule(
                pattern=(pat.text() if pat else ""),
                field=combo.currentText() if combo else "TAG",
                fg=fg_btn.color() if fg_btn else "",
                bg=bg_btn.color() if bg_btn else "",
                entire_row=_cell_checked(self._rules_table.cellWidget(row, 5)),
                enabled=_cell_checked(self._rules_table.cellWidget(row, 0)),
            ))
        return rules

    def _on_rule_row_moved(self, src: int, dest: int) -> None:
        # src/dest are absolute row indices in the table; convert to indices
        # into the rule list (which doesn't include the pinned row).
        pin = self._rules_table.pinned_top
        rules = self._snapshot_color_rules()
        src_i = src - pin
        dest_i = dest - pin
        if not (0 <= src_i < len(rules)) or not (0 <= dest_i < len(rules)):
            return
        rule = rules.pop(src_i)
        rules.insert(dest_i, rule)
        # Rebuild from scratch — Qt would otherwise leave cell widgets behind.
        self._rules_table.setRowCount(0)
        self._add_search_highlight_row()
        for r in rules:
            self._add_rule_row(r)
        self._rules_table.selectRow(dest)

    # ---------- rule preview helpers ----------
    def _refresh_rule_preview(self, row: int) -> None:
        """Paint the Pattern cell with the row's fg/bg colors as a preview."""
        if row < 0 or row >= self._rules_table.rowCount():
            return
        pat = self._rules_table.item(row, 1)
        if pat is None:
            return
        fg_btn = self._rules_table.cellWidget(row, 3)
        bg_btn = self._rules_table.cellWidget(row, 4)
        if isinstance(fg_btn, ColorButton) and fg_btn.color():
            pat.setForeground(QColor(fg_btn.color()))
        else:
            pat.setData(Qt.ForegroundRole, None)
        if isinstance(bg_btn, ColorButton) and bg_btn.color():
            pat.setBackground(QColor(bg_btn.color()))
        else:
            pat.setData(Qt.BackgroundRole, None)

    def _refresh_rule_preview_for_btn(self, btn: ColorButton) -> None:
        """Locate the row owning this ColorButton and refresh its preview."""
        for row in range(self._rules_table.rowCount()):
            if (
                self._rules_table.cellWidget(row, 3) is btn
                or self._rules_table.cellWidget(row, 4) is btn
            ):
                self._refresh_rule_preview(row)
                return

    def _delete_selected_rules(self) -> None:
        pin = self._rules_table.pinned_top
        rows = sorted(
            {idx.row() for idx in self._rules_table.selectedIndexes() if idx.row() >= pin},
            reverse=True,
        )
        for row in rows:
            self._rules_table.removeRow(row)

    def _delete_selected_excludes(self) -> None:
        rows = sorted(
            {idx.row() for idx in self._exclude_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self._exclude_table.removeRow(row)

    def _collect_color_rules(self) -> List[ColorRule]:
        rules: List[ColorRule] = []
        for row in range(self._rules_table.pinned_top, self._rules_table.rowCount()):
            pat = self._rules_table.item(row, 1)
            combo: Optional[QComboBox] = self._rules_table.cellWidget(row, 2)
            fg_btn: Optional[ColorButton] = self._rules_table.cellWidget(row, 3)
            bg_btn: Optional[ColorButton] = self._rules_table.cellWidget(row, 4)
            if pat is None:
                continue
            text = pat.text().strip()
            if not text:
                continue
            rules.append(ColorRule(
                pattern=text,
                field=combo.currentText() if combo else "TAG",
                fg=fg_btn.color() if fg_btn else "",
                bg=bg_btn.color() if bg_btn else "",
                entire_row=_cell_checked(self._rules_table.cellWidget(row, 5)),
                enabled=_cell_checked(self._rules_table.cellWidget(row, 0)),
            ))
        return rules

    def _collect_exclude_rules(self) -> List[ExcludeRule]:
        rules: List[ExcludeRule] = []
        for row in range(self._exclude_table.rowCount()):
            pat = self._exclude_table.item(row, 1)
            combo: Optional[QComboBox] = self._exclude_table.cellWidget(row, 2)
            if pat is None or combo is None:
                continue
            text = pat.text().strip()
            if not text:
                continue
            rules.append(ExcludeRule(
                pattern=text,
                field=combo.currentText(),
                enabled=_cell_checked(self._exclude_table.cellWidget(row, 0)),
            ))
        return rules

    # ================================================================== profiles

    def _profiles_dir(self) -> Path:
        d = _profiles_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_profile(self) -> None:
        rules = self._collect_color_rules()
        excludes = self._collect_exclude_rules()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Color Profile",
            str(self._profiles_dir()),
            "Color Profile (*.json)",
        )
        if not path:
            return
        if not path.endswith(".json"):
            path += ".json"
        # Level / search-highlight colors live in dedicated settings keys
        # but are part of the visual "profile" — snapshot them too so loading
        # a profile restores the full look, not just the rule list.
        level_fg = {lvl: btn.color() for lvl, btn in self._level_fg_btns.items()}
        level_bg = {lvl: btn.color() for lvl, btn in self._level_bg_btns.items()}
        search_fg = self._search_fg_btn.color() if self._search_fg_btn else ""
        search_bg = self._search_bg_btn.color() if self._search_bg_btn else ""
        data = {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "startup_tag": self._startup_tag_edit.text(),
            "startup_text": self._startup_text_edit.text(),
            "level_fg": level_fg,
            "level_bg": level_bg,
            "search_fg": search_fg,
            "search_bg": search_bg,
            "color_rules": [r.to_dict() for r in rules],
            "exclude_rules": [
                {"pattern": r.pattern, "field": r.field, "enabled": r.enabled}
                for r in excludes
            ],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        self._settings.last_profile_name = Path(path).stem
        self._settings.save()
        self.set_profile_dirty(False)
        self._update_profile_label()

    def _load_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Color Profile",
            str(self._profiles_dir()),
            "Color Profile (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)

            data = _migrate_profile(data)

            startup_tag = data.get("startup_tag", "")
            startup_text = data.get("startup_text", "")
            if not isinstance(startup_tag, str):
                startup_tag = ""
            if not isinstance(startup_text, str):
                startup_text = ""

            color_rules = [
                ColorRule.from_dict(r)
                for r in data.get("color_rules", [])
                if isinstance(r, dict)
            ]
            exclude_rules = [
                ExcludeRule(
                    pattern=r.get("pattern", ""),
                    field=r.get("field", "TAG"),
                    enabled=bool(r.get("enabled", True)),
                )
                for r in data.get("exclude_rules", [])
                if isinstance(r, dict)
            ]
            # Level / search colors are optional — older profiles (saved before
            # they were included) just keep the dialog's current values.
            raw_level_fg = data.get("level_fg") if isinstance(data.get("level_fg"), dict) else {}
            raw_level_bg = data.get("level_bg") if isinstance(data.get("level_bg"), dict) else {}
            level_fg = {k: v for k, v in raw_level_fg.items() if isinstance(v, str)}
            level_bg = {k: v for k, v in raw_level_bg.items() if isinstance(v, str)}
            search_fg = data.get("search_fg") if isinstance(data.get("search_fg"), str) else None
            search_bg = data.get("search_bg") if isinstance(data.get("search_bg"), str) else None
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Load Error", str(exc))
            return

        self._suppress_dirty = True
        try:
            self._startup_tag_edit.setText(startup_tag)
            self._startup_text_edit.setText(startup_text)

            for lvl, btn in self._level_fg_btns.items():
                if lvl in level_fg:
                    btn.set_color(level_fg[lvl])
            for lvl, btn in self._level_bg_btns.items():
                if lvl in level_bg:
                    btn.set_color(level_bg[lvl])

            self._rules_table.setRowCount(0)
            self._add_search_highlight_row()
            for rule in color_rules:
                self._add_rule_row(rule)
            # _add_search_highlight_row created fresh buttons bound to current
            # settings.search_fg/bg; override them with the profile values.
            if search_fg is not None and self._search_fg_btn is not None:
                self._search_fg_btn.set_color(search_fg)
            if search_bg is not None and self._search_bg_btn is not None:
                self._search_bg_btn.set_color(search_bg)
            self._refresh_search_preview()

            self._exclude_table.setRowCount(0)
            for rule in exclude_rules:
                self._add_exclude_row(rule)
        finally:
            self._suppress_dirty = False

        self._settings.last_profile_name = Path(path).stem
        self._settings.save()
        self.set_profile_dirty(False)
        self._update_profile_label()
