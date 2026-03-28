"""Figure out the right scale for template matching by testing against known crops."""
import cv2
import numpy as np
from pathlib import Path

# Load a known gain crop with "+11" visible
crop = cv2.imread("data/gain_ocr_samples/1774657070255_175_1185.png")
print(f"Crop size: {crop.shape[1]}x{crop.shape[0]}")

# Load the "1" template (has alpha channel)
tmpl_rgba = cv2.imread("data/digit_templates/digit_1.png", cv2.IMREAD_UNCHANGED)
print(f"Template 1 size: {tmpl_rgba.shape[1]}x{tmpl_rgba.shape[0]}")

# Load the "+" template
plus_rgba = cv2.imread("data/digit_templates/digit_plus.png", cv2.IMREAD_UNCHANGED)
print(f"Template + size: {plus_rgba.shape[1]}x{plus_rgba.shape[0]}")

# Try different scales and find best match
best_scale = 0
best_val = 0

for scale_pct in range(30, 80, 2):
    scale = scale_pct / 100.0
    h = int(tmpl_rgba.shape[0] * scale)
    w = int(tmpl_rgba.shape[1] * scale)
    if h > crop.shape[0] or w > crop.shape[1]:
        continue

    # Resize template
    resized = cv2.resize(tmpl_rgba, (w, h), interpolation=cv2.INTER_AREA)

    # Split alpha
    tmpl_bgr = resized[:, :, :3]
    tmpl_alpha = resized[:, :, 3]

    # Create mask from alpha
    mask = (tmpl_alpha > 128).astype(np.uint8) * 255

    # Match
    result = cv2.matchTemplate(crop, tmpl_bgr, cv2.TM_CCORR_NORMED, mask=mask)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

    if max_val > best_val:
        best_val = max_val
        best_scale = scale_pct
        best_loc = max_loc

    if scale_pct % 10 == 0:
        print(f"  scale={scale_pct}%: max_val={max_val:.4f} at {max_loc}")

print(f"\nBest scale: {best_scale}% with match={best_val:.4f} at {best_loc}")
print(f"Template at {best_scale}%: {int(tmpl_rgba.shape[1]*best_scale/100)}x{int(tmpl_rgba.shape[0]*best_scale/100)}")
