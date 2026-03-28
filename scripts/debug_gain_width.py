"""Debug: check gain number positions to find proper region widths."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

# Use the stamina tile screenshot where +11 is visible
src = sys.argv[1] if len(sys.argv) > 1 else "/tmp/tile_stamina.png"
frame = cv2.imread(src)
if frame is None:
    print(f"Cannot read {src}")
    sys.exit(1)

# Save the full gain bar area with wider regions
# Gain numbers appear in y=1185-1255 area
strip = frame[1175:1265, 0:1080]
cv2.imwrite("/tmp/gain_full_strip.png", strip)
print("Saved /tmp/gain_full_strip.png (y=1175-1265, full width)")

# Also save wider crops per stat to find where numbers actually end
regions = {
    "speed":   (0, 1175, 210, 1265),
    "stamina": (140, 1175, 380, 1265),
    "power":   (300, 1175, 550, 1265),
    "guts":    (470, 1175, 710, 1265),
    "wit":     (630, 1175, 880, 1265),
}
for name, (x1, y1, x2, y2) in regions.items():
    roi = frame[y1:y2, x1:x2]
    cv2.imwrite(f"/tmp/gain_wide_{name}.png", roi)
    print(f"  {name}: saved wide crop ({x1},{y1},{x2},{y2})")
