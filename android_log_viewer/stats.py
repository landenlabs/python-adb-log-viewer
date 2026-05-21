from __future__ import annotations

from typing import Dict, List, Tuple

from .log_record import LogRecord


class _Stat:
    __slots__ = ("count", "first", "last")

    def __init__(self, ts: str) -> None:
        self.count = 1
        self.first = ts
        self.last = ts

    def add(self, ts: str) -> None:
        self.count += 1
        if ts < self.first:
            self.first = ts
        if ts > self.last:
            self.last = ts


class StatsTracker:
    """
    Incrementally tracks per-PID and per-Tag message counts and time ranges.
    Only the top_n most-frequent entries are retained in the returned snapshots.
    """

    DEFAULT_TOP_N = 200

    def __init__(self, top_n: int = DEFAULT_TOP_N) -> None:
        self.top_n = top_n
        self._pids: Dict[str, _Stat] = {}
        self._tags: Dict[str, _Stat] = {}

    def update(self, records: List[LogRecord]) -> None:
        for rec in records:
            if rec.pid in self._pids:
                self._pids[rec.pid].add(rec.timestamp)
            else:
                self._pids[rec.pid] = _Stat(rec.timestamp)
            if rec.tag in self._tags:
                self._tags[rec.tag].add(rec.timestamp)
            else:
                self._tags[rec.tag] = _Stat(rec.timestamp)

    def reset(self) -> None:
        self._pids.clear()
        self._tags.clear()

    def pid_stats(self) -> List[Tuple[str, int, str, str]]:
        """[(pid, count, first_ts, last_ts), ...] sorted by count desc, top N."""
        items = sorted(self._pids.items(), key=lambda x: -x[1].count)[: self.top_n]
        return [(pid, s.count, s.first, s.last) for pid, s in items]

    def tag_stats(self) -> List[Tuple[str, int, str, str]]:
        """[(tag, count, first_ts, last_ts), ...] sorted by count desc, top N."""
        items = sorted(self._tags.items(), key=lambda x: -x[1].count)[: self.top_n]
        return [(tag, s.count, s.first, s.last) for tag, s in items]
