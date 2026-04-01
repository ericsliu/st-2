"""Debug energy bar pixel detection."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image

img = Image.open("screenshots/run_log/dry_run.png")

BAR_Y = 236
BAR_X_START = 340
BAR_X_END = 750

filled = 0
total = 0
for x in range(BAR_X_START, BAR_X_END, 5):
    r, g, b = img.getpixel((x, BAR_Y))[:3]
    total += 1
    is_gray = abs(r - g) < 15 and abs(g - b) < 15 and 100 < r < 140
    is_white = r > 240 and g > 240 and b > 240
    is_filled = not is_gray and not is_white
    if is_filled:
        filled += 1
    if x % 20 == 0:
        marker = "FILLED" if is_filled else ("GRAY" if is_gray else "WHITE" if is_white else "???")
        print(f"  x={x} RGB=({r},{g},{b}) {marker}")

print(f"\nFilled: {filled}/{total} = {int(100 * filled / max(total, 1))}%")
