"""Debug energy bar pixel reading."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image

img = Image.open("screenshots/run_log/dry_run.png")

BAR_Y = 236
BAR_X_START = 340
BAR_X_END = 750

print(f"Scanning energy bar at y={BAR_Y}, x={BAR_X_START}-{BAR_X_END}")
print()

green_count = 0
total = 0
for x in range(BAR_X_START, BAR_X_END, 5):
    r, g, b = img.getpixel((x, BAR_Y))[:3]
    total += 1
    is_green = g > 130 and g > r + 30 and b < 150
    if is_green:
        green_count += 1
    if x % 20 == 0:
        print(f"  x={x} RGB=({r},{g},{b}) {'GREEN' if is_green else ''}")

print(f"\nGreen: {green_count}/{total} = {int(100 * green_count / max(total, 1))}%")

# Try different y values to find the bar
print("\nScanning different y values:")
for y in range(220, 260, 2):
    gc = 0
    t = 0
    for x in range(BAR_X_START, BAR_X_END, 5):
        r, g, b = img.getpixel((x, y))[:3]
        t += 1
        if g > 130 and g > r + 30 and b < 150:
            gc += 1
    pct = int(100 * gc / max(t, 1))
    if pct > 0:
        print(f"  y={y}: {pct}%")
