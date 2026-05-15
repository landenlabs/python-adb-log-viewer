from __future__ import annotations

from typing import List, Optional, Set

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QSize, QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .adb_reader import AdbReader, check_adb, find_adb_on_path, list_devices, parse_line, resolve_adb
from .version import __version__
from .app_settings import AppSettings
from .colors_dialog import ColorsDialog
from .constants import LEVEL_NAMES, LEVELS, MAX_RECORDS, PRUNE_SIZE
from .database import LogDatabase
from .filter_view_dialog import FilterViewDialog
from .log_model import COL_MSG, COL_TAG, HighlightDelegate, LogFilterProxy, LogModel
from .log_record import LogRecord
from .about_dialog import AboutDialog
from .bookmarks_dialog import BookmarksDialog
from .mem_dialog import MemDialog
from .net_dialog import NetDialog
from .packages_dialog import PackagesDialog
from .settings_dialog import SettingsDialog
from .stats import StatsTracker
from .themes import apply_theme
from .stats_dialog import StatsDialog
from .timeline_widget import TimelineWidget

# Font point sizes available via Ctrl+/Ctrl− zoom (index 3 = 9 pt = 100 %)
_ZOOM_SIZES = (6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 24)
_BASE_ZOOM_IDX = 3
_BASE_PT = _ZOOM_SIZES[_BASE_ZOOM_IDX]   # 9

# Style for toolbar buttons whose companion dialog is currently open.
_DIALOG_OPEN_STYLE = "color: #FFFFFF; background-color: #1565C0; font-weight: bold;"


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 ** 2:.1f} MB"


