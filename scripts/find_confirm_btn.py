"""Find the green Race confirm button position."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image

img = Image.open("screenshots/run_log/turn_1774604810.png")

# Scan for the green Race button in the popup
print("Scanning for green confirm button (y=1100-1400):")
for y in range(1100, 1400, 5):
    green_xs = []
    for x in range(400, 900, 5):
        r, g, b = img.getpixel((x, y))[:3]
        if g > 150 and g > r and g > b and (g - r) > 30:
            green_xs.append(x)
    if len(green_xs) >= 3:
        print(f"  y={y}: green at x={min(green_xs)}-{max(green_xs)} ({len(green_xs)} px)")
