"""Convert icon.png -> icon.ico for the PyInstaller build.

Run by build.bat before PyInstaller if icon.png is present. Produces a
multi-size .ico file (16, 32, 48, 64, 128, 256 px square) so Windows
Explorer / taskbar / start menu each get a crisp icon at their native
rendering size.

Handles non-square inputs by padding with a transparent background --
the icon stays centered and its proportions are preserved.
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()
PNG = HERE / "icon.png"
ICO = HERE / "icon.ico"
SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main():
    if not PNG.exists():
        print(f"[build_icon] no {PNG.name} present, skipping icon conversion")
        return 0
    try:
        from PIL import Image
    except ImportError:
        print("[build_icon] ERROR: Pillow not installed. Run: pip install pillow", file=sys.stderr)
        return 1

    # Skip if .ico is already newer than .png (saves a few seconds on
    # repeat builds when the icon hasn't changed)
    if ICO.exists() and ICO.stat().st_mtime >= PNG.stat().st_mtime:
        print(f"[build_icon] {ICO.name} is up to date, skipping conversion")
        return 0

    img = Image.open(PNG).convert("RGBA")
    w, h = img.size
    print(f"[build_icon] source: {PNG.name} ({w}x{h})")

    # Pad to square so aspect ratio is preserved across all sizes.
    if w != h:
        side = max(w, h)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(img, ((side - w) // 2, (side - h) // 2), img)
        img = square
        print(f"[build_icon] padded to square: {side}x{side}")

    # Pillow's save() with sizes= produces a multi-resolution .ico.
    img.save(ICO, format="ICO", sizes=SIZES)
    print(f"[build_icon] wrote {ICO.name} with sizes: {', '.join(f'{s[0]}px' for s in SIZES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
