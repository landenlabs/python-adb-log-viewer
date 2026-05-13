from __future__ import annotations

from PySide6.QtWidgets import QApplication

LIGHT_QSS = """
QLineEdit:read-only {
    background-color: #f5f5f5;
    color: #555;
}
QWidget#zoom_frame {
    border: 1px solid #bbb;
    border-radius: 3px;
}
QWidget#zoom_frame QPushButton {
    border: none;
    padding: 0 3px;
    background: transparent;
}
QWidget#zoom_frame QPushButton:hover {
    background: #e0e0e0;
    border-radius: 2px;
}
QWidget#zoom_frame QPushButton:pressed { background: #bdbdbd; }
QPushButton#zoom_pct {
    border-left: 1px solid #bbb;
    border-right: 1px solid #bbb;
    border-top: none;
    border-bottom: none;
}
QPushButton#zoom_pct:hover  { background: #e0e0e0; }
QPushButton#zoom_pct:pressed { background: #bdbdbd; }
"""

DARK_QSS = """
QWidget {
    background-color: #2b2b2b;
    color: #d4d4d4;
}
QMainWindow { background-color: #252525; }
QToolBar {
    background-color: #313131;
    border-bottom: 1px solid #444;
    spacing: 4px;
}
QLabel { background-color: transparent; }
QTableView, QTableWidget {
    background-color: #1e1e1e;
    alternate-background-color: #252525;
    gridline-color: #3a3a3a;
    color: #d4d4d4;
    selection-background-color: #264f78;
    selection-color: #ffffff;
}
QHeaderView::section {
    background-color: #353535;
    border: 1px solid #444;
    color: #d4d4d4;
    padding: 2px 4px;
}
QGroupBox {
    border: 1px solid #505050;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 6px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}
QLineEdit {
    background-color: #3c3f41;
    border: 1px solid #555;
    border-radius: 3px;
    color: #d4d4d4;
    padding: 2px 4px;
    selection-background-color: #264f78;
}
QLineEdit:focus { border-color: #4d94d4; }
QLineEdit:read-only {
    background-color: #313131;
    color: #a0a0a0;
}
QPushButton {
    background-color: #4a4a4a;
    border: 1px solid #606060;
    border-radius: 3px;
    color: #d4d4d4;
    padding: 3px 10px;
}
QPushButton:hover {
    background-color: #575757;
    border-color: #6e6e6e;
}
QPushButton:pressed { background-color: #3a3a3a; }
QPushButton:checked {
    background-color: #264f78;
    border-color: #4d94d4;
    color: #ffffff;
}
QPushButton:disabled {
    color: #666;
    background-color: #3a3a3a;
    border-color: #484848;
}
QComboBox {
    background-color: #3c3f41;
    border: 1px solid #555;
    border-radius: 3px;
    color: #d4d4d4;
    padding: 2px 6px;
}
QComboBox::drop-down { border: none; width: 16px; }
QComboBox QAbstractItemView {
    background-color: #3c3f41;
    border: 1px solid #555;
    color: #d4d4d4;
    selection-background-color: #264f78;
    selection-color: #ffffff;
    outline: none;
}
QCheckBox { spacing: 6px; }
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #666;
    border-radius: 2px;
    background-color: #3c3f41;
}
QCheckBox::indicator:checked {
    background-color: #264f78;
    border-color: #4d94d4;
}
QScrollBar:vertical {
    background-color: #2b2b2b;
    width: 12px;
    border: none;
}
QScrollBar::handle:vertical {
    background-color: #555;
    min-height: 24px;
    border-radius: 4px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover { background-color: #707070; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background-color: #2b2b2b;
    height: 12px;
    border: none;
}
QScrollBar::handle:horizontal {
    background-color: #555;
    min-width: 24px;
    border-radius: 4px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover { background-color: #707070; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QStatusBar {
    background-color: #313131;
    border-top: 1px solid #444;
}
QDialog { background-color: #2b2b2b; }
QMenu {
    background-color: #3c3f41;
    border: 1px solid #555;
    padding: 4px 0;
}
QMenu::item { padding: 4px 20px; }
QMenu::item:selected {
    background-color: #264f78;
    color: #ffffff;
}
QMenu::separator {
    height: 1px;
    background-color: #555;
    margin: 4px 0;
}
QToolTip {
    background-color: #4a4a4a;
    border: 1px solid #666;
    color: #d4d4d4;
    padding: 4px 6px;
}
QSplitter::handle { background-color: #444; }
QFrame[frameShape="4"], QFrame[frameShape="5"] { color: #444; }
QWidget#zoom_frame {
    border: 1px solid #555;
    border-radius: 3px;
}
QWidget#zoom_frame QPushButton {
    border: none;
    padding: 0 3px;
    background: transparent;
    color: #d4d4d4;
}
QWidget#zoom_frame QPushButton:hover {
    background: #555;
    border-radius: 2px;
}
QWidget#zoom_frame QPushButton:pressed { background: #3a3a3a; }
QPushButton#zoom_pct {
    border-left: 1px solid #555;
    border-right: 1px solid #555;
    border-top: none;
    border-bottom: none;
}
QPushButton#zoom_pct:hover  { background: #555; }
QPushButton#zoom_pct:pressed { background: #3a3a3a; }
"""


def apply_theme(theme: str) -> None:
    app = QApplication.instance()
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_QSS if theme == "dark" else LIGHT_QSS)
