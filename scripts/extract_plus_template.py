"""Extract the '+' symbol template from a known gain crop for template matching.

Uses the guts tile's speed gain crop (+8) where the + was correctly identified.
Saves a grayscale template of just the + symbol to data/digit_templates/plus.png.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
from PIL import Image

# Use a known-good crop that shows a clear "+"
CROP_PATH = Path("screenshots/debug_gains/tile_3_guts_gain_speed.png")
OUT_DIR = Path("data/digit_templates")
OUT_DIR.mkdir(parents=True, exist_ok=True)

img = cv2.imread(str(CROP_PATH))
if img is None:
    print(f"Cannot read {CROP_PATH}")
    sys.exit(1)

print(f"Crop size: {img.shape[1]}x{img.shape[0]}")

# Convert to grayscale using max channel (lightness) to preserve shape regardless of color
gray = np.max(img, axis=2)

# The + is in the left portion of the crop. Let's save the full gray for inspection
cv2.imwrite(str(OUT_DIR / "gain_crop_gray.png"), gray)

# Upscale 3x for inspection
gray_3x = cv2.resize(gray, (gray.shape[1] * 3, gray.shape[0] * 3), interpolation=cv2.INTER_LANCZOS4)
cv2.imwrite(str(OUT_DIR / "gain_crop_gray_3x.png"), gray_3x)

# The + symbol occupies roughly the left 45% of the crop
# Let's find it more precisely using thresholding
# The + is a bright symbol on a varied background
h, w = gray.shape
plus_region = gray[:, :int(w * 0.55)]

# Threshold to get the bright + shape
_, thresh = cv2.threshold(plus_region, 160, 255, cv2.THRESH_BINARY)

# Find contours to isolate the +
contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

if contours:
    # Get the largest contour (should be the +)
    largest = max(contours, key=cv2.contourArea)
    x, y, cw, ch = cv2.boundingRect(largest)
    print(f"Largest contour: x={x}, y={y}, w={cw}, h={ch}, area={cv2.contourArea(largest)}")

    # Extract the + with a small padding
    pad = 2
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(plus_region.shape[1], x + cw + pad)
    y2 = min(plus_region.shape[0], y + ch + pad)

    plus_template = gray[y1:y2, x1:x2]
    cv2.imwrite(str(OUT_DIR / "plus.png"), plus_template)
    print(f"Saved plus.png ({x2-x1}x{y2-y1}px)")

    # Also save 3x version
    plus_3x = cv2.resize(plus_template, (plus_template.shape[1] * 3, plus_template.shape[0] * 3),
                          interpolation=cv2.INTER_LANCZOS4)
    cv2.imwrite(str(OUT_DIR / "plus_3x.png"), plus_3x)
    print(f"Saved plus_3x.png")

    # Show where the digit starts (right edge of +)
    print(f"\nDigit starts at x={x2} out of crop width {w}")
    print(f"Ratio: {x2/w:.2f} (crop from {x2/w:.0%} rightward to get just the digit)")
else:
    print("No contours found - check threshold")

# Also save the threshold for inspection
cv2.imwrite(str(OUT_DIR / "gain_crop_thresh.png"), thresh)
print(f"\nAll outputs in {OUT_DIR}/")
