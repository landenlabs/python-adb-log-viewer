from __future__ import annotations

import re
from typing import List, Optional, Set, Tuple, TYPE_CHECKING

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QRectF,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
)

from .app_settings import ExcludeRule
from .constants import LEVEL_BG, LEVEL_FG, MAX_RECORDS, PRUNE_SIZE
from .log_record import LogRecord

_COLUMNS = ("Time", "PID", "TID", "Lvl", "Tag", "Message")
COL_TIME, COL_PID, COL_TID, COL_LEVEL, COL_TAG, COL_MSG = range(6)

# Custom role: returns list of (start, end, fg_hex, bg_hex) for partial-text highlighting
HIGHLIGHT_ROLE = Qt.UserRole + 3

# Compiled color rule entry: (pattern, field, fg_or_None, bg_or_None, entire_row)
_CompiledRule = Tuple[re.Pattern, str, Optional[str], Optional[str], bool]


class LogModel(QAbstractTableModel):
    """Holds all log records in memory and exposes them to QTableView."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._records: List[LogRecord] = []
        self._merge_enabled: bool = False
        # Modern monospaced font stack
        self._font = QFont("JetBrains Mono, Cascadia Code, Consolas, Menlo, monospace", 9)
        self._font.setStyleStrategy(QFont.PreferAntialias)
        # Color config — initialised from constants, updated via set_color_config()
        self._level_fg: dict[str, QColor] = dict(LEVEL_FG)
        self._level_bg: dict[str, QColor] = dict(LEVEL_BG)
        self._color_rules: List[_CompiledRule] = []
        # row_id -> source-row index of the head record. Sub-rows share a row_id
        # with their parent and are not tracked here. Maintained across all
        # mutations so find_row_for_row_id is O(1) regardless of insertion order
        # (bookmark rows can land anywhere, breaking the old monotonic invariant).
        self._row_id_index: dict[int, int] = {}

    # ------------------------------------------------------------------ Qt API
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(_COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return _COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if row >= len(self._records):
            return None
        rec = self._records[row]

        if role == Qt.DisplayRole:
            if col == COL_TIME:
                return rec.timestamp
            if col == COL_PID:     return rec.pid
            if col == COL_TID:     return rec.tid
            if col == COL_LEVEL:   return rec.level
            if col == COL_TAG:     return rec.tag
            if col == COL_MSG:
                if rec.is_sub_row:
                    return "  " + rec.message
                if rec.sub_messages:
                    indicator = "▼ " if rec._expanded else "▶ "
                    return indicator + rec.sub_messages[0]
                return rec.message

        elif role == Qt.BackgroundRole:
            if rec._cached_bg is not None:
                return rec._cached_bg
            res = self._level_bg.get(rec.level, QColor("#FFFFFF"))
            for pat, field, fg, bg, entire_row in self._color_rules:
                if not entire_row or not bg:
                    continue
                target = rec.tag if field == "TAG" else rec.message
                if pat.search(target):
                    res = QColor(bg)
                    break
            rec._cached_bg = res
            return res

        elif role == Qt.ForegroundRole:
            if rec._cached_fg is not None:
                return rec._cached_fg
            res = self._level_fg.get(rec.level, QColor("#000000"))
            for pat, field, fg, bg, entire_row in self._color_rules:
                if not entire_row or not fg:
                    continue
                target = rec.tag if field == "TAG" else rec.message
                if pat.search(target):
                    res = QColor(fg)
                    break
            rec._cached_fg = res
            return res

        elif role == Qt.FontRole:
            return self._font

        elif role == Qt.UserRole:
            return rec

        elif role == HIGHLIGHT_ROLE:
            if rec._cached_highlights is None:
                rec._cached_highlights = {}
            if col in rec._cached_highlights:
                return rec._cached_highlights[col]

            if col == COL_TAG:
                field_name, text = "TAG", rec.tag
            elif col == COL_MSG:
                field_name, text = "MESSAGE", rec.message
            else:
                return None

            spans: list[tuple[int, int, Optional[str], Optional[str]]] = []
            for pat, field, fg, bg, entire_row in self._color_rules:
                if entire_row or field != field_name:
                    continue
                if not fg and not bg:
                    continue
                for m in pat.finditer(text):
                    spans.append((m.start(), m.end(), fg, bg))

            res = spans or None
            rec._cached_highlights[col] = res
            return res

        return None

    def set_font_size(self, pt: int) -> None:
        self._font = QFont("Courier New", pt)
        rows = len(self._records)
        if rows:
            top = self.index(0, 0)
            bottom = self.index(rows - 1, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom, [Qt.FontRole, Qt.SizeHintRole])

    def set_color_config(
        self,
        level_fg: dict[str, str],
        level_bg: dict[str, str],
        rules,
    ) -> None:
        """Apply user-configured level colors and color rules to the model."""
        # Merge with constants so V and F always have a fallback
        new_fg = dict(LEVEL_FG)
        new_bg = dict(LEVEL_BG)
        for lvl, hex_c in level_fg.items():
            if hex_c:
                new_fg[lvl] = QColor(hex_c)
        for lvl, hex_c in level_bg.items():
            if hex_c:
                new_bg[lvl] = QColor(hex_c)
        self._level_fg = new_fg
        self._level_bg = new_bg

        compiled: List[_CompiledRule] = []
        for rule in rules:
            if not rule.enabled or not rule.pattern:
                continue
            try:
                pat = re.compile(rule.pattern, re.IGNORECASE)
            except re.error:
                pat = re.compile(re.escape(rule.pattern), re.IGNORECASE)
            compiled.append((
                pat,
                rule.field,
                rule.fg or None,
                rule.bg or None,
                rule.entire_row,
            ))
        self._color_rules = compiled

        # Clear cache so new rules are applied
        for rec in self._records:
            rec._cached_bg = None
            rec._cached_fg = None
            rec._cached_highlights = None

        rows = len(self._records)
        if rows:
            top = self.index(0, 0)
            bottom = self.index(rows - 1, self.columnCount() - 1)
            self.dataChanged.emit(
                top,
                bottom,
                [Qt.BackgroundRole, Qt.ForegroundRole, HIGHLIGHT_ROLE],
            )

    def set_merge_enabled(self, enabled: bool) -> None:
        self._merge_enabled = enabled

    # ------------------------------------------------------------------ write
    def append_records(self, records: List[LogRecord]) -> None:
        if not records:
            return

        if self._merge_enabled:
            records = self._merge_batch(records)

        if not records:
            return

        # Cap memory growth: if we're about to exceed MAX_RECORDS,
        # prune the oldest records plus a buffer to avoid pruning on every batch.
        if len(self._records) + len(records) > MAX_RECORDS:
            to_remove = (len(self._records) + len(records) - MAX_RECORDS) + PRUNE_SIZE
            self.prune_oldest(to_remove)

        first = len(self._records)
        last = first + len(records) - 1
        self.beginInsertRows(QModelIndex(), first, last)
        self._records.extend(records)
        for i, rec in enumerate(records, start=first):
            if not rec.is_sub_row:
                self._row_id_index[rec.row_id] = i
        self.endInsertRows()

    def _find_tail(self) -> Tuple[Optional[LogRecord], int]:
        """Return the last non-sub-row record and its source index."""
        for i in range(len(self._records) - 1, -1, -1):
            if not self._records[i].is_sub_row:
                return self._records[i], i
        return None, -1

    def _merge_batch(self, records: List[LogRecord]) -> List[LogRecord]:
        """Collapse consecutive same-(ts_key, tag) records in the batch,
        then try to fold the first record into the model's tail."""
        # Pass 1: collapse within the batch
        collapsed: List[LogRecord] = []
        for rec in records:
            if (collapsed
                    and collapsed[-1].timestamp[:17] == rec.timestamp[:17]
                    and collapsed[-1].tag == rec.tag):
                head = collapsed[-1]
                if head.sub_messages is None:
                    head.sub_messages = [head.message]
                head.sub_messages.append(rec.message)
                head.message = head.message + "\n" + rec.message
            else:
                collapsed.append(rec)

        # Pass 2: fold first collapsed record into the model's tail if it matches
        if collapsed:
            tail, tail_src_row = self._find_tail()
            first = collapsed[0]
            if (tail is not None
                    and not tail._expanded
                    and tail.timestamp[:17] == first.timestamp[:17]
                    and tail.tag == first.tag):
                if tail.sub_messages is None:
                    tail.sub_messages = [tail.message]
                if first.sub_messages:
                    tail.sub_messages.extend(first.sub_messages)
                else:
                    tail.sub_messages.append(first.message)
                tail.message = "\n".join(tail.sub_messages)
                head_idx = self.index(tail_src_row, COL_MSG)
                self.dataChanged.emit(head_idx, head_idx, [Qt.DisplayRole])
                collapsed = collapsed[1:]

        return collapsed

    def toggle_expand(self, source_row: int) -> None:
        """Expand or collapse a merged head row in-place."""
        if source_row < 0 or source_row >= len(self._records):
            return
        rec = self._records[source_row]
        if rec.is_sub_row or not rec.sub_messages or len(rec.sub_messages) < 2:
            return

        if not rec._expanded:
            sub_rows: List[LogRecord] = []
            for msg in rec.sub_messages[1:]:
                sub = LogRecord(
                    row_id=rec.row_id,
                    timestamp=rec.timestamp,
                    pid=rec.pid,
                    tid=rec.tid,
                    level=rec.level,
                    tag=rec.tag,
                    message=msg,
                )
                sub.is_sub_row = True
                sub._parent_rec = rec
                sub_rows.append(sub)

            insert_at = source_row + 1
            self.beginInsertRows(QModelIndex(), insert_at, insert_at + len(sub_rows) - 1)
            self._records[insert_at:insert_at] = sub_rows
            rec._expanded = True
            self._shift_index_from(insert_at, len(sub_rows))
            self.endInsertRows()
        else:
            end = source_row + 1
            while end < len(self._records) and self._records[end].is_sub_row:
                end += 1
            if end > source_row + 1:
                removed = end - (source_row + 1)
                self.beginRemoveRows(QModelIndex(), source_row + 1, end - 1)
                del self._records[source_row + 1:end]
                rec._expanded = False
                self._shift_index_from(source_row + 1, -removed)
                self.endRemoveRows()

        head_idx = self.index(source_row, COL_MSG)
        self.dataChanged.emit(head_idx, head_idx, [Qt.DisplayRole])

    def prune_oldest(self, count: int) -> None:
        """Remove the first 'count' records from the model, plus any trailing sub-rows."""
        if count <= 0 or not self._records:
            return
        actual = min(count, len(self._records))
        # Don't orphan sub-rows that immediately follow the last pruned record
        while actual < len(self._records) and self._records[actual].is_sub_row:
            actual += 1
        pruned_ids = {r.row_id for r in self._records[:actual] if not r.is_sub_row}
        self.beginRemoveRows(QModelIndex(), 0, actual - 1)
        del self._records[:actual]
        for rid in pruned_ids:
            self._row_id_index.pop(rid, None)
        for rid in list(self._row_id_index):
            self._row_id_index[rid] -= actual
        self.endRemoveRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._records.clear()
        self._row_id_index.clear()
        self.endResetModel()

    # ------------------------------------------------------------------ read
    def record_at(self, row: int) -> Optional[LogRecord]:
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    def all_records(self) -> List[LogRecord]:
        return self._records

    def find_row_for_row_id(self, row_id: int) -> int:
        """Return the source-model row index of the head record with this row_id,
        or -1 if not present."""
        return self._row_id_index.get(row_id, -1)

    def _shift_index_from(self, threshold: int, delta: int) -> None:
        """Adjust _row_id_index entries with value >= threshold by delta."""
        if delta == 0:
            return
        for rid, pos in self._row_id_index.items():
            if pos >= threshold:
                self._row_id_index[rid] = pos + delta

    # ------------------------------------------------------------------ bookmarks
    def insert_bookmark_after(self, source_row: int, row_id: int) -> int:
        """Insert a level-B bookmark row directly after source_row (skipping any
        sub-rows that belong to it). Returns the new row's source index, or -1
        if source_row is out of range."""
        if source_row < 0 or source_row >= len(self._records):
            return -1
        insert_at = source_row + 1
        while insert_at < len(self._records) and self._records[insert_at].is_sub_row:
            insert_at += 1
        rec = self._records[source_row]
        head_ts = rec.timestamp
        bookmark = LogRecord(
            row_id=row_id,
            timestamp=head_ts,
            pid="",
            tid="",
            level="B",
            tag="",
            message="",
        )
        self.beginInsertRows(QModelIndex(), insert_at, insert_at)
        self._records.insert(insert_at, bookmark)
        self._shift_index_from(insert_at, 1)
        self._row_id_index[row_id] = insert_at
        self.endInsertRows()
        return insert_at

    def remove_bookmark_row(self, row_id: int) -> bool:
        """Remove the bookmark row with the given row_id. Returns True on success."""
        pos = self._row_id_index.get(row_id, -1)
        if pos < 0 or pos >= len(self._records):
            return False
        rec = self._records[pos]
        if rec.level != "B":
            return False
        self.beginRemoveRows(QModelIndex(), pos, pos)
        del self._records[pos]
        self._row_id_index.pop(row_id, None)
        self._shift_index_from(pos, -1)
        self.endRemoveRows()
        return True

    def clear_bookmark_rows(self) -> int:
        """Remove all bookmark rows. Returns the number removed."""
        ids = [r.row_id for r in self._records if r.level == "B" and not r.is_sub_row]
        for rid in ids:
            self.remove_bookmark_row(rid)
        return len(ids)

    def bookmarked_records(self) -> List[LogRecord]:
        """Return all bookmark rows (level == "B") in row order."""
        return [r for r in self._records if r.level == "B" and not r.is_sub_row]

    def find_row_for_timestamp(self, ts: str) -> int:
        """Binary search: first row whose timestamp >= ts."""
        lo, hi = 0, len(self._records) - 1
        result = len(self._records)
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._records[mid].timestamp >= ts:
                result = mid
                hi = mid - 1
            else:
                lo = mid + 1
        return result


