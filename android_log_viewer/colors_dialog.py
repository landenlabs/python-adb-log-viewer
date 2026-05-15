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
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .app_settings import EXCLUDE_FIELDS, AppSettings, ExcludeRule, _profiles_dir
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


class CollapsibleBox(QWidget):
    """A simple collapsible container with a title and a toggle button."""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._is_expanded = True

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self._header = QPushButton(f"▼  {title}")
        self._header.setObjectName("collapsible_header")
        self._header.setCheckable(True)
        self._header.setChecked(True)
        self._header.clicked.connect(self.toggle)
        self._layout.addWidget(self._header)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(4, 8, 4, 8)
        self._layout.addWidget(self._content)

    def toggle(self) -> None:
        self._is_expanded = not self._is_expanded
        self._content.setVisible(self._is_expanded)
        self._content.setMaximumHeight(16777215 if self._is_expanded else 0)
        title = self._header.text()[3:]
        self._header.setText(f"{'▼' if self._is_expanded else '▶'}  {title}")

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
        self._build_ui()
        self._load()

    # ================================================================== build

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # Profile name banner
        self._profile_name_label = QLabel()
        self._profile_name_label.setObjectName("profile_name_label")
        self._profile_name_label.setStyleSheet(
            "color: #555; font-size: 10px; padding: 1px 6px;"
        )
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
        bottom_row.addWidget(self._btn_load_profile)
        bottom_row.addWidget(self._btn_save_profile)
        
        bottom_row.addStretch()

        btns = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        btns.button(QDialogButtonBox.Apply).clicked.connect(self._on_apply)
        btns.button(QDialogButtonBox.Close).clicked.connect(self.hide)
        bottom_row.addWidget(btns)
        
        root.addLayout(bottom_row)

        self._btn_save_profile.clicked.connect(self._save_profile)
        self._btn_load_profile.clicked.connect(self._load_profile)

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

        # Table
        self._rules_table = QTableWidget()
        self._rules_table.setColumnCount(6)
        self._rules_table.setHorizontalHeaderLabels(
            ["On", "Pattern  (regex, case-insensitive)", "Field", "Foreground", "Background", "Row"]
        )
        self._rules_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._rules_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._rules_table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self._rules_table.verticalHeader().setVisible(False)
        self._rules_table.verticalHeader().setDefaultSectionSize(28)
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
        hint.setStyleSheet("color: #888; font-size: 10px;")
        layout.addLayout(btn_row)
        layout.addWidget(hint)

        self._btn_add.clicked.connect(lambda: self._add_rule_row())
        self._btn_delete_rules.clicked.connect(self._delete_selected_rules)

        return container

    def _build_exclude_group(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

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
        name = self._settings.last_profile_name
        self._profile_name_label.setText(
            f"Profile: {name}" if name else "Profile: (none)"
        )

    def _load(self) -> None:
        for lvl, btn in self._level_fg_btns.items():
            btn.set_color(self._settings.level_fg.get(lvl, ""))
        for lvl, btn in self._level_bg_btns.items():
            btn.set_color(self._settings.level_bg.get(lvl, ""))

        self._rules_table.setRowCount(0)
        for rule in self._settings.color_rules:
            self._add_rule_row(rule)

        self._exclude_table.setRowCount(0)
        for rule in self._settings.exclude_rules:
            self._add_exclude_row(rule)

        self._update_profile_label()

    def _on_apply(self) -> None:
        # Level colors
        for lvl, btn in self._level_fg_btns.items():
            self._settings.level_fg[lvl] = btn.color()
        for lvl, btn in self._level_bg_btns.items():
            self._settings.level_bg[lvl] = btn.color()

        self._settings.color_rules = self._collect_color_rules()
        self._settings.exclude_rules = self._collect_exclude_rules()
        self._settings.save()
        self.colors_applied.emit()

    # ================================================================== row helpers

    def _add_rule_row(self, rule: Optional[ColorRule] = None) -> None:
        row = self._rules_table.rowCount()
        self._rules_table.insertRow(row)

        # Col 0: On — checkbox item
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
        chk.setCheckState(Qt.Checked if (rule is None or rule.enabled) else Qt.Unchecked)
        chk.setTextAlignment(Qt.AlignCenter)
        self._rules_table.setItem(row, 0, chk)

        # Col 1: Pattern — editable text
        pat = QTableWidgetItem(rule.pattern if rule else "")
        self._rules_table.setItem(row, 1, pat)

        # Col 2: Field — combo
        combo = QComboBox()
        combo.addItems(list(COLOR_RULE_FIELDS))
        if rule and rule.field in COLOR_RULE_FIELDS:
            combo.setCurrentText(rule.field)
        combo.setFrame(False)
        self._rules_table.setCellWidget(row, 2, combo)

        # Col 3: Foreground — ColorButton
        fg_btn = ColorButton(rule.fg if rule else "")
        self._rules_table.setCellWidget(row, 3, fg_btn)

        # Col 4: Background — ColorButton
        bg_btn = ColorButton(rule.bg if rule else "")
        self._rules_table.setCellWidget(row, 4, bg_btn)

        # Col 5: Entire Row — checkbox item
        row_chk = QTableWidgetItem()
        row_chk.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
        row_chk.setCheckState(
            Qt.Checked if (rule is None or rule.entire_row) else Qt.Unchecked
        )
        row_chk.setToolTip(
            "Checked: apply colors to the entire row.\n"
            "Unchecked: highlight only the matching text in the Tag or Message column."
        )
        row_chk.setTextAlignment(Qt.AlignCenter)
        self._rules_table.setItem(row, 5, row_chk)

        self._rules_table.scrollToBottom()
        if rule is None:
            self._rules_table.editItem(self._rules_table.item(row, 1))

    def _add_exclude_row(self, rule: Optional[ExcludeRule] = None) -> None:
        row = self._exclude_table.rowCount()
        self._exclude_table.insertRow(row)

        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
        chk.setCheckState(Qt.Checked if (rule is None or rule.enabled) else Qt.Unchecked)
        chk.setTextAlignment(Qt.AlignCenter)
        self._exclude_table.setItem(row, 0, chk)

        pat = QTableWidgetItem(rule.pattern if rule else "")
        self._exclude_table.setItem(row, 1, pat)

        from .app_settings import EXCLUDE_FIELDS
        combo = QComboBox()
        combo.addItems(list(EXCLUDE_FIELDS))
        combo.setCurrentText(rule.field if rule and rule.field in EXCLUDE_FIELDS else "TAG")
        combo.setFrame(False)
        self._exclude_table.setCellWidget(row, 2, combo)

        self._exclude_table.scrollToBottom()
        if rule is None:
            self._exclude_table.editItem(self._exclude_table.item(row, 1))

    def _delete_selected_rules(self) -> None:
        rows = sorted(
            {idx.row() for idx in self._rules_table.selectedIndexes()},
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
        for row in range(self._rules_table.rowCount()):
            chk = self._rules_table.item(row, 0)
            pat = self._rules_table.item(row, 1)
            combo: Optional[QComboBox] = self._rules_table.cellWidget(row, 2)
            fg_btn: Optional[ColorButton] = self._rules_table.cellWidget(row, 3)
            bg_btn: Optional[ColorButton] = self._rules_table.cellWidget(row, 4)
            row_chk = self._rules_table.item(row, 5)
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
                entire_row=(row_chk.checkState() == Qt.Checked) if row_chk else True,
                enabled=(chk.checkState() == Qt.Checked) if chk else True,
            ))
        return rules

    def _collect_exclude_rules(self) -> List[ExcludeRule]:
        rules: List[ExcludeRule] = []
        for row in range(self._exclude_table.rowCount()):
            chk = self._exclude_table.item(row, 0)
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
                enabled=(chk.checkState() == Qt.Checked) if chk else True,
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
        data = {
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
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Load Error", str(exc))
            return

        self._rules_table.setRowCount(0)
        for rule in color_rules:
            self._add_rule_row(rule)

        self._exclude_table.setRowCount(0)
        for rule in exclude_rules:
            self._add_exclude_row(rule)

        self._settings.last_profile_name = Path(path).stem
        self._settings.save()
        self._update_profile_label()
