"""Find View Results button position on pre-race screen."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image

img = Image.open("screenshots/run_log/turn_1774604875.png")

# View Results is a white button on the left, Race is green on the right
# Both at the bottom of the screen
print("Scanning bottom for white button (View Results) and green button (Race):")
for y in range(1750, 1920, 5):
    whites = []
    greens = []
    for x in range(50, 1000, 5):
        r, g, b = img.getpixel((x, y))[:3]
        if r > 230 and g > 230 and b > 230:
            whites.append(x)
        if g > 150 and g > r and g > b and (g - r) > 30:
            greens.append(x)
    if whites or greens:
        parts = []
        if whites:
            parts.append(f"white x={min(whites)}-{max(whites)}")
        if greens:
            parts.append(f"green x={min(greens)}-{max(greens)}")
        print(f"  y={y}: {', '.join(parts)}")
