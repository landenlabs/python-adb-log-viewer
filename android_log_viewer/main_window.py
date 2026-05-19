from __future__ import annotations

from typing import List, Optional, Set

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QSize, QTimer, Qt
from PySide6.QtGui import QAction, QFontMetrics, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
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
from .app_settings import AppSettings, RECENT_CAP
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

# Style for the collapsible filter-section toggles. The :checked state is the
# expanded state; "active" property marks a section whose field has content.
# `color` is omitted on the un-checked rules so the theme stylesheet
# (QPushButton color) supplies it — dark text in light mode, light in dark.
_SECTION_BUTTON_STYLE = """
QPushButton {
    text-align: left;
    padding: 2px 6px;
    background: transparent;
    border: 1px solid transparent;
}
QPushButton[active="true"] {
    font-weight: bold;
}
QPushButton:checked, QPushButton[active="true"]:checked {
    color: white;
    background-color: #1565C0;
    border: 1px solid #1565C0;
    font-weight: bold;
}
"""


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

        # CLI args win; otherwise fall back to startup patterns saved in the color profile.
        effective_tag = initial_tag or self._settings.startup_tag
        effective_text = initial_text or self._settings.startup_text
        if effective_tag or effective_text:
            self._apply_initial_filters(effective_tag, effective_text)

        self._auto_expand_filter_sections()
        self._update_section_indicators()

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
        # Off-state uses the theme's default button text color so it stays
        # readable in dark mode. On-state is set in _on_record_toggled.
        self._btn_record.setStyleSheet("")
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
        row.setSpacing(4)

        # ----- Level -----
        self._lvl_toggle = self._make_section_toggle("Level")
        row.addWidget(self._lvl_toggle)
        self._lvl_container = QWidget()
        lvl_l = QHBoxLayout(self._lvl_container)
        lvl_l.setContentsMargins(0, 0, 0, 0)
        lvl_l.setSpacing(4)
        self._level_cbs: dict[str, QCheckBox] = {}
        for lvl in LEVELS:
            cb = QCheckBox(lvl)
            cb.setChecked(lvl in self._settings.level_filters)
            cb.setToolTip(LEVEL_NAMES[lvl])
            self._level_cbs[lvl] = cb
            lvl_l.addWidget(cb)
        row.addWidget(self._lvl_container)
        self._wire_section_toggle(self._lvl_toggle, self._lvl_container, "Level")

        row.addSpacing(8)

        # ----- Tag -----
        self._tag_toggle = self._make_section_toggle("Tag")
        row.addWidget(self._tag_toggle)
        self._tag_edit = self._make_filter_combo(
            placeholder="Tag regex…",
            tooltip=(
                "Filter by tag name (regex, case-insensitive).\n"
                "Shortcut: Ctrl+L\n"
                "Right-click for recent items."
            ),
            recent=self._settings.recent_tags,
        )
        row.addWidget(self._tag_edit, stretch=1)
        self._wire_section_toggle(self._tag_toggle, self._tag_edit, "Tag")

        # ----- Message -----
        self._text_toggle = self._make_section_toggle("Message")
        row.addWidget(self._text_toggle)
        self._text_edit = self._make_filter_combo(
            placeholder="Message regex…",
            tooltip=(
                "Filter by message text or tag (regex, case-insensitive).\n"
                "Shortcut: Ctrl+F\n"
                "Right-click for recent items."
            ),
            recent=self._settings.recent_texts,
        )
        row.addWidget(self._text_edit, stretch=1)
        self._wire_section_toggle(self._text_toggle, self._text_edit, "Message")

        # ----- Search -----
        self._search_toggle = self._make_section_toggle("Search")
        row.addWidget(self._search_toggle)
        self._search_container = QWidget()
        sc_l = QHBoxLayout(self._search_container)
        sc_l.setContentsMargins(0, 0, 0, 0)
        sc_l.setSpacing(2)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search message (regex)…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setToolTip(
            "Search message text (regex, case-insensitive).\n"
            "Enter / ▶: next match · Shift+Enter / ◀: previous match\n"
            "Shortcut to focus: Ctrl+G"
        )
        sc_l.addWidget(self._search_edit, stretch=1)
        self._btn_search_prev = QPushButton("◀")
        self._btn_search_prev.setFixedWidth(28)
        self._btn_search_prev.setToolTip("Previous match (Shift+Enter)")
        sc_l.addWidget(self._btn_search_prev)
        self._btn_search_next = QPushButton("▶")
        self._btn_search_next.setFixedWidth(28)
        self._btn_search_next.setToolTip("Next match (Enter)")
        sc_l.addWidget(self._btn_search_next)
        row.addWidget(self._search_container, stretch=1)
        self._wire_section_toggle(self._search_toggle, self._search_container, "Search")

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

    # ============================================================ collapsible sections
    def _make_section_toggle(self, label: str) -> QPushButton:
        btn = QPushButton(f"▸ {label}")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        # Buttons must not steal horizontal space from the input widgets.
        btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        btn.setStyleSheet(_SECTION_BUTTON_STYLE)
        # Reserve room for the bold/expanded label so width doesn't jitter
        # when the indicator or arrow swaps.
        font = btn.font()
        font.setBold(True)
        fm = QFontMetrics(font)
        btn.setFixedWidth(fm.horizontalAdvance(f"▾ {label}") + 18)
        return btn

    def _wire_section_toggle(self, btn: QPushButton, widget: QWidget, label: str) -> None:
        def on_toggle(checked: bool) -> None:
            btn.setText(f"{'▾' if checked else '▸'} {label}")
            widget.setVisible(checked)
            if checked:
                # Move focus into the newly opened input for quick typing.
                if isinstance(widget, (QLineEdit, QComboBox)):
                    widget.setFocus()
                else:
                    focus_target = widget.findChild(QLineEdit)
                    if focus_target:
                        focus_target.setFocus()

        btn.toggled.connect(on_toggle)
        widget.setVisible(False)

    # ============================================================ filter combo (MRU)
    def _make_filter_combo(
        self, placeholder: str, tooltip: str, recent: List[str]
    ) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        # We manage the MRU ourselves; Qt's auto-insert would duplicate entries
        # and ignore our cap.
        combo.setInsertPolicy(QComboBox.NoInsert)
        combo.setMaxVisibleItems(15)
        combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Off-the-shelf completer can over-eagerly auto-fill regex chars; keep
        # the dropdown-only behavior of Qt's default combo completer instead.
        completer = combo.completer()
        if completer is not None:
            completer.setCompletionMode(QCompleter.PopupCompletion)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
        le = combo.lineEdit()
        le.setPlaceholderText(placeholder)
        le.setClearButtonEnabled(True)
        combo.setToolTip(tooltip)
        if recent:
            combo.addItems(recent)
        # Start blank — addItems selects index 0 by default.
        combo.setCurrentIndex(-1)
        le.clear()
        # Custom right-click menu adds a "Clear recent" action.
        le.setContextMenuPolicy(Qt.CustomContextMenu)
        le.customContextMenuRequested.connect(
            lambda pos, c=combo: self._show_filter_combo_menu(c, pos)
        )
        return combo

    def _recent_list_for(self, combo: QComboBox) -> List[str]:
        if combo is self._tag_edit:
            return self._settings.recent_tags
        if combo is self._text_edit:
            return self._settings.recent_texts
        return []

    def _commit_recent(self, combo: QComboBox) -> None:
        """Push the combo's current text to the front of its MRU list."""
        text = combo.currentText().strip()
        if not text:
            return
        recent = self._recent_list_for(combo)
        if recent and recent[0] == text:
            return  # already at top, no save needed
        if text in recent:
            recent.remove(text)
        recent.insert(0, text)
        del recent[RECENT_CAP:]
        # Refresh the dropdown items without disturbing the edit field.
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(recent)
        combo.setEditText(text)
        combo.blockSignals(False)
        self._settings.save()

    def _show_filter_combo_menu(self, combo: QComboBox, pos) -> None:
        le = combo.lineEdit()
        menu = le.createStandardContextMenu()
        menu.addSeparator()
        recent = self._recent_list_for(combo)
        act = menu.addAction(f"Clear recent ({len(recent)})")
        act.setEnabled(bool(recent))
        act.triggered.connect(lambda: self._clear_recent(combo))
        menu.exec(le.mapToGlobal(pos))

    def _clear_recent(self, combo: QComboBox) -> None:
        recent = self._recent_list_for(combo)
        recent.clear()
        text = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.setEditText(text)
        combo.blockSignals(False)
        self._settings.save()

    def _auto_expand_filter_sections(self) -> None:
        """Open any filter section whose field already has content at startup,
        so the user can see what's active without hunting for a collapsed label."""
        if not all(cb.isChecked() for cb in self._level_cbs.values()):
            self._lvl_toggle.setChecked(True)
        if self._tag_edit.currentText():
            self._tag_toggle.setChecked(True)
        if self._text_edit.currentText():
            self._text_toggle.setChecked(True)
        if self._search_edit.text():
            self._search_toggle.setChecked(True)

    # ================================================================== wiring
    def _wire_signals(self) -> None:
        self._btn_refresh.clicked.connect(self._refresh_devices)
        self._btn_connect.toggled.connect(self._on_connect_toggled)
        self._btn_clear.clicked.connect(self._clear_logs)
        self._act_save.triggered.connect(self._save_logs)
        self._act_open.triggered.connect(self._open_logs)
        self._chk_autoscroll.toggled.connect(self._on_autoscroll_toggled)

        for cb in self._level_cbs.values():
            cb.toggled.connect(self._on_filter_changed)
            cb.toggled.connect(self._update_section_indicators)

        self._tag_edit.editTextChanged.connect(self._proxy.set_tag_filter)
        self._tag_edit.editTextChanged.connect(self._update_status)
        self._tag_edit.editTextChanged.connect(self._schedule_timeline_filter_rebuild)
        self._tag_edit.editTextChanged.connect(self._update_section_indicators)
        self._text_edit.editTextChanged.connect(self._proxy.set_text_filter)
        self._text_edit.editTextChanged.connect(self._update_status)
        self._text_edit.editTextChanged.connect(self._schedule_timeline_filter_rebuild)
        self._text_edit.editTextChanged.connect(self._update_section_indicators)

        # Commit the current value to the MRU when the user is "done" with it
        # (Enter pressed or focus left). activated also fires when an item is
        # picked from the dropdown.
        self._tag_edit.lineEdit().editingFinished.connect(
            lambda c=self._tag_edit: self._commit_recent(c)
        )
        self._text_edit.lineEdit().editingFinished.connect(
            lambda c=self._text_edit: self._commit_recent(c)
        )
        self._tag_edit.activated.connect(
            lambda _i, c=self._tag_edit: self._commit_recent(c)
        )
        self._text_edit.activated.connect(
            lambda _i, c=self._text_edit: self._commit_recent(c)
        )

        # Search field — does not filter, just jumps to matches.
        self._search_edit.returnPressed.connect(lambda: self._search_jump(forward=True))
        self._search_edit.textChanged.connect(self._update_section_indicators)
        self._search_edit.textChanged.connect(self._on_search_text_changed)
        self._btn_search_next.clicked.connect(lambda: self._search_jump(forward=True))
        self._btn_search_prev.clicked.connect(lambda: self._search_jump(forward=False))

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

        search_focus = QAction("Focus search", self)
        search_focus.setShortcut(QKeySequence("Ctrl+G"))
        search_focus.triggered.connect(self._focus_search)
        self.addAction(search_focus)

        # Shift+Return in the search field walks backwards. QLineEdit eats
        # plain Return for returnPressed; bind Shift+Return to the field
        # itself so it only fires while it has focus.
        search_prev_action = QAction("Search previous", self._search_edit)
        search_prev_action.setShortcut(QKeySequence("Shift+Return"))
        search_prev_action.triggered.connect(lambda: self._search_jump(forward=False))
        self._search_edit.addAction(search_prev_action)

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
            self._tag_edit.setEditText(tag)
        if text:
            self._text_edit.setEditText(text)
        # Auto-connect when exactly one real device is present
        current = self._device_combo.currentText()
        if self._device_combo.count() == 1 and not current.startswith("("):
            QTimer.singleShot(0, lambda: self._btn_connect.setChecked(True))

    # ================================================================== search
    def _focus_search(self) -> None:
        if not self._search_toggle.isChecked():
            self._search_toggle.setChecked(True)
        self._search_edit.setFocus()
        self._search_edit.selectAll()

    def _search_jump(self, forward: bool) -> None:
        pattern = self._search_edit.text()
        if not pattern:
            return
        try:
            rx = _re.compile(pattern, _re.IGNORECASE)
        except _re.error:
            rx = _re.compile(_re.escape(pattern), _re.IGNORECASE)

        rows = self._proxy.rowCount()
        if rows == 0:
            self.statusBar().showMessage("No rows to search.", 2000)
            return

        current = self._table.currentIndex().row()
        if current < 0:
            current = -1 if forward else rows
        step = 1 if forward else -1

        for offset in range(1, rows + 1):
            r = (current + offset * step) % rows
            rec: Optional[LogRecord] = self._proxy.data(
                self._proxy.index(r, 0), Qt.UserRole
            )
            if rec is None:
                continue
            m = rx.search(rec.message)
            if m:
                # Disable autoscroll so we stay on the match.
                if self._auto_scroll:
                    self._chk_autoscroll.setChecked(False)
                # Vertical scroll only — column 0 is at the left edge so this
                # never forces a horizontal jump on its own.
                self._table.scrollTo(
                    self._proxy.index(r, 0), QTableView.PositionAtCenter
                )
                self._table.selectRow(r)
                self._adjust_horizontal_for_match(rec.message, m.start(), m.end())
                self.statusBar().showMessage(
                    f"Match {r + 1} of {rows} visible rows", 2000
                )
                return

        self.statusBar().showMessage(f"No matches for '{pattern}'", 2500)

    def _adjust_horizontal_for_match(
        self, message: str, match_start: int, match_end: int
    ) -> None:
        """Keep horizontal scroll at the left edge whenever possible.
        Only shift right by the minimum needed to bring the match into view."""
        hbar = self._table.horizontalScrollBar()
        hbar.setValue(0)

        fm = QFontMetrics(self._model._font)
        pre_width = fm.horizontalAdvance(message[:match_start])
        match_width = fm.horizontalAdvance(message[match_start:match_end])

        hh = self._table.horizontalHeader()
        # With hbar=0, this gives the column's offset from the viewport left.
        msg_col_x = hh.sectionViewportPosition(COL_MSG)
        # QStyledItemDelegate's default text margin (Qt uses 3-4 px in practice).
        cell_text_pad = 4

        match_left_vp = msg_col_x + cell_text_pad + pre_width
        match_right_vp = match_left_vp + match_width
        vp_width = self._table.viewport().width()
        if vp_width <= 0:
            return

        # Match already visible — leave horizontal scroll at 0.
        if match_right_vp <= vp_width:
            return

        # Off to the right: shift just enough so the match lands a bit inside
        # the right edge, preserving as much left-side context as possible.
        right_margin = 80
        shift = match_right_vp - vp_width + right_margin
        # Cap to scrollbar range so we don't try to scroll past content.
        shift = min(shift, hbar.maximum())
        hbar.setValue(shift)

    def _on_search_text_changed(self, text: str) -> None:
        """Live-highlight matches of the search pattern in the message column."""
        self._model.set_search_highlight(
            text, self._settings.search_fg, self._settings.search_bg
        )

    def _update_section_indicators(self) -> None:
        """Mark a section toggle "active" (bold) when its field has content,
        so collapsed sections still hint that a filter/search is in play.
        Color management lives entirely in _SECTION_BUTTON_STYLE: black when
        collapsed, white-on-blue when expanded, regardless of active state."""
        all_checked = all(cb.isChecked() for cb in self._level_cbs.values())
        states = (
            (self._lvl_toggle, not all_checked),
            (self._tag_toggle, bool(self._tag_edit.currentText())),
            (self._text_toggle, bool(self._text_edit.currentText())),
            (self._search_toggle, bool(self._search_edit.text())),
        )
        for btn, is_active in states:
            btn.setProperty("active", "true" if is_active else "false")
            # Force the stylesheet engine to re-evaluate the property selector.
            btn.style().unpolish(btn)
            btn.style().polish(btn)

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
            self._btn_record.setStyleSheet("color: #E53935; font-weight: bold;")
            self.statusBar().showMessage("Recording resumed — new logs will be saved.", 3000)
        else:
            self._btn_record.setText("○ REC")
            # Clear override; theme stylesheet supplies a readable text color.
            self._btn_record.setStyleSheet("")
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
        if len(selected_rows) > 1:
            menu.addAction(
                f"Copy {len(selected_rows)} selected messages",
                self._copy_selected_messages,
            )
        else:
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
            lambda t=rec.tag: self._tag_edit.setEditText(re_escape_tag(t)),
        )
        menu.addAction(
            "Exclude this tag",
            lambda t=rec.tag: self._tag_edit.setEditText(f"^((?!{re_escape_tag(t)}).)*$"),
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

    def _copy_selected_messages(self) -> None:
        selected_rows = sorted({i.row() for i in self._table.selectedIndexes()})
        lines = []
        for row in selected_rows:
            rec: Optional[LogRecord] = self._proxy.data(
                self._proxy.index(row, 0), Qt.UserRole
            )
            if rec:
                lines.append(rec.message)
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
            self._settings_dialog.theme_changed.connect(self._on_theme_changed)
        device_text = self._device_combo.currentText()
        device = device_text if not device_text.startswith("(") else None
        self._settings_dialog.set_device(device)
        self._settings_dialog._load()   # sync from current settings each time shown
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _on_theme_changed(self, theme: str) -> None:
        """Live preview/apply from the Settings dialog — restyle without
        touching the rest of the settings pipeline."""
        apply_theme(theme)
        # Background pixmap for the empty-state overlay is theme-specific.
        self._update_empty_overlay()

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
        # Keep the live search highlight in sync with the (possibly changed)
        # search-highlight colors from the Color Rules dialog.
        self._model.set_search_highlight(
            self._search_edit.text(),
            self._settings.search_fg,
            self._settings.search_bg,
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
