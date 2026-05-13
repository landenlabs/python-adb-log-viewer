#!/usr/bin/env python3
"""Entry point: python main.py"""
import argparse
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from android_log_viewer.main_window import MainWindow
from android_log_viewer.resources import resource_path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Android Log Viewer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "If --tag or --text is given and exactly one device is connected,\n"
            "the viewer will auto-connect on launch."
        ),
    )
    p.add_argument("-t", "--tag",  default="", metavar="REGEX",
                   help="Initial TAG filter regex")
    p.add_argument("-m", "--text", default="", metavar="REGEX",
                   help="Initial message text filter regex")
    args, _ = p.parse_known_args()   # ignore Qt-specific flags (-style, -display, …)
    return args


def main() -> None:
    args = _parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName("Android Log Viewer")
    app.setOrganizationName("LanDen Labs")
    app.setOrganizationDomain("landenlabs.com")
    icon_png = resource_path("log-viewer.png")
    if icon_png.exists():
        app.setWindowIcon(QIcon(str(icon_png)))
    win = MainWindow(initial_tag=args.tag, initial_text=args.text)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
