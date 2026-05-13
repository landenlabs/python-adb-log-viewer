from __future__ import annotations

from PySide6.QtWidgets import QApplication

# --- Modern Palette ---
# Dark: JetBrains/VSCode inspired
D_BG_DEEP = "#1e1e1e"      # Table background
D_BG_MAIN = "#252526"      # Main window background
D_BG_ALT  = "#2d2d2d"      # Alternate rows / Toolbars
D_BG_SIDER = "#333333"     # Header sections
D_BORDER  = "#454545"
D_ACCENT  = "#007acc"      # Primary Blue
D_TEXT    = "#cccccc"
D_TEXT_HI = "#ffffff"

# Light: GitHub/MacOS inspired
L_BG_MAIN = "#ffffff"
L_BG_ALT  = "#f6f8fa"
L_BORDER  = "#d0d7de"
L_ACCENT  = "#0969da"
L_TEXT    = "#24292f"


LIGHT_QSS = f"""
QMainWindow, QDialog {{
    background-color: {L_BG_MAIN};
    color: {L_TEXT};
}}
QToolBar {{
    background-color: {L_BG_ALT};
    border-bottom: 1px solid {L_BORDER};
    padding: 4px;
    spacing: 6px;
}}
QTableView {{
    background-color: {L_BG_MAIN};
    alternate-background-color: {L_BG_ALT};
    gridline-color: {L_BORDER};
    selection-background-color: #c8e1ff;
    selection-color: {L_TEXT};
    border: none;
}}
QHeaderView::section {{
    background-color: {L_BG_ALT};
    border: none;
    border-bottom: 1px solid {L_BORDER};
    border-right: 1px solid {L_BORDER};
    padding: 4px;
    font-weight: bold;
}}
QLineEdit {{
    border: 1px solid {L_BORDER};
    border-radius: 4px;
    padding: 3px 8px;
    background: white;
}}
QLineEdit:focus {{ border: 1px solid {L_ACCENT}; }}
QPushButton {{
    background-color: #f3f4f6;
    border: 1px solid {L_BORDER};
    border-radius: 4px;
    padding: 4px 12px;
    font-weight: 500;
}}
QPushButton:hover {{ background-color: #e5e7eb; }}
QPushButton:pressed {{ background-color: #d1d5db; }}
QPushButton:checked {{
    background-color: {L_ACCENT};
    color: white;
    border-color: {L_ACCENT};
}}
QStatusBar {{
    background-color: {L_BG_ALT};
    border-top: 1px solid {L_BORDER};
}}
#zoom_frame QPushButton {{
    padding: 2px 4px;
}}
QSplitter::handle {{ background-color: {L_BORDER}; }}
QPushButton#collapsible_header {{
    text-align: left;
    font-weight: bold;
    padding: 6px;
    background: #e0e0e0;
    color: {L_TEXT};
    border: 1px solid {L_BORDER};
    border-radius: 4px;
}}
QPushButton#collapsible_header:checked {{
    background: #d0d0d0;
    color: {L_TEXT};
}}
QPushButton#collapsible_header:hover {{ background: #d5d5d5; }}
"""

DARK_QSS = f"""
QMainWindow, QDialog {{
    background-color: {D_BG_MAIN};
    color: {D_TEXT};
}}
QToolBar {{
    background-color: {D_BG_ALT};
    border-bottom: 1px solid {D_BORDER};
    padding: 4px;
    spacing: 6px;
}}
QTableView {{
    background-color: {D_BG_DEEP};
    alternate-background-color: #252525;
    gridline-color: {D_BORDER};
    selection-background-color: #264f78;
    selection-color: {D_TEXT_HI};
    border: none;
}}
QHeaderView::section {{
    background-color: {D_BG_SIDER};
    border: none;
    border-bottom: 1px solid {D_BORDER};
    border-right: 1px solid {D_BORDER};
    padding: 4px;
    color: #aaaaaa;
}}
QLineEdit {{
    background-color: #3c3c3c;
    border: 1px solid {D_BORDER};
    border-radius: 4px;
    padding: 3px 8px;
    color: {D_TEXT_HI};
}}
QLineEdit:focus {{ border: 1px solid {D_ACCENT}; }}
QPushButton {{
    background-color: #3a3a3a;
    border: 1px solid {D_BORDER};
    border-radius: 4px;
    padding: 4px 12px;
    color: {D_TEXT};
}}
QPushButton:hover {{ background-color: #454545; border-color: #555555; }}
QPushButton:pressed {{ background-color: #2d2d2d; }}
QPushButton:checked {{
    background-color: {D_ACCENT};
    color: {D_TEXT_HI};
    border-color: {D_ACCENT};
}}
QComboBox {{
    background-color: #3c3c3c;
    border: 1px solid {D_BORDER};
    border-radius: 4px;
    padding: 2px 8px;
}}
QStatusBar {{
    background-color: {D_BG_ALT};
    border-top: 1px solid {D_BORDER};
    color: #888888;
}}
#zoom_frame QPushButton {{
    padding: 2px 4px;
}}
QSplitter::handle {{ background-color: {D_BORDER}; }}
QPushButton#collapsible_header {{
    text-align: left;
    font-weight: bold;
    padding: 6px;
    background: #444444;
    color: {D_TEXT_HI};
    border: 1px solid {D_BORDER};
    border-radius: 4px;
}}
QPushButton#collapsible_header:checked {{
    background: #444444;
    color: {D_TEXT_HI};
}}
QPushButton#collapsible_header:hover {{ background: #555555; }}
QScrollBar:vertical {{
    background-color: {D_BG_DEEP};
    width: 12px;
}}
QScrollBar::handle:vertical {{
    background-color: #424242;
    min-height: 20px;
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background-color: #4f4f4f; }}
"""


def apply_theme(theme: str) -> None:
    app = QApplication.instance()
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_QSS if theme == "dark" else LIGHT_QSS)
