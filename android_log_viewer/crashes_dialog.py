from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QGuiApplication,
    QPalette,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from .icons import app_icon
from .log_record import LogRecord


# Detection key: (timestamp, pid, tid, level).  Tag is *not* part of identity;
# the start record's tag is shown in the header only.
CrashKey = Tuple[str, str, str, str]


@dataclass
class CrashTrace:
    key: CrashKey
    tag: str
    records: List[LogRecord] = field(default_factory=list)
    promoted: bool = False

    def header(self) -> str:
        ts, pid, tid, _lvl = self.key
        return f"{ts}  {pid}/{tid}  {self.tag}"

    def as_text(self) -> str:
        lines = [self.header()]
        lines.extend(rec.message for rec in self.records)
        return "\n".join(lines)


class _CrashBlockDelegate(QStyledItemDelegate):
    """Renders each list item via QTextDocument so blocks can carry inline
    HTML highlights for the search-regex matches."""

    _SIDE_PAD = 6
    _TOP_PAD = 4

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()

        # Let the style draw the row background / selection highlight.
        opt.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)

        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setDocumentMargin(0)
        doc.setHtml(index.data(Qt.DisplayRole) or "")
        doc.setTextWidth(option.rect.width() - 2 * self._SIDE_PAD)

        painter.save()
        painter.translate(option.rect.left() + self._SIDE_PAD,
                          option.rect.top() + self._TOP_PAD)
        ctx = QAbstractTextDocumentLayout.PaintContext()
        if option.state & QStyle.State_Selected:
            ctx.palette.setColor(
                QPalette.Text,
                option.palette.color(QPalette.HighlightedText),
            )
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setDocumentMargin(0)
        doc.setHtml(index.data(Qt.DisplayRole) or "")
        width = option.rect.width() if option.rect.width() > 0 else 400
        doc.setTextWidth(width - 2 * self._SIDE_PAD)
        return QSize(
            int(doc.idealWidth()) + 2 * self._SIDE_PAD,
            int(doc.size().height()) + 2 * self._TOP_PAD,
        )


