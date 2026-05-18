from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
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
QLabel, QCheckBox, QRadioButton, QGroupBox {{ color: {L_TEXT}; }}
QGroupBox::title {{ color: {L_TEXT}; }}
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
    color: {L_TEXT};
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
    color: {L_TEXT};
}}
QLineEdit:focus {{ border: 1px solid {L_ACCENT}; }}
QComboBox {{
    background-color: white;
    border: 1px solid {L_BORDER};
    border-radius: 4px;
    padding: 2px 8px;
    color: {L_TEXT};
}}
QComboBox QLineEdit {{
    background: transparent;
    border: none;
    color: {L_TEXT};
}}
QComboBox QAbstractItemView {{
    background-color: white;
    color: {L_TEXT};
    selection-background-color: #c8e1ff;
    selection-color: {L_TEXT};
    border: 1px solid {L_BORDER};
    outline: none;
}}
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
    color: {L_TEXT};
}}
QStatusBar QLabel, QStatusBar QCheckBox {{ color: {L_TEXT}; }}
#zoom_frame QPushButton {{
    padding: 2px 4px;
}}
QSplitter::handle {{ background-color: {L_BORDER}; }}
QScrollArea {{ background-color: {L_BG_MAIN}; border: none; }}
QScrollArea > QWidget > QWidget {{ background-color: {L_BG_MAIN}; }}
QGroupBox {{
    background-color: {L_BG_MAIN};
    border: 1px solid {L_BORDER};
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 4px;
    color: {L_TEXT};
}}
QFrame {{ background-color: transparent; }}
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
QLabel, QCheckBox, QRadioButton, QGroupBox {{ color: {D_TEXT}; }}
QGroupBox::title {{ color: {D_TEXT_HI}; }}
QToolBar {{
    background-color: {D_BG_ALT};
    border-bottom: 1px solid {D_BORDER};
    padding: 4px;
    spacing: 6px;
    color: {D_TEXT};
}}
QToolBar QLabel {{ color: {D_TEXT}; }}
QTableView {{
    background-color: {D_BG_DEEP};
    alternate-background-color: #252525;
    gridline-color: {D_BORDER};
    selection-background-color: #264f78;
    selection-color: {D_TEXT_HI};
    color: {D_TEXT};
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
    color: {D_TEXT_HI};
}}
QComboBox:editable {{
    background-color: #3c3c3c;
    color: {D_TEXT_HI};
}}
QComboBox QLineEdit {{
    background: transparent;
    border: none;
    color: {D_TEXT_HI};
}}
QComboBox QAbstractItemView {{
    background-color: #3c3c3c;
    color: {D_TEXT_HI};
    selection-background-color: #264f78;
    selection-color: {D_TEXT_HI};
    border: 1px solid {D_BORDER};
    outline: none;
}}
QStatusBar {{
    background-color: {D_BG_ALT};
    border-top: 1px solid {D_BORDER};
    color: {D_TEXT};
}}
QStatusBar QLabel, QStatusBar QCheckBox {{ color: {D_TEXT}; }}
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
QScrollArea {{ background-color: {D_BG_MAIN}; border: none; }}
QScrollArea > QWidget > QWidget {{ background-color: {D_BG_MAIN}; }}
QGroupBox {{
    background-color: {D_BG_MAIN};
    border: 1px solid {D_BORDER};
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 8px;
    color: {D_TEXT};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 4px;
    color: {D_TEXT_HI};
}}
QFrame {{ background-color: transparent; }}
QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background-color: #3c3c3c;
    color: {D_TEXT_HI};
    border: 1px solid {D_BORDER};
    border-radius: 4px;
}}
QToolTip {{
    background-color: #3c3c3c;
    color: {D_TEXT_HI};
    border: 1px solid {D_BORDER};
}}
"""


def _dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.Window,          QColor(D_BG_MAIN))
    p.setColor(QPalette.WindowText,      QColor(D_TEXT))
    p.setColor(QPalette.Base,            QColor(D_BG_DEEP))
    p.setColor(QPalette.AlternateBase,   QColor("#252525"))
    p.setColor(QPalette.Text,            QColor(D_TEXT))
    p.setColor(QPalette.Button,          QColor("#3a3a3a"))
    p.setColor(QPalette.ButtonText,      QColor(D_TEXT))
    p.setColor(QPalette.BrightText,      QColor(D_TEXT_HI))
    p.setColor(QPalette.Highlight,       QColor("#264f78"))
    p.setColor(QPalette.HighlightedText, QColor(D_TEXT_HI))
    p.setColor(QPalette.ToolTipBase,     QColor("#3c3c3c"))
    p.setColor(QPalette.ToolTipText,     QColor(D_TEXT_HI))
    p.setColor(QPalette.PlaceholderText, QColor("#888888"))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#666666"))
    p.setColor(QPalette.Disabled, QPalette.Text,       QColor("#666666"))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#666666"))
    return p


def _light_palette() -> QPalette:
    # Built from the system default so platform tweaks survive.
    p = QApplication.style().standardPalette()
    p.setColor(QPalette.Window,     QColor(L_BG_MAIN))
    p.setColor(QPalette.WindowText, QColor(L_TEXT))
    p.setColor(QPalette.Base,       QColor(L_BG_MAIN))
    p.setColor(QPalette.Text,       QColor(L_TEXT))
    return p


def apply_theme(theme: str) -> None:
    app = QApplication.instance()
    app.setStyle("Fusion")
    if theme == "dark":
        app.setPalette(_dark_palette())
        app.setStyleSheet(DARK_QSS)
    else:
        app.setPalette(_light_palette())
        app.setStyleSheet(LIGHT_QSS)
