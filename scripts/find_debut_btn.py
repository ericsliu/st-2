"""Find the Debut button by scanning for its distinctive colors."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

src = sys.argv[1] if len(sys.argv) > 1 else "/tmp/action_screen.png"
frame = cv2.imread(src)
if frame is None:
    print(f"Cannot read {src}")
    sys.exit(1)

h, w = frame.shape[:2]
print(f"Image: {w}x{h}")

# Save several horizontal strips to find where Debut button is
for y_start in [900, 950, 1000, 1050, 1100, 1150, 1200]:
    strip = frame[y_start:y_start+60, 0:400]
    cv2.imwrite(f"/tmp/strip_{y_start}.png", strip)

# Also save the full left side from 900-1300
left_panel = frame[900:1300, 0:400]
cv2.imwrite("/tmp/debut_search_area.png", left_panel)
print("Saved /tmp/debut_search_area.png (y=900-1300, x=0-400)")