class MainWindow(QMainWindow):
    def __init__(
        self,
        initial_tag: str = "",
        initial_text: str = "",
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"Android Log Viewer - v{__version__}   LanDen Labs (2026)")
        self.resize(1440, 900)

        self._db = LogDatabase()          # in-memory SQLite
        self._db_buffer: List[LogRecord] = []   # pending DB writes, flushed at 100 rows
        self._reader: Optional[AdbReader] = None
        self._auto_scroll = True
        self._next_row_id = 1

        self._model = LogModel()
        self._proxy = LogFilterProxy()
        self._proxy.setSourceModel(self._model)

        self._settings = AppSettings.load()
        self._stats = StatsTracker()
        self._stats_dialog: Optional[StatsDialog] = None
        self._settings_dialog: Optional[SettingsDialog] = None
        self._mem_dialog: Optional[MemDialog] = None
        self._net_dialog: Optional[NetDialog] = None
        self._packages_dialog: Optional[PackagesDialog] = None
        self._colors_dialog: Optional[ColorsDialog] = None
        self._filter_view: Optional[FilterViewDialog] = None
        self._bookmarks_dialog: Optional[BookmarksDialog] = None
        self._selected_range: Optional[tuple] = None   # (from_key, to_key) or None
        self._range_filter_active: bool = False

        self._zoom_idx: int = _BASE_ZOOM_IDX   # current index into _ZOOM_SIZES
        self._recording: bool = False           # whether incoming rows go to the DB

        # Throttle stats-dialog auto-refresh to at most once per 2 s
        self._stats_refresh_timer = QTimer(self)
        self._stats_refresh_timer.setSingleShot(True)
        self._stats_refresh_timer.setInterval(2000)
        self._stats_refresh_timer.timeout.connect(self._refresh_stats_dialog_if_visible)

        # Debounce timeline filter rebuilds (text inputs fire on every keystroke)
        self._timeline_filter_timer = QTimer(self)
        self._timeline_filter_timer.setSingleShot(True)
        self._timeline_filter_timer.setInterval(300)
        self._timeline_filter_timer.timeout.connect(self._rebuild_timeline_filter)

        self._bg_pixmaps: dict[str, Optional[QPixmap]] = {}
        self._load_bg_pixmaps()

        # Maps a tracked dialog -> the toolbar button whose style reflects
        # that dialog's open/closed state. Populated lazily by _track_dialog.
        self._tracked_dialogs: dict[object, QPushButton] = {}

        self._build_ui()
        self._wire_signals()
        self._refresh_devices()

        # Apply loaded settings immediately so the proxy and button labels
        # reflect saved state without requiring the user to open the dialog.
        self._proxy.set_exclude_rules(self._settings.exclude_rules)
        self._model.set_merge_enabled(self._settings.merge_same_time_tag)
        self._update_settings_button_label()
        self._apply_color_config()
        apply_theme(self._settings.theme)
        # Row height must be re-applied after apply_theme — the theme's
        # QHeaderView::section padding can otherwise override the value set
        # in _build_ui, leaving Compact rows ignored until next toggle.
        self._table.verticalHeader().setDefaultSectionSize(
            16 if self._settings.compact_rows else 20
        )

        if initial_tag or initial_text:
            self._apply_initial_filters(initial_tag, initial_text)

        self._update_empty_overlay()
        QTimer.singleShot(0, self._check_adb_on_startup)

    # ================================================================== background image
    def _load_bg_pixmaps(self) -> None:
        from .resources import resource_path
        for name in ("bg-dark.jpg", "bg-light.jpg"):
            path = resource_path(name)
            self._bg_pixmaps[name] = QPixmap(str(path)) if path.exists() else None

    def _update_empty_overlay(self) -> None:
        if self._proxy.rowCount() > 0:
            self._empty_overlay.hide()
            return
        viewport = self._table.viewport()
        vp_size = viewport.size()
        img_name = "bg-dark.jpg" if self._settings.theme == "dark" else "bg-light.jpg"
        px = self._bg_pixmaps.get(img_name)
        if px and not px.isNull() and vp_size.width() > 0 and vp_size.height() > 0:
            scaled = px.scaled(vp_size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            self._empty_overlay.setPixmap(scaled)
        self._empty_overlay.setGeometry(viewport.rect())
        self._empty_overlay.show()
        self._empty_overlay.raise_()
        self._empty_overlay.update()

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        if obj is self._table.viewport() and event.type() == QEvent.Type.Resize:
            # Always recompute — gating on isVisible() can skip the very first
            # resize that fires before the visibility chain is fully established.
            self._update_empty_overlay()
        elif obj in self._tracked_dialogs and event.type() in (
            QEvent.Type.Show, QEvent.Type.Hide,
        ):
            self._refresh_dialog_button(obj)
        return super().eventFilter(obj, event)

    # ============================================================ dialog buttons
    def _track_dialog(self, dialog: object, button: "QPushButton") -> None:
        """Tie a dialog's visibility to a toolbar button's highlight style."""
        self._tracked_dialogs[dialog] = button
        dialog.installEventFilter(self)

    def _toggle_dialog(self, dialog) -> None:
        """Open if closed, close if open."""
        if dialog.isVisible():
            dialog.hide()
            return
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _refresh_dialog_button(self, dialog: object) -> None:
        btn = self._tracked_dialogs.get(dialog)
        if btn is None:
            return
        # Stats has a layered state (open + active filters); delegate to it.
        if btn is self._act_stats:
            self._update_stats_button_label()
            return
        # Menu actions don't support stylesheets — bold-italic font marks "open."
        if isinstance(btn, QAction):
            font = btn.font()
            font.setBold(dialog.isVisible())
            font.setItalic(dialog.isVisible())
            btn.setFont(font)
            return
        btn.setStyleSheet(_DIALOG_OPEN_STYLE if dialog.isVisible() else "")

    # ================================================================== build
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(0)  # Flush layout
        root.setContentsMargins(0, 0, 0, 0)

        self._build_toolbar()
        self._build_menu_bar()


        # Filter bar gets a slightly different background in CSS usually, 
        # but let's ensure it has some padding here.
        filter_container = QWidget()
        filter_container.setObjectName("filter_container")
        filter_layout = QVBoxLayout(filter_container)
        filter_layout.setContentsMargins(8, 4, 8, 4)
        filter_layout.addWidget(self._build_filter_bar())
        root.addWidget(filter_container)

        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(1)

        # -- log table
        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QTableView.SelectRows)
        self._table.setSelectionMode(QTableView.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        self._table.verticalHeader().setVisible(False)
        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.resizeSection(0, 180)  # timestamp
        hh.resizeSection(1, 60)   # pid
        hh.resizeSection(2, 60)   # tid
        hh.resizeSection(3, 40)   # level
        hh.resizeSection(4, 180)  # tag
        hh.resizeSection(COL_MSG, 2000)  # message — wide enough to trigger horizontal scroll
        hh.setSectionResizeMode(COL_MSG, QHeaderView.Interactive)
        self._table.setHorizontalScrollMode(QTableView.ScrollPerPixel)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._table.setAutoScroll(False)
        
        vh = self._table.verticalHeader()
        # Allow rows as small as 14px — the theme's QHeaderView::section
        # padding otherwise gives Qt a min section height around 20 and
        # silently clips setDefaultSectionSize(16) for compact rows.
        vh.setMinimumSectionSize(14)
        vh.setDefaultSectionSize(16 if self._settings.compact_rows else 20)
        vh.setSectionResizeMode(QHeaderView.Fixed)
        
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._highlight_delegate = HighlightDelegate(self._table)
        self._table.setItemDelegateForColumn(COL_TAG, self._highlight_delegate)
        self._table.setItemDelegateForColumn(COL_MSG, self._highlight_delegate)

        # Parent to viewport(), not the QTableView frame — otherwise the
        # viewport (and its stylesheet background) paints over the overlay.
        self._empty_overlay = QLabel(self._table.viewport())
        self._empty_overlay.setAlignment(Qt.AlignCenter)
        self._empty_overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._empty_overlay.hide()
        self._table.viewport().installEventFilter(self)

        splitter.addWidget(self._table)

        # -- timeline
        self._timeline = TimelineWidget()
        splitter.addWidget(self._timeline)
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter)

        # -- status bar
        sb = QStatusBar()
        sb.setSizeGripEnabled(False)
        self.setStatusBar(sb)
        self._lbl_conn = QLabel("Disconnected")
        self._lbl_conn.setMinimumWidth(110)
        self._lbl_total = QLabel("0 records")
        self._lbl_total.setMinimumWidth(100)
        self._lbl_shown = QLabel("0 shown")
        self._lbl_shown.setMinimumWidth(90)
        self._lbl_db_size = QLabel("DB: 0 B")
        self._lbl_db_size.setMinimumWidth(90)
        self._lbl_db_size.setToolTip("Approximate size of the in-memory log database")
        # Zoom control  [ − | 100% | + ]
        zoom_frame = QWidget()
        zoom_frame.setObjectName("zoom_frame")
        zl = QHBoxLayout(zoom_frame)
        zl.setContentsMargins(2, 2, 8, 2)
        zl.setSpacing(2)

        self._btn_zoom_out = QPushButton("−")   # U+2212 proper minus
        self._btn_zoom_out.setFixedSize(24, 24)
        self._btn_zoom_out.setToolTip("Zoom out  (Ctrl−)")
        zl.addWidget(self._btn_zoom_out)

        self._lbl_zoom = QPushButton("100%")
        self._lbl_zoom.setObjectName("zoom_pct")
        self._lbl_zoom.setFixedWidth(54)
        self._lbl_zoom.setFixedHeight(24)
        self._lbl_zoom.setToolTip("Reset to 100%  (Ctrl+0)")
        zl.addWidget(self._lbl_zoom)

        self._btn_zoom_in = QPushButton("+")
        self._btn_zoom_in.setFixedSize(24, 24)
        self._btn_zoom_in.setToolTip("Zoom in  (Ctrl+)")
        zl.addWidget(self._btn_zoom_in)

        # Time range label — left side of status bar (non-permanent, left-aligned)
        self._lbl_time_range = QLabel()
        self._lbl_time_range.setStyleSheet(
            "color: #1565C0; font-weight: bold; padding: 0 6px;"
        )
        self._lbl_time_range.setToolTip("Selected time range on the timeline")
        sb.addWidget(self._lbl_time_range)

        self._chk_autoscroll = QCheckBox("Scroll")
        self._chk_autoscroll.setChecked(True)
        self._chk_autoscroll.setToolTip("Auto-scroll to latest")
        sb.addPermanentWidget(self._chk_autoscroll)

        sb.addPermanentWidget(self._lbl_conn)
        sb.addPermanentWidget(self._lbl_total)
        sb.addPermanentWidget(self._lbl_shown)
        sb.addPermanentWidget(self._lbl_db_size)
        sb.addPermanentWidget(zoom_frame)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)

        self._btn_connect = QPushButton("Connect")
        self._btn_connect.setCheckable(True)
        self._btn_connect.setMinimumWidth(80)
        self._btn_connect.setToolTip("Start/Stop streaming logs")
        tb.addWidget(self._btn_connect)

        tb.addSeparator()
        tb.addWidget(QLabel(" Device: "))
        self._device_combo = QComboBox()
        self._device_combo.setMinimumWidth(140)
        tb.addWidget(self._device_combo)

        self._btn_refresh = QPushButton("⟳")
        self._btn_refresh.setFixedWidth(32)
        self._btn_refresh.setToolTip("Refresh device list")
        tb.addWidget(self._btn_refresh)

        tb.addSeparator()

        self._btn_record = QPushButton("○ REC")
        self._btn_record.setCheckable(True)
        self._btn_record.setChecked(False)
        self._btn_record.setFixedWidth(75)
        self._btn_record.setToolTip("Toggle recording to database")
        self._btn_record.setStyleSheet("color: #757575;")
        tb.addWidget(self._btn_record)

        self._btn_clear = QPushButton("Clear")
        self._btn_clear.setToolTip("Clear logs and device buffer")
        tb.addWidget(self._btn_clear)

        tb.addSeparator()

        # File / Tools menu actions — created here, placed on the menu bar by
        # _build_menu_bar(). Stats / Save still respond to setText() so their
        # dynamic-label code keeps working.
        self._act_save = QAction("Save…", self)
        self._act_open = QAction("Open…", self)
        self._act_stats = QAction("Stats", self)
        self._act_memory = QAction("Mem", self)
        self._act_network = QAction("Net", self)
        self._act_packages = QAction("Pkgs", self)
        self._act_packages.setToolTip("List installed 3rd-party packages")

        self._btn_filter_view = QPushButton("View")
        self._btn_filter_view.setToolTip("Open a second filtered log view")
        tb.addWidget(self._btn_filter_view)

        self._btn_bookmarks = QPushButton("Bookmarks")
        self._btn_bookmarks.setToolTip(
            "Manage bookmarks (Ctrl+B to toggle bookmark on current row)"
        )
        tb.addWidget(self._btn_bookmarks)

        self._btn_colors = QPushButton("Colors")
        tb.addWidget(self._btn_colors)

        self._btn_settings = QPushButton("Config")
        tb.addWidget(self._btn_settings)

        # Push ? button to the far right
        _spacer = QWidget()
        _spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(_spacer)

        self._btn_about = QPushButton("?")
        self._btn_about.setFixedWidth(32)
        tb.addWidget(self._btn_about)

        # keyboard shortcuts
        QAction(self).setShortcut(QKeySequence.Save)

    def _build_menu_bar(self) -> None:
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")
        file_menu.addAction(self._act_open)
        file_menu.addAction(self._act_save)

        tools_menu = mb.addMenu("&Tools")
        tools_menu.addAction(self._act_stats)
        tools_menu.addAction(self._act_memory)
        tools_menu.addAction(self._act_network)
        tools_menu.addAction(self._act_packages)

    def _build_filter_bar(self) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        row = QHBoxLayout(frame)
        row.setContentsMargins(6, 3, 6, 3)
        row.setSpacing(6)

        row.addWidget(QLabel("Level:"))
        self._level_cbs: dict[str, QCheckBox] = {}
        for lvl in LEVELS:
            cb = QCheckBox(lvl)
            cb.setChecked(lvl in self._settings.level_filters)
            cb.setToolTip(LEVEL_NAMES[lvl])
            self._level_cbs[lvl] = cb
            row.addWidget(cb)

        row.addSpacing(12)

        row.addWidget(QLabel("Tag:"))
        self._tag_edit = QLineEdit()
        self._tag_edit.setPlaceholderText("regex…")
        self._tag_edit.setClearButtonEnabled(True)
        self._tag_edit.setToolTip(
            "Filter by tag name (regex, case-insensitive).\n"
            "Shortcut: Ctrl+L"
        )
        row.addWidget(self._tag_edit, stretch=1)

        row.addWidget(QLabel("Text:"))
        self._text_edit = QLineEdit()
        self._text_edit.setPlaceholderText("regex…")
        self._text_edit.setClearButtonEnabled(True)
        self._text_edit.setToolTip(
            "Filter by message text or tag (regex, case-insensitive).\n"
            "Shortcut: Ctrl+F"
        )
        row.addWidget(self._text_edit, stretch=1)

        self._btn_clear_filters = QPushButton("✕ Filters")
        self._btn_clear_filters.setToolTip("Reset all level, tag, and text filters")
        row.addWidget(self._btn_clear_filters)

        self._btn_show_range = QPushButton("Show Range")
        self._btn_show_range.setToolTip(
            "Filter the log list to the selected time range\n"
            "and disable auto-scroll"
        )
        self._btn_show_range.setStyleSheet("color: #1565C0; font-weight: bold;")
        self._btn_show_range.setVisible(False)
        row.addWidget(self._btn_show_range)

        self._btn_clear_range = QPushButton("Clear Range")
        self._btn_clear_range.setToolTip(
            "Remove the time range filter and restore the full log list"
        )
        self._btn_clear_range.setVisible(False)
        row.addWidget(self._btn_clear_range)

        return frame

    # ================================================================== wiring
    def _wire_signals(self) -> None:
        self._btn_refresh.clicked.connect(self._refresh_devices)
        self._btn_connect.toggled.connect(self._on_connect_toggled)
        self._btn_clear.clicked.connect(self._clear_logs)
        self._act_save.triggered.connect(self._save_logs)
        self._act_open.triggered.connect(self._open_logs)
        self._btn_clear_filters.clicked.connect(self._clear_filters)
        self._chk_autoscroll.toggled.connect(self._on_autoscroll_toggled)

        for cb in self._level_cbs.values():
            cb.toggled.connect(self._on_filter_changed)

        self._tag_edit.textChanged.connect(self._proxy.set_tag_filter)
        self._tag_edit.textChanged.connect(self._update_status)
        self._tag_edit.textChanged.connect(self._schedule_timeline_filter_rebuild)
        self._text_edit.textChanged.connect(self._proxy.set_text_filter)
        self._text_edit.textChanged.connect(self._update_status)
        self._text_edit.textChanged.connect(self._schedule_timeline_filter_rebuild)

        self._proxy.rowsInserted.connect(self._on_proxy_rows_inserted)
        self._proxy.modelReset.connect(self._update_status)

        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.doubleClicked.connect(self._on_row_double_clicked)
        self._table.verticalScrollBar().sliderMoved.connect(self._on_manual_scroll)

        self._timeline.timestamp_selected.connect(self._jump_to_timestamp)
        self._timeline.range_selected.connect(self._on_range_selected)
        self._table.selectionModel().currentChanged.connect(self._on_table_row_selected)
        self._btn_show_range.clicked.connect(self._on_show_range)
        self._btn_clear_range.clicked.connect(self._on_clear_range)

        self._act_stats.triggered.connect(self._show_stats_dialog)
        self._act_memory.triggered.connect(self._show_mem_dialog)
        self._act_network.triggered.connect(self._show_net_dialog)
        self._act_packages.triggered.connect(self._show_packages_dialog)
        self._btn_filter_view.clicked.connect(self._show_filter_view)
        self._btn_bookmarks.clicked.connect(self._show_bookmarks_dialog)
        self._btn_settings.clicked.connect(self._show_settings_dialog)
        self._btn_colors.clicked.connect(self._show_colors_dialog)
        self._btn_about.clicked.connect(self._show_about_dialog)
        self._btn_record.toggled.connect(self._on_record_toggled)
        self._btn_zoom_out.clicked.connect(self._zoom_out)
        self._btn_zoom_in.clicked.connect(self._zoom_in)
        self._lbl_zoom.clicked.connect(self._zoom_reset)

        # keyboard shortcuts
        save_action = QAction("Save", self)
        save_action.setShortcut(QKeySequence("Ctrl+S"))
        save_action.triggered.connect(self._save_logs)
        self.addAction(save_action)

        open_action = QAction("Open", self)
        open_action.setShortcut(QKeySequence("Ctrl+O"))
        open_action.triggered.connect(self._open_logs)
        self.addAction(open_action)

        tag_focus = QAction("Focus tag filter", self)
        tag_focus.setShortcut(QKeySequence("Ctrl+L"))
        tag_focus.triggered.connect(self._tag_edit.setFocus)
        self.addAction(tag_focus)

        text_focus = QAction("Focus text filter", self)
        text_focus.setShortcut(QKeySequence("Ctrl+F"))
        text_focus.triggered.connect(self._text_edit.setFocus)
        self.addAction(text_focus)

        copy_rows_action = QAction("Copy Selected Rows", self._table)
        copy_rows_action.setShortcut(QKeySequence("Ctrl+C"))
        copy_rows_action.triggered.connect(self._copy_selected_rows)
        self._table.addAction(copy_rows_action)

        bookmark_action = QAction("Toggle bookmark", self)
        bookmark_action.setShortcut(QKeySequence("Ctrl+B"))
        bookmark_action.triggered.connect(self._toggle_bookmark_current_row)
        self.addAction(bookmark_action)

        # Zoom shortcuts  (Ctrl++  Ctrl+=  Ctrl−  Ctrl+0)
        for keys, slot in [
            ("Ctrl++", self._zoom_in),
            ("Ctrl+=", self._zoom_in),
            ("Ctrl+-", self._zoom_out),
            ("Ctrl+0", self._zoom_reset),
        ]:
            act = QAction(self)
            act.setShortcut(QKeySequence(keys))
            act.triggered.connect(slot)
            self.addAction(act)

    # ================================================================== initial filters / auto-connect
    def _apply_initial_filters(self, tag: str, text: str) -> None:
        if tag:
            self._tag_edit.setText(tag)
        if text:
            self._text_edit.setText(text)
        # Auto-connect when exactly one real device is present
        current = self._device_combo.currentText()
        if self._device_combo.count() == 1 and not current.startswith("("):
            QTimer.singleShot(0, lambda: self._btn_connect.setChecked(True))

    # ================================================================== adb helpers
    def _adb_exe(self) -> str:
        """Return the resolved adb executable path to use for all subprocess calls."""
        return find_adb_on_path(self._settings.adb_path) or resolve_adb(self._settings.adb_path)

    def _check_adb_on_startup(self) -> None:
        """Called once after the main window is shown. Opens settings with an error
        banner if the configured (or default) adb executable cannot be found."""
        ok, msg = check_adb(self._settings.adb_path)
        if not ok:
            self._show_settings_dialog()
            if self._settings_dialog:
                self._settings_dialog.show_adb_error(msg)

    # ================================================================== device
    def _refresh_devices(self) -> None:
        self._device_combo.clear()
        devices = list_devices(adb_exe=self._adb_exe())
        if devices:
            for d in devices:
                self._device_combo.addItem(d)
        else:
            self._device_combo.addItem("(no device)")

    # ================================================================== zoom
    def _zoom_in(self) -> None:
        if self._zoom_idx < len(_ZOOM_SIZES) - 1:
            self._zoom_idx += 1
            self._set_font_size(_ZOOM_SIZES[self._zoom_idx])

    def _zoom_out(self) -> None:
        if self._zoom_idx > 0:
            self._zoom_idx -= 1
            self._set_font_size(_ZOOM_SIZES[self._zoom_idx])

    def _zoom_reset(self) -> None:
        self._zoom_idx = _BASE_ZOOM_IDX
        self._set_font_size(_ZOOM_SIZES[self._zoom_idx])

    def _set_font_size(self, pt: int) -> None:
        self._model.set_font_size(pt)
        row_h = max(14, round(18 * pt / _BASE_PT))
        vh = self._table.verticalHeader()
        vh.setDefaultSectionSize(row_h)
        for i in range(self._model.rowCount()):
            vh.resizeSection(i, row_h)
        pct = round(100 * pt / _BASE_PT)
        self._lbl_zoom.setText(f"{pct}%")

    # ================================================================== record
    def _on_record_toggled(self, checked: bool) -> None:
        self._recording = checked
        if checked:
            self._btn_record.setText("● REC")
            self._btn_record.setStyleSheet("color: #C62828; font-weight: bold;")
            self.statusBar().showMessage("Recording resumed — new logs will be saved.", 3000)
        else:
            self._btn_record.setText("○ REC")
            self._btn_record.setStyleSheet("color: #757575;")
            self.statusBar().showMessage("Recording paused — logs are shown but not saved.", 3000)

    # ================================================================== capture
    def _on_connect_toggled(self, checked: bool) -> None:
        if checked:
            self._start_capture()
        else:
            self._stop_capture()

    def _start_capture(self) -> None:
        text = self._device_combo.currentText()
        device: Optional[str] = text if not text.startswith("(") else None

        self._reader = AdbReader(
            device=device,
            buffers=self._settings.buffers,
            exclude_rules=self._settings.exclude_rules,
            adb_exe=self._adb_exe(),
            parent=self,
        )
        self._reader.records_ready.connect(self._on_records_ready)
        self._reader.error_occurred.connect(self._on_reader_error)
        self._reader.started_reading.connect(lambda: self._lbl_conn.setText("● Connected"))
        self._reader.stopped_reading.connect(self._on_reader_stopped)
        self._reader.start()
        self._btn_connect.setText("Disconnect")

    def _stop_capture(self) -> None:
        if self._reader:
            self._reader.stop()
            self._reader.wait(3000)
            self._reader = None
        self._flush_db_buffer()           # write any trailing rows
        self._mark_disconnected()

    def _on_reader_error(self, msg: str) -> None:
        self._reader = None       # thread has already finished
        self._flush_db_buffer()
        if "Could not find 'adb'" in msg:
            # Fatal: adb binary missing — stop and tell the user
            self._mark_disconnected()
            QMessageBox.critical(self, "ADB Error", msg)
        else:
            # Transient error (decode glitch, pipe reset, etc.) — reconnect
            self._schedule_reconnect(msg)

    def _on_reader_stopped(self) -> None:
        if self._reader is not None:
            # Thread finished without us calling stop() — unexpected disconnect
            self._reader = None
            self._flush_db_buffer()
            if self._btn_connect.isChecked():
                self._schedule_reconnect("Connection lost")

    def _schedule_reconnect(self, reason: str) -> None:
        self._lbl_conn.setText("⟳ Reconnecting…")
        self.statusBar().showMessage(
            f"Reconnecting in 2 s  ({reason})", 4000
        )
        QTimer.singleShot(2000, self._try_reconnect)

    def _try_reconnect(self) -> None:
        if self._btn_connect.isChecked() and self._reader is None:
            self._start_capture()

    def _mark_disconnected(self) -> None:
        """Update UI to Disconnected state without touching the reader."""
        self._lbl_conn.setText("Disconnected")
        self._btn_connect.blockSignals(True)
        self._btn_connect.setChecked(False)
        self._btn_connect.setText("Connect")
        self._btn_connect.blockSignals(False)

    # ================================================================== records
    def _flush_db_buffer(self) -> None:
        if self._db_buffer:
            self._db.insert_batch(self._db_buffer)
            self._db_buffer.clear()

            # Maintain DB record limit
            count = self._db.count()
            if count > MAX_RECORDS:
                self._db.prune_oldest(count - MAX_RECORDS + PRUNE_SIZE)

    def _on_records_ready(self, records: List[LogRecord]) -> None:
        # Assign sequential IDs
        for rec in records:
            rec.row_id = self._next_row_id
            self._next_row_id += 1

        # --- UI path: update immediately on every emission ---
        self._model.append_records(records)
        self._timeline.add_records(records)
        if self._filter_view is not None:
            self._filter_view.add_records(records)
        if self._settings.timeline_follows_filter and self._timeline._filtered_buckets is not None:
            filtered_new = [r for r in records if self._proxy.accepts_for_timeline(r)]
            if filtered_new:
                self._timeline.add_filtered_records(filtered_new)
        self._stats.update(records)
        self._update_status()

        if self._stats_dialog and self._stats_dialog.isVisible():
            if not self._stats_refresh_timer.isActive():
                self._stats_refresh_timer.start()

        # --- DB path: buffer writes only when recording is active ---
        if self._recording:
            self._db_buffer.extend(records)
            if len(self._db_buffer) >= 100:
                self._flush_db_buffer()

    def _on_proxy_rows_inserted(self) -> None:
        self._update_status()
        if self._auto_scroll:
            self._table.scrollToBottom()

    # ================================================================== filters
    def _on_filter_changed(self) -> None:
        levels = {lvl for lvl, cb in self._level_cbs.items() if cb.isChecked()}
        self._proxy.set_levels(levels)
        self._settings.level_filters = levels
        self._settings.save()
        self._update_status()
        self._schedule_timeline_filter_rebuild()

    def _clear_filters(self) -> None:
        for cb in self._level_cbs.values():
            cb.setChecked(True)
        self._tag_edit.clear()
        self._text_edit.clear()

    # ================================================================== timeline filter
    def _schedule_timeline_filter_rebuild(self) -> None:
        if not self._timeline_filter_timer.isActive():
            self._timeline_filter_timer.start()

    def _has_active_timeline_filter(self) -> bool:
        p = self._proxy
        all_levels = {"V", "D", "I", "W", "E", "F"}
        return bool(
            p._allowed != all_levels
            or p._tag_rx is not None
            or p._text_rx is not None
            or p._pid_set
            or p._tag_set
            or p._exclude_rules
        )

    def _rebuild_timeline_filter(self) -> None:
        if not self._settings.timeline_follows_filter or not self._has_active_timeline_filter():
            self._timeline.set_filtered_records(None)
            return
        filtered = [
            r for r in self._model.all_records()
            if not r.is_sub_row and self._proxy.accepts_for_timeline(r)
        ]
        self._timeline.set_filtered_records(filtered)

    # ================================================================== scroll
    def _on_autoscroll_toggled(self, checked: bool) -> None:
        self._auto_scroll = checked

    def _on_manual_scroll(self) -> None:
        sb = self._table.verticalScrollBar()
        if sb.value() < sb.maximum():
            self._auto_scroll = False
            self._chk_autoscroll.blockSignals(True)
            self._chk_autoscroll.setChecked(False)
            self._chk_autoscroll.blockSignals(False)

    def _jump_to_timestamp(self, ts: str) -> None:
        src_row = self._model.find_row_for_timestamp(ts)
        src_idx = self._model.index(src_row, 0)
        proxy_idx = self._proxy.mapFromSource(src_idx)
        if proxy_idx.isValid():
            self._table.scrollTo(proxy_idx, QTableView.PositionAtTop)
            self._table.selectRow(proxy_idx.row())

    def _on_table_row_selected(self, current: QModelIndex, previous: QModelIndex) -> None:
        if not current.isValid():
            return
        rec: Optional[LogRecord] = self._proxy.data(
            self._proxy.index(current.row(), 0), Qt.UserRole
        )
        if rec:
            self._timeline.set_cursor_key(rec.timestamp[:17])

    def _on_row_double_clicked(self, proxy_idx: QModelIndex) -> None:
        if not proxy_idx.isValid():
            return
        src_idx = self._proxy.mapToSource(proxy_idx)
        rec = self._model.record_at(src_idx.row())
        if rec and not rec.is_sub_row and rec.sub_messages and len(rec.sub_messages) > 1:
            self._model.toggle_expand(src_idx.row())

    # ================================================================== time range
    def _on_range_selected(self, from_key: str, to_key: str) -> None:
        """Called continuously while dragging and once on release."""
        self._selected_range = (from_key, to_key)
        from_time = from_key[6:]   # "HH:MM:SS"
        to_time = to_key[6:]
        self._lbl_time_range.setText(f"⏱  {from_time} – {to_time}")
        self._btn_show_range.setVisible(True)
        self._btn_clear_range.setVisible(True)

    def _on_show_range(self) -> None:
        if not self._selected_range:
            return
        from_key, to_key = self._selected_range
        self._proxy.set_time_range(from_key, to_key)
        self._range_filter_active = True
        self._auto_scroll = False
        self._chk_autoscroll.blockSignals(True)
        self._chk_autoscroll.setChecked(False)
        self._chk_autoscroll.blockSignals(False)
        self._update_status()
        self._btn_show_range.setStyleSheet(
            "color: #FFFFFF; background-color: #1565C0; font-weight: bold;"
        )

    def _on_clear_range(self) -> None:
        # Capture the source index of the top-visible row before changing the filter
        top_proxy_idx = self._table.indexAt(QPoint(0, 0))
        src_idx = (
            self._proxy.mapToSource(top_proxy_idx)
            if top_proxy_idx.isValid()
            else QModelIndex()
        )

        self._proxy.set_time_range(None, None)
        self._range_filter_active = False
        self._selected_range = None
        self._timeline.clear_range()
        self._lbl_time_range.setText("")
        self._btn_show_range.setVisible(False)
        self._btn_clear_range.setVisible(False)
        self._btn_show_range.setStyleSheet("color: #1565C0; font-weight: bold;")
        self._update_status()

        # Try to keep the same row visible at the top
        if src_idx.isValid():
            new_proxy_idx = self._proxy.mapFromSource(src_idx)
            if new_proxy_idx.isValid():
                self._table.scrollTo(new_proxy_idx, QTableView.PositionAtTop)

    # ================================================================== context menu
    def _show_context_menu(self, pos) -> None:
        idx = self._table.indexAt(pos)
        if not idx.isValid():
            return
        rec: Optional[LogRecord] = self._proxy.data(
            self._proxy.index(idx.row(), 0), Qt.UserRole
        )
        if rec is None:
            return

        selected_rows = sorted({i.row() for i in self._table.selectedIndexes()})

        menu = QMenu(self)
        _copy_msg = rec.sub_messages[0] if rec.sub_messages and not rec.is_sub_row else rec.message
        menu.addAction("Copy row", lambda: QApplication.clipboard().setText(
            f"{rec.timestamp}  {rec.pid:>6}  {rec.tid:>6}  {rec.level}  {rec.tag}: {_copy_msg}"
        ))
        if len(selected_rows) > 1:
            menu.addAction(
                f"Copy {len(selected_rows)} selected rows",
                self._copy_selected_rows,
            )
        menu.addAction("Copy message", lambda: QApplication.clipboard().setText(rec.message))
        menu.addSeparator()
        if rec.level == "B":
            menu.addAction(
                "Remove bookmark\tCtrl+B",
                lambda rid=rec.row_id: self._remove_bookmark_for_row_id(rid),
            )
        else:
            src_idx = self._proxy.mapToSource(self._proxy.index(idx.row(), 0))
            src_row = src_idx.row()
            menu.addAction(
                "Add bookmark\tCtrl+B",
                lambda sr=src_row: self._add_bookmark_at_source_row(sr),
            )
        menu.addSeparator()
        menu.addAction(
            f"Filter by tag: {rec.tag}",
            lambda t=rec.tag: self._tag_edit.setText(re_escape_tag(t)),
        )
        menu.addAction(
            "Exclude this tag",
            lambda t=rec.tag: self._tag_edit.setText(f"^((?!{re_escape_tag(t)}).)*$"),
        )
        menu.addAction(
            f"Save exclude rule for tag: {rec.tag}",
            lambda t=rec.tag: self._add_exclude_rule_from_tag(t),
        )
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _copy_selected_rows(self) -> None:
        selected_rows = sorted({i.row() for i in self._table.selectedIndexes()})
        lines = []
        for row in selected_rows:
            rec: Optional[LogRecord] = self._proxy.data(
                self._proxy.index(row, 0), Qt.UserRole
            )
            if rec:
                if rec.is_sub_row:
                    lines.append(f"  {rec.message}")
                else:
                    msg = rec.sub_messages[0] if rec.sub_messages else rec.message
                    lines.append(
                        f"{rec.timestamp}  {rec.pid:>6}  {rec.tid:>6}  {rec.level}  {rec.tag}: {msg}"
                    )
        if lines:
            QApplication.clipboard().setText("\n".join(lines))

    def _add_exclude_rule_from_tag(self, tag: str) -> None:
        from .app_settings import ExcludeRule
        pattern = f"^{_re.escape(tag)}$"
        if any(r.pattern == pattern and r.field == "TAG" for r in self._settings.exclude_rules):
            self.statusBar().showMessage(f"Exclude rule already exists for tag: {tag}", 3000)
            return
        self._settings.exclude_rules.append(ExcludeRule(pattern=pattern, field="TAG", enabled=True))
        self._settings.save()
        self._proxy.set_exclude_rules(self._settings.exclude_rules)
        self._update_settings_button_label()
        if self._colors_dialog and self._colors_dialog.isVisible():
            self._colors_dialog._load()
        self._rebuild_timeline_filter()
        self.statusBar().showMessage(f"Exclude rule added for tag: {tag}", 3000)

    # ================================================================== status
    def _update_status(self) -> None:
        total = self._model.rowCount()
        shown = self._proxy.rowCount()
        self._lbl_total.setText(f"{total:,} records")
        self._lbl_shown.setText(f"{shown:,} shown")
        self._lbl_db_size.setText(f"DB: {_fmt_bytes(self._db.size_bytes())}")
        self._update_empty_overlay()

    # ================================================================== clear
    def _clear_logs(self) -> None:
        # 1. Wipe in-app state
        self._db_buffer.clear()
        self._model.clear()
        self._refresh_bookmarks_dialog_if_visible()
        self._db.clear()
        self._next_row_id = 1
        self._timeline.reset([])
        if self._filter_view is not None:
            self._filter_view.reset_timeline([])
        self._stats.reset()
        self._proxy.set_pid_filter(set())
        self._proxy.set_tag_set_filter(set())
        self._proxy.set_time_range(None, None)
        self._selected_range = None
        self._range_filter_active = False
        self._lbl_time_range.setText("")
        self._btn_show_range.setVisible(False)
        self._btn_clear_range.setVisible(False)
        self._btn_show_range.setStyleSheet("color: #1565C0; font-weight: bold;")
        if self._stats_dialog:
            self._stats_dialog._active_pids = set()
            self._stats_dialog._active_tags = set()
            self._stats_dialog.refresh(self._stats)
            self._stats_dialog._update_status_label()
        self._update_stats_button_label()
        self._update_status()

        # 2. Also clear the device-side ring buffer so old logs don't replay
        #    on the next connect.  Fire-and-forget; errors are silent.
        self._clear_device_logbuf()

    def _clear_device_logbuf(self) -> None:
        """Run 'adb logcat -c' in the background to flush the device ring buffer."""
        import subprocess as _sp
        device_text = self._device_combo.currentText()
        cmd = [self._adb_exe()]
        if not device_text.startswith("("):
            cmd += ["-s", device_text]
        cmd += ["logcat", "-c"]
        try:
            _sp.Popen(cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        except Exception:
            pass   # adb not on PATH or no device — silently ignore

    # ================================================================== save/open
    def _save_logs(self) -> None:
        path, filt = QFileDialog.getSaveFileName(
            self,
            "Save Logs",
            "",
            "SQLite database (*.db);;Text file (*.txt *.log)",
        )
        if not path:
            return
        if path.endswith(".db"):
            if not self._recording:
                db_count = self._db.count() + len(self._db_buffer)
                view_count = self._model.rowCount()
                msg = QMessageBox(self)
                msg.setWindowTitle("Record is Off")
                msg.setIcon(QMessageBox.Warning)
                msg.setText(
                    f"Record is currently <b>off</b>.<br><br>"
                    f"The .db file will only contain <b>{db_count:,} recorded row(s)</b>.<br>"
                    f"The current view has <b>{view_count:,} row(s)</b> total.<br><br>"
                    f"Save as <b>.txt</b> to export everything you see."
                )
                btn_db  = msg.addButton("Save .db anyway", QMessageBox.AcceptRole)
                btn_txt = msg.addButton("Save as .txt",    QMessageBox.ActionRole)
                msg.addButton("Cancel",                    QMessageBox.RejectRole)
                msg.exec()
                clicked = msg.clickedButton()
                if clicked is btn_txt:
                    txt_path = path[:-3] + ".txt"
                    self._save_as_text(txt_path)
                    return
                elif clicked is not btn_db:
                    return
            self._flush_db_buffer()
            self._db.save_to_file(path)
        else:
            self._save_as_text(path)

    def _save_as_text(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for row in range(self._proxy.rowCount()):
                rec: Optional[LogRecord] = self._proxy.data(
                    self._proxy.index(row, 0), Qt.UserRole
                )
                if rec:
                    fh.write(
                        f"{rec.timestamp}  {rec.pid:>6}  {rec.tid:>6}  "
                        f"{rec.level}  {rec.tag}: {rec.message}\n"
                    )

    def _open_logs(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Logs",
            "",
            "All supported (*.db *.txt *.log);;SQLite (*.db);;Text (*.txt *.log);;All (*)",
        )
        if not path:
            return
        try:
            if path.endswith(".db"):
                records = self._db.load_from_file(path)
            else:
                records = _load_text_file(path)

            self._db_buffer.clear()           # drop unflushed rows before replacing DB
            self._model.clear()
            self._refresh_bookmarks_dialog_if_visible()
            self._db.clear()
            self._next_row_id = 1
            self._stats.reset()
            self._proxy.set_pid_filter(set())
            self._proxy.set_tag_set_filter(set())
            self._proxy.set_time_range(None, None)
            self._selected_range = None
            self._range_filter_active = False
            self._lbl_time_range.setText("")
            self._btn_show_range.setVisible(False)
            self._btn_clear_range.setVisible(False)
            self._btn_show_range.setStyleSheet("color: #1565C0; font-weight: bold;")
            for rec in records:
                rec.row_id = self._next_row_id
                self._next_row_id += 1
            self._db.insert_batch(records)
            self._model.append_records(records)
            self._timeline.reset(records)
            if self._filter_view is not None:
                self._filter_view.reset_timeline(records)
            self._stats.update(records)
            if self._stats_dialog:
                self._stats_dialog._active_pids = set()
                self._stats_dialog._active_tags = set()
                self._stats_dialog.refresh(self._stats)
                self._stats_dialog._update_status_label()
            self._update_stats_button_label()
            self._update_status()
            self._rebuild_timeline_filter()
        except Exception as exc:
            QMessageBox.critical(self, "Open Error", str(exc))

    # ================================================================== settings dialog

    def _show_settings_dialog(self) -> None:
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(self._settings, parent=self)
            self._settings_dialog.settings_applied.connect(self._apply_settings)
        device_text = self._device_combo.currentText()
        device = device_text if not device_text.startswith("(") else None
        self._settings_dialog.set_device(device)
        self._settings_dialog._load()   # sync from current settings each time shown
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _apply_settings(self) -> None:
        apply_theme(self._settings.theme)
        self._proxy.set_exclude_rules(self._settings.exclude_rules)
        self._model.set_merge_enabled(self._settings.merge_same_time_tag)
        self._table.verticalHeader().setDefaultSectionSize(
            16 if self._settings.compact_rows else 20
        )
        self._settings.save()
        self._update_settings_button_label()
        self._update_status()
        self._update_empty_overlay()
        self._rebuild_timeline_filter()
        if self._filter_view is not None:
            self._filter_view.apply_exclude_rules(self._settings.exclude_rules)
        self._refresh_devices()   # pick up any adb path change
        if self._reader:
            self.statusBar().showMessage(
                "Buffer changes will take effect on the next Connect.", 5000
            )

    def _update_settings_button_label(self) -> None:
        active = sum(1 for r in self._settings.exclude_rules if r.enabled and r.pattern)
        if active:
            self._btn_settings.setText(f"Settings [{active}]")
            self._btn_settings.setStyleSheet("color: #E65100;")
        else:
            self._btn_settings.setText("Settings")
            self._btn_settings.setStyleSheet("")

    # ================================================================== stats dialog
    def _show_stats_dialog(self) -> None:
        if self._stats_dialog is None:
            self._stats_dialog = StatsDialog(parent=self)
            self._stats_dialog.filter_applied.connect(self._on_stats_filter_applied)
            self._track_dialog(self._stats_dialog, self._act_stats)
        if not self._stats_dialog.isVisible():
            device_text = self._device_combo.currentText()
            self._stats_dialog.set_device(
                device_text if not device_text.startswith("(") else None
            )
            self._stats_dialog.set_adb_exe(self._adb_exe())
            self._stats_dialog.refresh(self._stats)
        self._toggle_dialog(self._stats_dialog)

    def _refresh_stats_dialog_if_visible(self) -> None:
        if self._stats_dialog and self._stats_dialog.isVisible():
            self._stats_dialog.refresh(self._stats)

    def _on_stats_filter_applied(self, pids: Set[str], tags: Set[str]) -> None:
        self._proxy.set_pid_filter(pids)
        self._proxy.set_tag_set_filter(tags)
        self._update_stats_button_label()
        self._update_status()
        self._rebuild_timeline_filter()

    def _update_stats_button_label(self) -> None:
        pids = self._stats_dialog.active_pids if self._stats_dialog else set()
        tags = self._stats_dialog.active_tags if self._stats_dialog else set()
        if pids or tags:
            parts = []
            if pids:
                parts.append(f"{len(pids)}P")
            if tags:
                parts.append(f"{len(tags)}T")
            self._act_stats.setText(f"Stats [{'+'.join(parts)}]")
        else:
            self._act_stats.setText("Stats")
        # QAction in a menu — express "open" / "filter-active" via font weight.
        font = self._act_stats.font()
        is_open = self._stats_dialog is not None and self._stats_dialog.isVisible()
        font.setBold(bool(is_open or pids or tags))
        font.setItalic(bool(is_open))
        self._act_stats.setFont(font)

    # ================================================================== colors dialog
    def _show_colors_dialog(self) -> None:
        if self._colors_dialog is None:
            self._colors_dialog = ColorsDialog(self._settings, parent=self)
            self._colors_dialog.colors_applied.connect(self._apply_color_config)
            self._track_dialog(self._colors_dialog, self._btn_colors)
        if not self._colors_dialog.isVisible():
            self._colors_dialog._load()
        self._toggle_dialog(self._colors_dialog)

    def _apply_color_config(self) -> None:
        self._model.set_color_config(
            self._settings.level_fg,
            self._settings.level_bg,
            self._settings.color_rules,
        )

    # ================================================================== about dialog
    def _show_about_dialog(self) -> None:
        AboutDialog(parent=self).exec()

    # ================================================================== memory dialog
    def _show_mem_dialog(self) -> None:
        if self._mem_dialog is None:
            self._mem_dialog = MemDialog(parent=self)
            self._track_dialog(self._mem_dialog, self._act_memory)
        if not self._mem_dialog.isVisible():
            device_text = self._device_combo.currentText()
            self._mem_dialog.set_device(
                device_text if not device_text.startswith("(") else None
            )
            self._mem_dialog.set_adb_exe(self._adb_exe())
        self._toggle_dialog(self._mem_dialog)

    # ================================================================== bookmarks
    def _show_bookmarks_dialog(self) -> None:
        if self._bookmarks_dialog is None:
            self._bookmarks_dialog = BookmarksDialog(self._model, self._proxy, parent=self)
            self._bookmarks_dialog.jump_to_row_id.connect(self._jump_to_row_id)
            self._track_dialog(self._bookmarks_dialog, self._btn_bookmarks)
        if not self._bookmarks_dialog.isVisible():
            self._bookmarks_dialog.refresh()
        self._toggle_dialog(self._bookmarks_dialog)

    def _refresh_bookmarks_dialog_if_visible(self) -> None:
        if self._bookmarks_dialog is not None and self._bookmarks_dialog.isVisible():
            self._bookmarks_dialog.refresh()

    def _toggle_bookmark_current_row(self) -> None:
        idx = self._table.currentIndex()
        if not idx.isValid():
            return
        rec: Optional[LogRecord] = self._proxy.data(
            self._proxy.index(idx.row(), 0), Qt.UserRole
        )
        if rec is None:
            return
        if rec.level == "B":
            self._remove_bookmark_for_row_id(rec.row_id)
        else:
            src_row = self._proxy.mapToSource(self._proxy.index(idx.row(), 0)).row()
            self._add_bookmark_at_source_row(src_row)

    def _add_bookmark_at_source_row(self, source_row: int) -> None:
        row_id = self._next_row_id
        self._next_row_id += 1
        new_row = self._model.insert_bookmark_after(source_row, row_id)
        if new_row < 0:
            return
        self._refresh_bookmarks_dialog_if_visible()
        self.statusBar().showMessage("Bookmark added.", 2000)

    def _remove_bookmark_for_row_id(self, row_id: int) -> None:
        if self._model.remove_bookmark_row(row_id):
            self._refresh_bookmarks_dialog_if_visible()
            self.statusBar().showMessage("Bookmark removed.", 2000)

    def _jump_to_row_id(self, row_id: int) -> None:
        src_row = self._model.find_row_for_row_id(row_id)
        if src_row < 0:
            self.statusBar().showMessage("Bookmarked row is no longer in the log.", 3000)
            return
        proxy_idx = self._proxy.mapFromSource(self._model.index(src_row, 0))
        if not proxy_idx.isValid():
            self.statusBar().showMessage(
                "Bookmarked row is hidden by the current filter.", 3000
            )
            return
        # Disable autoscroll so the user stays at the jumped-to position.
        if self._auto_scroll:
            self._chk_autoscroll.setChecked(False)   # toggled signal updates _auto_scroll
        self._table.scrollTo(proxy_idx, QTableView.PositionAtCenter)
        self._table.selectRow(proxy_idx.row())
        self._table.setFocus()

    # ================================================================== filter view dialog
    def _show_filter_view(self) -> None:
        if self._filter_view is None:
            self._filter_view = FilterViewDialog(self._model, self._settings, parent=self)
            self._track_dialog(self._filter_view, self._btn_filter_view)
        if not self._filter_view.isVisible():
            device_text = self._device_combo.currentText()
            self._filter_view.set_device(
                device_text if not device_text.startswith("(") else None
            )
        self._toggle_dialog(self._filter_view)

    # ================================================================== network dialog
    def _show_net_dialog(self) -> None:
        if self._net_dialog is None:
            self._net_dialog = NetDialog(parent=self)
            self._track_dialog(self._net_dialog, self._act_network)
        if not self._net_dialog.isVisible():
            device_text = self._device_combo.currentText()
            self._net_dialog.set_device(
                device_text if not device_text.startswith("(") else None
            )
            self._net_dialog.set_adb_exe(self._adb_exe())
        self._toggle_dialog(self._net_dialog)

    # ================================================================== packages dialog
    def _show_packages_dialog(self) -> None:
        if self._packages_dialog is None:
            self._packages_dialog = PackagesDialog(parent=self)
            self._track_dialog(self._packages_dialog, self._act_packages)
        if not self._packages_dialog.isVisible():
            device_text = self._device_combo.currentText()
            self._packages_dialog.set_device(
                device_text if not device_text.startswith("(") else None
            )
            self._packages_dialog.set_adb_exe(self._adb_exe())
        self._toggle_dialog(self._packages_dialog)

    # ================================================================== lifecycle
    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Once the window is shown the table/viewport finally has its real
        # size, so the empty overlay's pixmap can be scaled to fit.
        self._update_empty_overlay()
        # Re-apply compact-rows: at init time Qt's style engine hasn't
        # fully computed section minimums, and the height set in __init__
        # can be silently clipped. Re-applying here makes it stick.
        self._table.verticalHeader().setDefaultSectionSize(
            16 if self._settings.compact_rows else 20
        )

    def closeEvent(self, event) -> None:
        # Stop any running background threads before Qt tears down the widget tree.
        # QThread::~QThread() aborts if the thread is still running.
        if self._mem_dialog is not None:
            self._mem_dialog.shutdown()
        if self._net_dialog is not None:
            self._net_dialog.shutdown()
        if self._packages_dialog is not None:
            self._packages_dialog.shutdown()
        if self._stats_dialog is not None:
            self._stats_dialog.shutdown()
        self._stop_capture()
        self._flush_db_buffer()           # persist any trailing rows before shutdown
        self._db.close()
        super().closeEvent(event)


# ============================================================ helpers
import re as _re


def re_escape_tag(tag: str) -> str:
    return _re.escape(tag)


def _load_text_file(path: str) -> List[LogRecord]:
    records: List[LogRecord] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            rec = parse_line(line.rstrip())
            if rec:
                records.append(rec)
    return records
