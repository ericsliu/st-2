"""Find the Close button on Result Pts popup."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image

img = Image.open("screenshots/run_log/turn_1774605106.png")

# The Close button is a white rounded rectangle with "Close" text
# Look for a concentrated white rectangle in y=1050-1200 area
print("Scanning for Close button (white rect with border):")
for y in range(1050, 1250, 5):
    white_xs = []
    for x in range(300, 750, 5):
        r, g, b = img.getpixel((x, y))[:3]
        if r > 230 and g > 230 and b > 230:
            white_xs.append(x)
    if len(white_xs) >= 5:
        print(f"  y={y}: white at x={min(white_xs)}-{max(white_xs)} ({len(white_xs)} px)")

# Also sample where I tapped
r, g, b = img.getpixel((540, 1135))[:3]
print(f"\nPixel at (540,1135): RGB=({r},{g},{b})")
