"""Broader scan for the Race button."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image

img = Image.open("screenshots/run_log/turn_1774604717.png")

# Scan entire bottom third for any green-ish button
print("Full scan y=1400-1920 for green button pixels:")
for y in range(1400, 1920, 5):
    green_xs = []
    for x in range(100, 1000, 5):
        r, g, b = img.getpixel((x, y))[:3]
        # Green button: high green, lower red, low blue
        if g > 150 and g > r and g > b and (g - r) > 30:
            green_xs.append(x)
    if len(green_xs) >= 5:
        print(f"  y={y}: green at x={min(green_xs)}-{max(green_xs)} ({len(green_xs)} px)")

# Let's also look for text "Race" by checking for specific colored text
# The Race button text would be white on green background
print("\nSampling specific rows for the Race button area:")
for y in [1740, 1745, 1750, 1755, 1760, 1765, 1770, 1775, 1780]:
    pixels = []
    for x in range(300, 800, 20):
        r, g, b = img.getpixel((x, y))[:3]
        pixels.append(f"({r},{g},{b})")
    print(f"  y={y}: {' '.join(pixels)}")