class HighlightDelegate(QStyledItemDelegate):
    """Delegate that paints per-span text highlights for entire_row=False rules."""

    def paint(self, painter, option, index) -> None:
        highlights = index.data(HIGHLIGHT_ROLE)
        if not highlights:
            super().paint(painter, option, index)
            return

        self.initStyleOption(option, index)
        opt = QStyleOptionViewItem(option)
        text: str = opt.text or ""
        opt.text = ""

        # 1. Draw the item background, selection, focus, etc.
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, option.widget)

        if not text:
            return

        # 2. Draw the text segments with highlights
        text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, option.widget)
        fm = option.fontMetrics
        ascent = fm.ascent()

        # Ensure highlights are sorted and within bounds
        highlights.sort(key=lambda x: x[0])

        painter.save()
        painter.setFont(option.font)
        painter.translate(text_rect.topLeft())
        # Clip to the text rectangle to prevent drawing into other columns
        painter.setClipRect(0, 0, text_rect.width(), text_rect.height())

        last_pos = 0
        x_offset = 0

        for start, end, fg_hex, bg_hex in highlights:
            if start >= len(text):
                break
            end = min(end, len(text))
            if end <= start:
                continue

            # Gap before this highlight
            if start > last_pos:
                seg = text[last_pos:start]
                painter.setPen(option.palette.text().color())
                painter.drawText(x_offset, ascent, seg)
                x_offset += fm.horizontalAdvance(seg)

            # The highlighted segment
            seg = text[start:end]
            adv = fm.horizontalAdvance(seg)
            if bg_hex:
                painter.fillRect(x_offset, 0, adv, text_rect.height(), QColor(bg_hex))
            if fg_hex:
                painter.setPen(QColor(fg_hex))
            else:
                painter.setPen(option.palette.text().color())

            painter.drawText(x_offset, ascent, seg)
            x_offset += adv
            last_pos = end

        # Trailing text
        if last_pos < len(text):
            seg = text[last_pos:]
            painter.setPen(option.palette.text().color())
            painter.drawText(x_offset, ascent, seg)

        painter.restore()


