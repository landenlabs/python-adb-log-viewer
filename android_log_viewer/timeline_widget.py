from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QRect, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget, QSizePolicy

from .constants import LEVEL_SEVERITY, TIMELINE_BAR_COLORS

_MARGIN_LEFT = 10
_MARGIN_RIGHT = 10
_AXIS_H = 18   # pixels reserved below bars for time labels
_BG = QColor("#1E1E2E")
_AXIS_COLOR = QColor("#44475A")
_LABEL_COLOR = QColor("#8BE9FD")
_SEL_PEN = QColor("#F8F8F2")
_RANGE_FILL = QColor(255, 255, 255, 45)   # semi-transparent white overlay
_RANGE_EDGE = QColor("#F8F8F2")           # bright edge lines
_DRAG_THRESHOLD = 4                        # pixels before drag is recognised


class TimelineWidget(QWidget):
    """
    Horizontal bar-chart timeline.  Each bucket = 1 second of log activity.
    Bar colour = worst severity level seen in that second.
    Click a bar  → emits timestamp_selected(str) to jump the table.
    Click + drag → selects a time range; emits range_selected(from_key, to_key).
    """

    timestamp_selected = Signal(str)
    # Fires continuously while dragging and once on release.
    # Arguments are full "MM-DD HH:MM:SS" keys (17 chars each).
    range_selected = Signal(str, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # {ts_key: {level: count}}  ts_key = "MM-DD HH:MM:SS" (17 chars)
        self._buckets: Dict[str, Dict[str, int]] = {}
        self._keys: List[str] = []      # sorted chronologically
        self._max_count: int = 1
        self._hover_key: Optional[str] = None
        self._sel_key: Optional[str] = None

        # Range selection state
        self._range_from: Optional[str] = None   # committed range start key
        self._range_to: Optional[str] = None     # committed range end key
        self._drag_anchor: Optional[str] = None  # key where drag started
        self._drag_anchor_x: float = 0.0
        self._drag_cursor: Optional[str] = None  # key under cursor during drag
        self._is_dragging: bool = False

        self.setMinimumHeight(80)
        self.setMaximumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)

        # Throttle repaints to 10 fps during high-frequency updates
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(100)
        self._repaint_timer.timeout.connect(self.update)

    # ------------------------------------------------------------------
    def add_records(self, records) -> None:
        """Incremental add — O(len(records))."""
        changed = False
        for rec in records:
            key = rec.timestamp[:17]   # "MM-DD HH:MM:SS" (drop .mmm)
            if key not in self._buckets:
                self._buckets[key] = defaultdict(int)
                self._keys.append(key)
                changed = True
            self._buckets[key][rec.level] += 1
            total = sum(self._buckets[key].values())
            if total > self._max_count:
                self._max_count = total
                changed = True
            elif not changed:
                changed = True
        if changed and not self._repaint_timer.isActive():
            self._repaint_timer.start()

    def reset(self, records) -> None:
        """Full rebuild from a record list (used after load/clear)."""
        self._buckets.clear()
        self._keys.clear()
        self._max_count = 1
        self._hover_key = None
        self._sel_key = None
        self._range_from = None
        self._range_to = None
        self._drag_anchor = None
        self._drag_cursor = None
        self._is_dragging = False
        self.add_records(records)
        self.update()

    def set_cursor_key(self, key: str) -> None:
        """Move the single-click cursor marker to the given timestamp key."""
        if key in self._buckets and key != self._sel_key:
            self._sel_key = key
            self.update()

    def clear_range(self) -> None:
        """Remove the visual range selection without emitting a signal."""
        self._range_from = None
        self._range_to = None
        self._drag_anchor = None
        self._drag_cursor = None
        self._is_dragging = False
        self.update()

    def current_range(self) -> Optional[Tuple[str, str]]:
        """Return (from_key, to_key) if a range is selected, else None."""
        if self._range_from and self._range_to:
            return (self._range_from, self._range_to)
        return None

    # ------------------------------------------------------------------
    def _key_at(self, x: float, clamp: bool = False) -> Optional[str]:
        if not self._keys:
            return None
        n = len(self._keys)
        avail = self.width() - _MARGIN_LEFT - _MARGIN_RIGHT
        bar_w = avail / n
        idx = int((x - _MARGIN_LEFT) / bar_w)
        if clamp:
            idx = max(0, min(n - 1, idx))
            return self._keys[idx]
        if 0 <= idx < n:
            return self._keys[idx]
        return None

    def _range_indices(self) -> Tuple[int, int]:
        """Return (start_idx, end_idx) for the current range, normalised lo <= hi."""
        if not (self._range_from and self._range_to):
            return (-1, -1)
        try:
            fi = self._keys.index(self._range_from)
            ti = self._keys.index(self._range_to)
        except ValueError:
            return (-1, -1)
        return (min(fi, ti), max(fi, ti))

    def _emit_range(self) -> None:
        if self._range_from and self._range_to:
            lo, hi = self._range_indices()
            self.range_selected.emit(self._keys[lo], self._keys[hi])

    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            key = self._key_at(event.position().x())
            self._drag_anchor = key
            self._drag_anchor_x = event.position().x()
            self._drag_cursor = key
            self._is_dragging = False

    def mouseMoveEvent(self, event) -> None:
        x = event.position().x()

        # Hover update (only when not dragging)
        key = self._key_at(x)
        if not self._is_dragging and key != self._hover_key:
            self._hover_key = key
            if key:
                counts = self._buckets[key]
                total = sum(counts.values())
                worst = max(counts, key=lambda l: LEVEL_SEVERITY.get(l, 0))
                self.setToolTip(f"{key}  ·  {total} messages  ·  worst: {worst}")
            else:
                self.setToolTip("")
            self.update()

        # Drag tracking
        if event.buttons() & Qt.LeftButton and self._drag_anchor:
            if not self._is_dragging:
                if abs(x - self._drag_anchor_x) >= _DRAG_THRESHOLD:
                    self._is_dragging = True

            if self._is_dragging:
                cursor_key = self._key_at(x, clamp=True)
                if cursor_key and cursor_key != self._drag_cursor:
                    self._drag_cursor = cursor_key
                    # Update committed range to reflect live drag
                    anchor_i = self._keys.index(self._drag_anchor) if self._drag_anchor in self._keys else 0
                    cursor_i = self._keys.index(cursor_key)
                    if anchor_i <= cursor_i:
                        self._range_from = self._drag_anchor
                        self._range_to = cursor_key
                    else:
                        self._range_from = cursor_key
                        self._range_to = self._drag_anchor
                    self.update()
                    self._emit_range()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        if self._is_dragging:
            # Finalise range
            self._is_dragging = False
            self._drag_anchor = None
            self._drag_cursor = None
            self.update()
            self._emit_range()
        else:
            # Single click — jump to timestamp, keep any existing range
            key = self._key_at(event.position().x())
            if key:
                self._sel_key = key
                self.timestamp_selected.emit(key)
                self.update()
            self._drag_anchor = None

    def leaveEvent(self, event) -> None:
        self._hover_key = None
        self.update()

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        w = self.width()
        h = self.height()
        bar_area_h = h - _AXIS_H

        painter.fillRect(0, 0, w, h, _BG)

        if not self._keys:
            painter.setPen(QColor("#6272A4"))
            painter.drawText(QRect(0, 0, w, h), Qt.AlignCenter, "No log data")
            painter.end()
            return

        n = len(self._keys)
        avail = w - _MARGIN_LEFT - _MARGIN_RIGHT
        bar_w = avail / n

        # Precompute range indices for fast per-bar check
        range_lo, range_hi = self._range_indices()
        has_range = range_lo >= 0 and range_hi >= 0

        for i, key in enumerate(self._keys):
            counts = self._buckets[key]
            total = sum(counts.values())
            worst = max(counts, key=lambda l: LEVEL_SEVERITY.get(l, 0))
            color = QColor(TIMELINE_BAR_COLORS.get(worst, QColor("#42A5F5")))

            in_range = has_range and range_lo <= i <= range_hi

            if key == self._sel_key:
                color = color.lighter(130)
            elif in_range:
                color = color.lighter(118)
            elif key == self._hover_key:
                color = color.lighter(115)

            bar_h = max(2, int((total / self._max_count) * (bar_area_h - 6)))
            x = int(_MARGIN_LEFT + i * bar_w)
            bar_px_w = max(1, int(bar_w) - (1 if bar_w >= 3 else 0))
            y = bar_area_h - bar_h

            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(x, y, bar_px_w, bar_h, 2, 2)

        # Range overlay: semi-transparent fill + edge lines
        if has_range:
            x1 = int(_MARGIN_LEFT + range_lo * bar_w)
            x2 = int(_MARGIN_LEFT + (range_hi + 1) * bar_w)
            painter.fillRect(x1, 0, x2 - x1, bar_area_h, _RANGE_FILL)
            pen = QPen(_RANGE_EDGE, 2)
            painter.setPen(pen)
            painter.drawLine(x1, 0, x1, bar_area_h)
            painter.drawLine(x2 - 1, 0, x2 - 1, bar_area_h)

        # Single-click selection marker
        if self._sel_key and self._sel_key in self._buckets:
            i = self._keys.index(self._sel_key)
            cx = int(_MARGIN_LEFT + (i + 0.5) * bar_w)
            painter.setPen(QPen(_SEL_PEN, 1))
            painter.drawLine(cx, 0, cx, bar_area_h)

        # Axis line
        painter.setPen(QPen(_AXIS_COLOR, 1))
        painter.drawLine(_MARGIN_LEFT, bar_area_h, w - _MARGIN_RIGHT, bar_area_h)

        # Time labels: start / mid / end
        painter.setPen(_LABEL_COLOR)
        font = self.font()
        font.setPointSize(8)
        painter.setFont(font)
        label_triples = [(0, 0), (n // 2, n // 2), (n - 1, n - 1)]
        for idx, _ in label_triples:
            if idx >= n:
                continue
            label = self._keys[idx][6:]   # "HH:MM:SS"
            lx = int(_MARGIN_LEFT + idx * bar_w)
            painter.drawText(
                QRect(lx - 24, bar_area_h + 1, 48, _AXIS_H - 2),
                Qt.AlignCenter,
                label,
            )

        painter.end()
