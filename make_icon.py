"""Rebuild log-viewer.ico from log-viewer.png with all standard Windows
icon sizes embedded.

Windows pulls icons from a .ico file at different sizes depending on the
context — 16×16 for the window title bar, 32×32 for the taskbar and
Explorer's properties dialog, 48×48 for the icon-view in Explorer, 256×256
for thumbnails and tile views. A .ico that only embeds one size forces
Windows to downscale on the fly, which usually looks blurry or makes the
icon fall back to a generic placeholder.

Run this whenever log-viewer.png changes:

    python make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required. Install with: pip install Pillow")

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "log-viewer.png"
DST = ROOT / "log-viewer.ico"

# Standard Windows icon sizes. 16/24/32 cover title bar, system tray,
# small Explorer views; 48/64 cover larger Explorer views; 128/256 cover
# thumbnails, Start menu tiles, high-DPI displays.
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> None:
    if not SRC.exists():
        sys.exit(f"Source PNG not found: {SRC}")

    img = Image.open(SRC)
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # Pillow's ICO writer accepts a sizes= list and downsamples each frame
    # from the source image using LANCZOS. The source should be at least
    # 256x256 — anything smaller and the large frames will be upscaled.
    if min(img.size) < 256:
        print(
            f"Warning: source is {img.size}; the 256x256 frame will be "
            "upscaled and may look soft.",
            file=sys.stderr,
        )

    img.save(DST, format="ICO", sizes=SIZES)
    print(f"Wrote {DST} with frames: {', '.join(f'{w}x{h}' for w, h in SIZES)}")


if __name__ == "__main__":
    main()
