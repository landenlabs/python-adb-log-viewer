from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
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

from .app_settings import AppSettings, _profiles_dir
from .color_rules import COLOR_RULE_FIELDS, ColorRule

_LEVEL_LABELS = {
    "D": "D  Debug",
    "I": "I  Info",
    "W": "W  Warning",
    "E": "E  Error",
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


class ColorsDialog(QDialog):
    """
    Non-modal Colors dialog.

    Level Colors section: configure fg/bg for D, I, W, E levels.
    Color Rules section:  regex-based row or text highlighting with save/load profiles.
    """

    colors_applied = Signal()

    def __init__(
        self,
        settings: AppSettings,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Colors")
        self.resize(820, 680)
        self.setModal(False)
        self._settings = settings
        self._build_ui()
        self._load()

    # ================================================================== build

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        root.addWidget(self._build_level_group())
        root.addWidget(self._build_rules_group(), stretch=1)

        btns = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        btns.button(QDialogButtonBox.Apply).clicked.connect(self._on_apply)
        btns.button(QDialogButtonBox.Close).clicked.connect(self.hide)
        root.addWidget(btns)

    # ------------------------------------------------------------------ level colors

    def _build_level_group(self) -> QWidget:
        grp = QGroupBox("Log Level Colors")
        grid = QGridLayout(grp)
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
        return grp

    # ------------------------------------------------------------------ color rules

    def _build_rules_group(self) -> QWidget:
        grp = QGroupBox("Color Rules  –  first matching rule wins for row coloring")
        layout = QVBoxLayout(grp)

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

        self._rules_table.horizontalHeader().setToolTip(
            "Row column: checked = color entire row, unchecked = highlight matching text only"
        )

        layout.addWidget(self._rules_table)

        # Buttons row
        btn_row = QHBoxLayout()

        self._btn_add = QPushButton("+ Add Rule")
        self._btn_add.setToolTip("Append a new blank color rule")
        self._btn_delete = QPushButton("Delete Selected")
        self._btn_delete.setToolTip("Remove the selected rule(s)")
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_delete)
        btn_row.addStretch()

        self._btn_save_profile = QPushButton("Save Profile…")
        self._btn_save_profile.setToolTip(
            "Save the current color rules list to a JSON profile file"
        )
        self._btn_load_profile = QPushButton("Load Profile…")
        self._btn_load_profile.setToolTip(
            "Load a previously saved color rules profile"
        )
        btn_row.addWidget(self._btn_save_profile)
        btn_row.addWidget(self._btn_load_profile)

        hint = QLabel(
            "Tip: double-click Pattern to edit.  Right-click a color button to clear it."
        )
        hint.setStyleSheet("color: #888; font-size: 10px;")
        layout.addLayout(btn_row)
        layout.addWidget(hint)

        self._btn_add.clicked.connect(lambda: self._add_rule_row())
        self._btn_delete.clicked.connect(self._delete_selected)
        self._btn_save_profile.clicked.connect(self._save_profile)
        self._btn_load_profile.clicked.connect(self._load_profile)

        return grp

    # ================================================================== load / save

    def _load(self) -> None:
        for lvl, btn in self._level_fg_btns.items():
            btn.set_color(self._settings.level_fg.get(lvl, ""))
        for lvl, btn in self._level_bg_btns.items():
            btn.set_color(self._settings.level_bg.get(lvl, ""))

        self._rules_table.setRowCount(0)
        for rule in self._settings.color_rules:
            self._add_rule_row(rule)

    def _on_apply(self) -> None:
        # Level colors
        for lvl, btn in self._level_fg_btns.items():
            self._settings.level_fg[lvl] = btn.color()
        for lvl, btn in self._level_bg_btns.items():
            self._settings.level_bg[lvl] = btn.color()

        self._settings.color_rules = self._collect_rules()
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

    def _delete_selected(self) -> None:
        rows = sorted(
            {idx.row() for idx in self._rules_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self._rules_table.removeRow(row)

    def _collect_rules(self) -> List[ColorRule]:
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

    # ================================================================== profiles

    def _profiles_dir(self) -> Path:
        d = _profiles_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_profile(self) -> None:
        rules = self._collect_rules()
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
        data = {"color_rules": [r.to_dict() for r in rules]}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

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
            rules = [
                ColorRule.from_dict(r)
                for r in data.get("color_rules", [])
                if isinstance(r, dict)
            ]
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Load Error", str(exc))
            return

        self._rules_table.setRowCount(0)
        for rule in rules:
            self._add_rule_row(rule)
