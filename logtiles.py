#!/usr/bin/env python3
"""Parse a log file and render one PNG per LOD level showing good (green)
and bad (red) tiles. Uses Qt (PySide6) for image rendering."""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

LINE_RE = re.compile(r"code=([0-9]+) .*lod=([0-9]+).x=([0-9]+).y=([0-9]+)")
URL_RE = re.compile(r"(https://\S*?)lod=")

GOOD_CODE = 200
COLOR_GOOD = QColor(0, 200, 0)
COLOR_BAD = QColor(220, 0, 0)
COLOR_MIXED = QColor(240, 220, 0)
COLOR_BG = QColor(30, 30, 30)
COLOR_GRID = QColor(60, 60, 60)

STATE_GOOD = 1
STATE_BAD = 2
STATE_MIXED = 3


def parse_log(path: Path):
    """Yield (lod, x, y, good, url_prefix) tuples for each matching line.

    url_prefix is the URL truncated at "lod=" (exclusive), or None if the
    line has no https URL.
    """
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = LINE_RE.search(line)
            if not m:
                continue
            code = int(m.group(1))
            lod = int(m.group(2))
            x = int(m.group(3))
            y = int(m.group(4))
            url_prefix = None
            if "https://" in line:
                um = URL_RE.search(line)
                if um:
                    url_prefix = um.group(1)
            yield lod, x, y, code == GOOD_CODE, url_prefix


def render_lod(lod: int, tiles: dict, tile_dim: int, out_path: Path,
               label: bool = False) -> None:
    """Render a single LOD image. `tiles` maps (x, y) -> state int."""
    grid = 1 << lod  # 2^lod tiles per side
    size = grid * tile_dim
    image = QImage(size, size, QImage.Format_RGB32)
    image.fill(COLOR_BG)

    color_for = {STATE_GOOD: COLOR_GOOD, STATE_BAD: COLOR_BAD, STATE_MIXED: COLOR_MIXED}

    painter = QPainter(image)
    try:
        for (x, y), state in tiles.items():
            if x < 0 or y < 0 or x >= grid or y >= grid:
                continue
            painter.fillRect(
                x * tile_dim,
                y * tile_dim,
                tile_dim,
                tile_dim,
                color_for[state],
            )

        if tile_dim >= 4:
            painter.setPen(COLOR_GRID)
            for i in range(grid + 1):
                p = i * tile_dim
                painter.drawLine(p, 0, p, size)
                painter.drawLine(0, p, size, p)

        if label:
            text = f"LOD {lod}"
            font_px = max(12, size // 20)
            font = QFont()
            font.setPixelSize(font_px)
            font.setBold(True)
            painter.setFont(font)
            band = int(font_px * 1.6)
            rect = QRect(0, size - band, size, band)
            # painter.fillRect(rect, QColor(0, 0, 0, 160))
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(rect, Qt.AlignCenter, text)
    finally:
        painter.end()

    image.save(str(out_path), "PNG")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, action="append",
                        help="Input log file (repeatable; all inputs are merged)")
    parser.add_argument("--tile-dimension", type=int, default=8,
                        help="Pixel size of each tile square (default: 8)")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="Directory to write lod_NN.png files into")
    parser.add_argument("--min-lod", type=int, default=2, help="Minimum LOD to emit (default: 2)")
    parser.add_argument("--max-lod", type=int, default=None,
                        help="Maximum LOD to emit (default: highest seen in log)")
    parser.add_argument("--label", action="store_true",
                        help="Annotate each image with \"LOD nn\" near the bottom")
    args = parser.parse_args(argv)

    missing = [p for p in args.input if not p.is_file()]
    if missing:
        for p in missing:
            print(f"error: input file not found: {p}", file=sys.stderr)
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # If a tile has both good and bad hits, it becomes mixed (yellow).
    by_lod: dict[int, dict[tuple[int, int], int]] = {}
    good_urls: Counter = Counter()
    bad_urls: Counter = Counter()
    for path in args.input:
        for lod, x, y, good, url_prefix in parse_log(path):
            tiles = by_lod.setdefault(lod, {})
            key = (x, y)
            new = STATE_GOOD if good else STATE_BAD
            prev = tiles.get(key)
            if prev is None:
                tiles[key] = new
            elif prev != new:
                tiles[key] = STATE_MIXED
            if url_prefix is not None:
                (good_urls if good else bad_urls)[url_prefix] += 1

    if not by_lod:
        print("warning: no matching lines found", file=sys.stderr)
        return 1

    # Need a QGuiApplication for QImage/QPainter to work.
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    _ = app

    seen_max = max(by_lod)
    max_lod = args.max_lod if args.max_lod is not None else seen_max

    for lod in range(args.min_lod, max_lod + 1):
        tiles = by_lod.get(lod, {})
        out = args.output_dir / f"lod_{lod:02d}.png"
        render_lod(lod, tiles, args.tile_dimension, out, label=args.label)
        good_n = sum(1 for v in tiles.values() if v == STATE_GOOD)
        bad_n = sum(1 for v in tiles.values() if v == STATE_BAD)
        mixed_n = sum(1 for v in tiles.values() if v == STATE_MIXED)
        print(f"wrote {out}  (good={good_n} bad={bad_n} mixed={mixed_n} grid={1 << lod}x{1 << lod})")

    def dump(label: str, counter: Counter) -> None:
        if not counter:
            return
        print(f"\n{label} URL prefixes (lod.. stripped): "
              f"{len(counter)} unique, {sum(counter.values())} total")
        for url, count in counter.most_common():
            print(f"  {count:6d}  {url}")

    dump("GOOD (code=200)", good_urls)
    dump("BAD  (code!=200)", bad_urls)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