class CrashesDialog(QDialog):
    """Detect crash traces by start/follow regex, list them, and offer
    tag-filter + search-highlight on the result blocks."""

    # Emitted when any persistent setting (capture-enabled, regexes, min count)
    # changes. Main window saves the dialog's current values to AppSettings.
    settings_changed = Signal()

    # Emitted when the promoted-trace count changes (for the toolbar badge).
    count_changed = Signal(int)

    # Emitted when is_crash flags on existing model records change (clear, etc.)
    # so the main window can repaint the log table.
    flags_changed = Signal()

    def __init__(
        self,
        start_regex: str = "",
        follow_regex: str = "",
        min_count: int = 2,
        capture_enabled: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Crashes")
        self.setWindowIcon(app_icon("crashes"))
        self.resize(640, 600)

        self._enabled: bool = bool(capture_enabled)
        self._start_pattern: Optional[re.Pattern] = None
        self._follow_pattern: Optional[re.Pattern] = None
        self._min_count: int = max(1, int(min_count))
        self._tag_filter_pat: Optional[re.Pattern] = None
        self._search_pat: Optional[re.Pattern] = None

        # All seen traces keyed by (time, pid, tid, level). Both pending and
        # promoted live here; only promoted ones are listed.
        self._traces: Dict[CrashKey, CrashTrace] = {}
        # Insertion order of *promoted* traces for stable list display.
        self._promoted_order: List[CrashKey] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        desc = QLabel(
            "Detect crash traces by matching log messages. The <b>Start</b> "
            "regex begins a trace; the <b>Follow</b> regex matches additional "
            "lines that share the same <b>Time</b>, <b>PID</b>, <b>TID</b>, "
            "and <b>Level</b>. A trace becomes a crash once at least "
            "<b>Min&nbsp;count</b> matching lines are seen. Click a block to "
            "copy its text to the clipboard."
        )
        desc.setWordWrap(True)
        desc.setTextFormat(Qt.RichText)
        desc.setObjectName("CrashesDesc")
        desc.setStyleSheet(
            "QLabel#CrashesDesc {"
            "  color: palette(text);"
            "  background-color: palette(alternate-base);"
            "  border: 1px solid palette(mid);"
            "  border-radius: 4px;"
            "  padding: 8px 10px;"
            "  font-size: 11px;"
            "}"
        )
        root.addWidget(desc)

        # -------- Settings section (collapsed by default) --------
        # Arrow-style toggle button + content container.
        self._settings_toggle = QPushButton("▶  Settings")
        self._settings_toggle.setObjectName("crash_settings_toggle")
        self._settings_toggle.setCheckable(True)
        self._settings_toggle.setChecked(False)
        # Pin both states explicitly so the theme's generic :checked rule
        # (white-on-accent) doesn't override us and leave the label invisible.
        self._settings_toggle.setStyleSheet(
            "QPushButton#crash_settings_toggle,"
            "QPushButton#crash_settings_toggle:checked {"
            "  text-align: left;"
            "  padding: 4px 8px;"
            "  border: 1px solid palette(mid);"
            "  border-radius: 4px;"
            "  background-color: palette(alternate-base);"
            "  color: palette(text);"
            "  font-weight: bold;"
            "}"
            "QPushButton#crash_settings_toggle:hover {"
            "  background-color: palette(midlight);"
            "}"
        )
        root.addWidget(self._settings_toggle)

        self._settings_content = QFrame()
        self._settings_content.setFrameShape(QFrame.StyledPanel)
        sg_layout = QVBoxLayout(self._settings_content)
        sg_layout.setContentsMargins(10, 6, 10, 8)
        sg_layout.setSpacing(6)

        # Row 0: enable checkbox
        self._chk_enabled = QCheckBox("Enable crash capture")
        self._chk_enabled.setChecked(self._enabled)
        self._chk_enabled.setToolTip(
            "When disabled, no crash logs are highlighted or captured.\n"
            "Previously detected traces remain listed but unhighlighted."
        )
        sg_layout.addWidget(self._chk_enabled)

        # Row 1: min count
        row_min = QHBoxLayout()
        row_min.addWidget(QLabel("Min count:"))
        self._spin_min = QSpinBox()
        self._spin_min.setRange(1, 999)
        self._spin_min.setValue(self._min_count)
        self._spin_min.setToolTip(
            "Minimum number of matching lines (start + follows) sharing "
            "Time/PID/TID/Level required to treat the group as a crash."
        )
        row_min.addWidget(self._spin_min)
        row_min.addStretch()
        sg_layout.addLayout(row_min)

        # Row 2: start regex
        row_start = QHBoxLayout()
        lbl_start = QLabel("Start regex:")
        lbl_start.setFixedWidth(90)
        row_start.addWidget(lbl_start)
        self._start_edit = QLineEdit(start_regex)
        self._start_edit.setPlaceholderText(r"e.g.  FATAL EXCEPTION")
        self._start_edit.setToolTip(
            "Matching message starts a new pending crash trace.\n"
            "Empty disables detection."
        )
        row_start.addWidget(self._start_edit, stretch=1)
        sg_layout.addLayout(row_start)
        self._start_status = self._make_status_label()
        sg_layout.addWidget(self._start_status)

        # Row 3: follow regex
        row_follow = QHBoxLayout()
        lbl_follow = QLabel("Follow regex:")
        lbl_follow.setFixedWidth(90)
        row_follow.addWidget(lbl_follow)
        self._follow_edit = QLineEdit(follow_regex)
        self._follow_edit.setPlaceholderText(r"e.g.  ^\s+at |Caused by|^\s+\.\.\.")
        self._follow_edit.setToolTip(
            "Matches lines that follow the start line in a stack trace.\n"
            "Only applied to records sharing Time/PID/TID/Level with the start.\n"
            "Empty means only the start line counts (use Min count = 1)."
        )
        row_follow.addWidget(self._follow_edit, stretch=1)
        sg_layout.addLayout(row_follow)
        self._follow_status = self._make_status_label()
        sg_layout.addWidget(self._follow_status)

        # Settings signals
        self._chk_enabled.toggled.connect(self._on_capture_toggled)
        self._spin_min.valueChanged.connect(self._on_min_changed)
        self._start_edit.editingFinished.connect(self._on_regex_committed)
        self._follow_edit.editingFinished.connect(self._on_regex_committed)
        self._start_edit.textChanged.connect(
            lambda s: self._update_status(self._start_status, s)
        )
        self._follow_edit.textChanged.connect(
            lambda s: self._update_status(self._follow_status, s, allow_empty=True)
        )
        self._update_status(self._start_status, start_regex)
        self._update_status(self._follow_status, follow_regex, allow_empty=True)

        # Collapse / expand
        self._settings_toggle.toggled.connect(self._on_settings_toggled)
        self._settings_content.setVisible(False)
        root.addWidget(self._settings_content)

        # -------- Filters above list --------
        flt_row = QHBoxLayout()
        flt_row.setSpacing(6)
        flt_row.addWidget(QLabel("Tag filter:"))
        self._tag_edit = QLineEdit()
        self._tag_edit.setPlaceholderText("regex on Tag (blank = show all)")
        self._tag_edit.setToolTip(
            "Regex applied to each trace's Tag. Only matching traces are shown."
        )
        flt_row.addWidget(self._tag_edit, stretch=1)
        flt_row.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("regex highlighted in blocks")
        self._search_edit.setToolTip(
            "Regex matches are highlighted within visible trace blocks."
        )
        flt_row.addWidget(self._search_edit, stretch=1)
        root.addLayout(flt_row)

        self._tag_edit.textChanged.connect(self._on_tag_filter_changed)
        self._search_edit.textChanged.connect(self._on_search_changed)

        # -------- Crash list --------
        self._lbl_count = QLabel("0 traces")
        count_font = self._lbl_count.font()
        count_font.setBold(True)
        self._lbl_count.setFont(count_font)
        root.addWidget(self._lbl_count)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setUniformItemSizes(False)
        self._list.setWordWrap(True)
        self._list.setItemDelegate(_CrashBlockDelegate(self._list))
        self._list.itemClicked.connect(self._on_item_clicked)
        root.addWidget(self._list, stretch=1)

        # Bottom buttons
        btn_row = QHBoxLayout()
        self._btn_clear_all = QPushButton("Clear All")
        self._btn_clear_all.setToolTip("Remove all detected crash traces")
        self._btn_clear_all.clicked.connect(self._on_clear_all)
        btn_row.addWidget(self._btn_clear_all)

        self._btn_clear_hidden = QPushButton("Clear Hidden")
        self._btn_clear_hidden.setToolTip(
            "Remove traces whose Tag does not match the current Tag filter.\n"
            "Enabled only when a Tag filter is set and some traces are hidden."
        )
        self._btn_clear_hidden.setEnabled(False)
        self._btn_clear_hidden.clicked.connect(self._on_clear_hidden)
        btn_row.addWidget(self._btn_clear_hidden)

        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        # Compile seeded patterns
        self._start_pattern = self._safe_compile(start_regex)
        self._follow_pattern = self._safe_compile(follow_regex)

    # ================================================================== utils
    @staticmethod
    def _safe_compile(text: str) -> Optional[re.Pattern]:
        text = (text or "").strip()
        if not text:
            return None
        try:
            return re.compile(text)
        except re.error:
            return None

    @staticmethod
    def _make_status_label() -> QLabel:
        lbl = QLabel("")
        lbl.setStyleSheet("color: palette(mid); font-size: 11px;")
        return lbl

    @staticmethod
    def _update_status(lbl: QLabel, text: str, allow_empty: bool = False) -> None:
        text = (text or "").strip()
        if not text:
            if allow_empty:
                lbl.setText("(blank)")
                lbl.setStyleSheet("color: palette(mid); font-size: 11px;")
            else:
                lbl.setText("Detection disabled (empty)")
                lbl.setStyleSheet("color: palette(mid); font-size: 11px;")
            return
        try:
            re.compile(text)
            lbl.setText("Regex OK")
            lbl.setStyleSheet("color: #2E7D32; font-size: 11px;")
        except re.error as exc:
            lbl.setText(f"Invalid regex: {exc}")
            lbl.setStyleSheet("color: #C62828; font-size: 11px;")

    # ================================================================== public
    @property
    def start_regex(self) -> str:
        return self._start_edit.text()

    @property
    def follow_regex(self) -> str:
        return self._follow_edit.text()

    @property
    def min_count(self) -> int:
        return int(self._spin_min.value())

    @property
    def capture_enabled(self) -> bool:
        return self._chk_enabled.isChecked()

    def crash_count(self) -> int:
        return len(self._promoted_order)

    def all_promoted_records(self) -> List[LogRecord]:
        out: List[LogRecord] = []
        for key in self._promoted_order:
            tr = self._traces.get(key)
            if tr is not None:
                out.extend(tr.records)
        return out

    def showEvent(self, event) -> None:  # noqa: N802 — Qt override
        # Populate the list on first show (and any re-show), since feed_records
        # skips refresh while the dialog is hidden to avoid wasted work.
        self._dedupe_traces()
        self.refresh()
        super().showEvent(event)

    # Duplicate detection compares only this many leading characters of the
    # trace body — full stack traces can be huge, and the leading text (the
    # exception header + top frames) is enough to identify a repeat crash,
    # ignoring timestamp (and any other key field).
    _HASH_PREFIX_LEN = 512

    @classmethod
    def _trace_hash(cls, trace: CrashTrace) -> str:
        body = "\n".join(rec.message for rec in trace.records)
        prefix = body[: cls._HASH_PREFIX_LEN]
        return hashlib.sha256(prefix.encode("utf-8", "ignore")).hexdigest()

    def _dedupe_traces(self, emit: bool = True) -> bool:
        """Drop promoted traces whose message-body hash duplicates an
        earlier trace's. Returns True if any trace was dropped."""
        seen: Dict[str, CrashKey] = {}
        keep_order: List[CrashKey] = []
        drop_records: List[LogRecord] = []
        removed = False
        for key in self._promoted_order:
            tr = self._traces.get(key)
            if tr is None:
                continue
            h = self._trace_hash(tr)
            if h in seen:
                drop_records.extend(tr.records)
                self._traces.pop(key, None)
                removed = True
            else:
                seen[h] = key
                keep_order.append(key)
        if not removed:
            return False
        self._promoted_order = keep_order
        for rec in drop_records:
            rec.is_crash = False
        if emit:
            self.count_changed.emit(self.crash_count())
            self.flags_changed.emit()
        return True

    def reset_traces(self) -> None:
        self._traces.clear()
        self._promoted_order.clear()
        self.refresh()
        self.count_changed.emit(0)

    def feed_records(self, records: List[LogRecord]) -> bool:
        """Process incoming records. Returns True if the table needs repaint
        (i.e. some record's is_crash flag changed)."""
        if not self._enabled or self._start_pattern is None:
            return False
        any_change = False
        newly_promoted = 0
        for rec in records:
            if rec.is_sub_row:
                continue
            key: CrashKey = (rec.timestamp, rec.pid, rec.tid, rec.level)
            pending = self._traces.get(key)
            if pending is None:
                # Try to start a new trace.
                if not self._start_pattern.search(rec.message):
                    continue
                trace = CrashTrace(key=key, tag=rec.tag, records=[rec])
                self._traces[key] = trace
                if len(trace.records) >= self._min_count:
                    trace.promoted = True
                    self._promoted_order.append(key)
                    rec.is_crash = True
                    any_change = True
                    newly_promoted += 1
            else:
                # Existing trace: append on follow-match (or another start-match).
                if self._follow_pattern is not None and self._follow_pattern.search(rec.message):
                    matched = True
                elif self._start_pattern.search(rec.message):
                    matched = True
                else:
                    matched = False
                if not matched:
                    continue
                pending.records.append(rec)
                if pending.promoted:
                    rec.is_crash = True
                    any_change = True
                elif len(pending.records) >= self._min_count:
                    pending.promoted = True
                    self._promoted_order.append(key)
                    for r in pending.records:
                        r.is_crash = True
                    any_change = True
                    newly_promoted += 1
        removed = self._dedupe_traces(emit=False)
        any_change = any_change or removed
        if newly_promoted or removed:
            self.count_changed.emit(self.crash_count())
        if any_change and self.isVisible():
            self.refresh()
        return any_change

    # ================================================================== list
    def _count_text(self) -> str:
        total = len(self._promoted_order)
        if self._tag_filter_pat is None:
            return f"{total} trace{'s' if total != 1 else ''}"
        visible = sum(
            1 for k in self._promoted_order
            if k in self._traces and self._tag_filter_pat.search(self._traces[k].tag)
        )
        return f"{visible} of {total} traces (tag-filtered)"

    def refresh(self) -> None:
        self._list.clear()
        visible = [self._traces[k] for k in self._promoted_order if k in self._traces]
        if self._tag_filter_pat is not None:
            visible = [t for t in visible if self._tag_filter_pat.search(t.tag)]
        self._lbl_count.setText(self._count_text())
        for tr in visible:
            self._list.addItem(self._make_item(tr))
        self._update_clear_hidden_state(len(visible))

    def _update_clear_hidden_state(self, visible_count: Optional[int] = None) -> None:
        if self._tag_filter_pat is None:
            self._btn_clear_hidden.setEnabled(False)
            return
        total = len(self._promoted_order)
        if visible_count is None:
            visible_count = sum(
                1 for k in self._promoted_order
                if k in self._traces and self._tag_filter_pat.search(self._traces[k].tag)
            )
        self._btn_clear_hidden.setEnabled(visible_count < total)

    def _make_item(self, trace: CrashTrace) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(Qt.DisplayRole, self._render_block_html(trace))
        item.setData(Qt.UserRole, trace.key)
        item.setToolTip("Click to copy this trace to the clipboard")
        return item

    def _render_block_html(self, trace: CrashTrace) -> str:
        header = html.escape(trace.header())
        n = len(trace.records)
        suffix = f"  ({n} line{'s' if n != 1 else ''})"
        body_lines = [self._highlight_html(rec.message) for rec in trace.records]
        body = "\n".join(body_lines)
        # <pre> preserves leading whitespace in stack frames and honors inline
        # <span> tags for search highlights.
        return (
            "<pre style='font-family: monospace; font-size: 10pt; margin: 0;'>"
            f"<b>{header}</b><span style='color: #888;'>{html.escape(suffix)}</span>\n"
            f"{body}"
            "</pre>"
        )

    def _highlight_html(self, text: str) -> str:
        if self._search_pat is None or not text:
            return html.escape(text)
        parts: List[str] = []
        last = 0
        for m in self._search_pat.finditer(text):
            if m.start() > last:
                parts.append(html.escape(text[last:m.start()]))
            parts.append(
                "<span style='background-color: #FFEB3B; color: #000;'>"
                + html.escape(text[m.start():m.end()])
                + "</span>"
            )
            last = m.end()
        if last < len(text):
            parts.append(html.escape(text[last:]))
        return "".join(parts)

    # ================================================================== events
    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.UserRole)
        trace = self._traces.get(key)
        if trace is None:
            return
        QGuiApplication.clipboard().setText(trace.as_text())
        self._lbl_count.setText(f"{self._count_text()} [block copied to clipboard]")

    def _on_clear_all(self) -> None:
        for rec in self.all_promoted_records():
            rec.is_crash = False
        self.reset_traces()
        self.flags_changed.emit()

    def _on_clear_hidden(self) -> None:
        if self._tag_filter_pat is None:
            return
        keep_order: List[CrashKey] = []
        drop_records: List[LogRecord] = []
        for key in self._promoted_order:
            tr = self._traces.get(key)
            if tr is None:
                continue
            if self._tag_filter_pat.search(tr.tag):
                keep_order.append(key)
            else:
                drop_records.extend(tr.records)
                self._traces.pop(key, None)
        self._promoted_order = keep_order
        for rec in drop_records:
            rec.is_crash = False
        self.refresh()
        self.count_changed.emit(self.crash_count())
        self.flags_changed.emit()

    def _on_settings_toggled(self, expanded: bool) -> None:
        self._settings_content.setVisible(expanded)
        self._settings_toggle.setText("▼  Settings" if expanded else "▶  Settings")

    def _on_capture_toggled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        # Toggle highlight on existing promoted records without dropping the
        # listed traces — re-enabling restores their row highlight.
        for rec in self.all_promoted_records():
            rec.is_crash = self._enabled
        self.flags_changed.emit()
        self.settings_changed.emit()

    def _on_min_changed(self, value: int) -> None:
        self._min_count = max(1, int(value))
        self.settings_changed.emit()

    def _on_regex_committed(self) -> None:
        self._start_pattern = self._safe_compile(self._start_edit.text())
        self._follow_pattern = self._safe_compile(self._follow_edit.text())
        self.settings_changed.emit()

    def _on_tag_filter_changed(self, text: str) -> None:
        self._tag_filter_pat = self._safe_compile(text)
        self.refresh()

    def _on_search_changed(self, text: str) -> None:
        self._search_pat = self._safe_compile(text)
        # Re-render items so the new highlight applies. resetting the list is
        # enough — DisplayRole is regenerated from current _search_pat.
        self.refresh()