class LogFilterProxy(QSortFilterProxyModel):
    """Client-side filter: level set + tag regex + text regex + stats PID/tag sets + time range."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._allowed: Set[str] = {"V", "D", "I", "W", "E", "F", "B"}
        self._tag_rx: Optional[re.Pattern] = None
        self._text_rx: Optional[re.Pattern] = None
        self._pid_set: Set[str] = set()   # empty = all PIDs
        self._tag_set: Set[str] = set()   # empty = all tags
        # Compiled exclusion rules: [(pattern, field), ...]
        self._exclude_rules: List[Tuple[re.Pattern, str]] = []
        # Time range filter: "MM-DD HH:MM:SS" keys (inclusive), or None = no limit
        self._time_from: Optional[str] = None
        self._time_to: Optional[str] = None
        self.setDynamicSortFilter(False)

    # ------------------------------------------------------------------ setters
    def set_levels(self, levels: Set[str]) -> None:
        self._allowed = levels
        self.invalidateFilter()

    def set_tag_filter(self, pattern: str) -> None:
        self._tag_rx = self._compile(pattern)
        self.invalidateFilter()

    def set_text_filter(self, pattern: str) -> None:
        self._text_rx = self._compile(pattern)
        self.invalidateFilter()

    def set_pid_filter(self, pids: Set[str]) -> None:
        """Restrict to exact PID matches. Empty set = no restriction."""
        self._pid_set = pids
        self.invalidateFilter()

    def set_tag_set_filter(self, tags: Set[str]) -> None:
        """Restrict to exact tag matches (from stats dialog). Empty set = no restriction."""
        self._tag_set = tags
        self.invalidateFilter()

    def set_time_range(self, from_ts: Optional[str], to_ts: Optional[str]) -> None:
        """Filter to rows whose timestamp key falls within [from_ts, to_ts].
        Pass None for both to remove the time filter."""
        self._time_from = from_ts
        self._time_to = to_ts
        self.invalidateFilter()

    def set_exclude_rules(self, rules: List[ExcludeRule]) -> None:
        """Compile and store exclusion rules. Rows matching any enabled rule are hidden."""
        compiled: List[Tuple[re.Pattern, str]] = []
        for rule in rules:
            if not rule.enabled or not rule.pattern:
                continue
            try:
                pat = re.compile(rule.pattern, re.IGNORECASE)
            except re.error:
                pat = re.compile(re.escape(rule.pattern), re.IGNORECASE)
            compiled.append((pat, rule.field))
        self._exclude_rules = compiled
        self.invalidateFilter()

    # ------------------------------------------------------------------ filter
    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        src: LogModel = self.sourceModel()  # type: ignore[assignment]
        rec = src.record_at(source_row)
        if rec is None:
            return False
        # Sub-rows inherit their parent's filter result so they show/hide together
        if rec.is_sub_row:
            parent = rec._parent_rec
            if parent is None:
                return False
            return self._accepts_record(parent)
        return self._accepts_record(rec)

    def _accepts_record(self, rec: "LogRecord") -> bool:
        if rec.level not in self._allowed:
            return False
        if self._tag_rx and not self._tag_rx.search(rec.tag):
            return False
        if self._text_rx:
            if not (self._text_rx.search(rec.message) or self._text_rx.search(rec.tag)):
                return False
        if self._pid_set and rec.pid not in self._pid_set:
            return False
        if self._tag_set and rec.tag not in self._tag_set:
            return False
        if self._time_from or self._time_to:
            ts_key = rec.timestamp[:17]
            if self._time_from and ts_key < self._time_from:
                return False
            if self._time_to and ts_key > self._time_to:
                return False
        for pattern, field in self._exclude_rules:
            if field == "PID" and pattern.search(rec.pid):
                return False
            elif field == "TAG" and pattern.search(rec.tag):
                return False
            elif field == "MESSAGE" and pattern.search(rec.message):
                return False
        return True

    def accepts_for_timeline(self, rec: "LogRecord") -> bool:
        """Like _accepts_record but skips the time range filter.
        Used to build the filtered timeline without creating a feedback loop
        between the range selection and the bars it contains."""
        if rec.level not in self._allowed:
            return False
        if self._tag_rx and not self._tag_rx.search(rec.tag):
            return False
        if self._text_rx:
            if not (self._text_rx.search(rec.message) or self._text_rx.search(rec.tag)):
                return False
        if self._pid_set and rec.pid not in self._pid_set:
            return False
        if self._tag_set and rec.tag not in self._tag_set:
            return False
        for pattern, field in self._exclude_rules:
            if field == "PID" and pattern.search(rec.pid):
                return False
            elif field == "TAG" and pattern.search(rec.tag):
                return False
            elif field == "MESSAGE" and pattern.search(rec.message):
                return False
        return True

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _compile(pattern: str) -> Optional[re.Pattern]:
        if not pattern:
            return None
        try:
            return re.compile(pattern, re.IGNORECASE)
        except re.error:
            return re.compile(re.escape(pattern), re.IGNORECASE)
