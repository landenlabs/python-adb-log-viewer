import argparse
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .resources import resource_path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Android Log Viewer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "If --tag or --text is given and exactly one device is connected,\n"
            "the viewer will auto-connect on launch.\n"
            "\n"
            "--colors accepts either a bare profile name (resolved against the\n"
            "  default color_profiles directory, with .json appended), or a\n"
            "  relative / absolute path to a profile .json file."
        ),
    )
    p.add_argument("-t", "--tag",  default="", metavar="REGEX",
                   help="Initial TAG filter regex")
    p.add_argument("-m", "--text", default="", metavar="REGEX",
                   help="Initial message text filter regex")
    p.add_argument("-c", "--colors", default="", metavar="NAME|PATH",
                   help="Load and apply a color profile at startup")
    args, _ = p.parse_known_args()
    return args


def main() -> None:
    args = _parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName("Android Log Viewer")
    # App-wide default window icon. Sub-dialogs that call setWindowIcon()
    # themselves (e.g. about, stats, packages) override this; everything
    # else — including the main window — inherits it. We prefer the .ico
    # over the .png because Qt picks the right embedded size per context
    # (title bar = 16, taskbar = 32, alt-tab = 48) instead of downscaling
    # one large source.
    icon_path = resource_path("log-viewer.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    win = MainWindow(
        initial_tag=args.tag,
        initial_text=args.text,
        initial_color_profile=args.colors,
    )
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
