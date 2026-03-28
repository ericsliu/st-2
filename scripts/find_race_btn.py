"""Find the green Race button on the race list screen."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image

img = Image.open("screenshots/run_log/turn_1774604717.png")

# Scan for green pixels in the bottom area where the Race button should be
print("Scanning for green button pixels (y=1700-1870):")
for y in range(1700, 1870, 5):
    green_xs = []
    for x in range(200, 900, 5):
        r, g, b = img.getpixel((x, y))[:3]
        if g > 160 and r < 150 and b < 100:
            green_xs.append(x)
    if green_xs:
        print(f"  y={y}: green at x={min(green_xs)}-{max(green_xs)} ({len(green_xs)} pixels)")

# Also check the exact pixels at our tap targets
for x, y in [(540, 1755), (540, 1810), (540, 1780), (540, 1790)]:
    r, g, b = img.getpixel((x, y))[:3]
    print(f"  ({x},{y}): RGB=({r},{g},{b})")
