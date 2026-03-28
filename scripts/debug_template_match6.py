"""Template matching v6 — simple approach: grayscale on color-isolated images, no mask.

Both template and region get color-isolated to black background,
then converted to grayscale for matching.
"""
import cv2
import numpy as np
from pathlib import Path

TMPL_DIR = Path("data/digit_templates")


def isolate_warm_colors(bgr):
    """Zero out non-orange/yellow/white pixels -> black background."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    orange = cv2.inRange(hsv, (5, 60, 130), (35, 255, 255))
    white = cv2.inRange(hsv, (0, 0, 210), (180, 50, 255))
    mask = cv2.bitwise_or(orange, white)
    return cv2.bitwise_and(bgr, bgr, mask=mask)


def sprite_to_gray(rgba):
    """Composite RGBA sprite onto black, convert to gray."""
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    bgr = (rgba[:, :, :3].astype(np.float32) * alpha).astype(np.uint8)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


def load_templates(scale):
    templates = {}
    for name in ["plus"] + [str(i) for i in range(10)]:
        path = TMPL_DIR / f"digit_{name}.png"
        rgba = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if rgba is None:
            continue
        h = max(1, int(rgba.shape[0] * scale))
        w = max(1, int(rgba.shape[1] * scale))
        resized = cv2.resize(rgba, (w, h), interpolation=cv2.INTER_AREA)
        gray = sprite_to_gray(resized)
        label = "+" if name == "plus" else name
        templates[label] = (gray, w, h)
    return templates


def match_gain(region_bgr, templates, threshold=0.50):
    """Match digit templates against a color-isolated gain region."""
    isolated = isolate_warm_colors(region_bgr)
    region_gray = cv2.cvtColor(isolated, cv2.COLOR_BGR2GRAY)

    matches = []
    for label, (tmpl, tw, th) in templates.items():
        if th > region_gray.shape[0] or tw > region_gray.shape[1]:
            continue
        result = cv2.matchTemplate(region_gray, tmpl, cv2.TM_CCOEFF_NORMED)
        locations = np.where(result >= threshold)
        for y, x in zip(*locations):
            matches.append((x, label, float(result[y, x]), tw))

    matches.sort(key=lambda m: (m[0], -m[2]))
    filtered = []
    for m in matches:
        if filtered and abs(m[0] - filtered[-1][0]) < filtered[-1][3] * 0.5:
            if m[2] > filtered[-1][2]:
                filtered[-1] = m
        else:
            filtered.append(m)
    return filtered


# Test 1: Full frame regions (known: speed=+13, guts=+5)
frame = cv2.imread("screenshots/debug_gains/stat_selection.png")
gain_regions = {
    "speed(+13)":  (20, 1185, 200, 1255),
    "guts(+5)":    (510, 1185, 700, 1255),
}

print("=== Full frame regions ===")
for scale_pct in range(30, 75, 5):
    scale = scale_pct / 100.0
    templates = load_templates(scale)
    for stat, (x1, y1, x2, y2) in gain_regions.items():
        region = frame[y1:y2, x1:x2]
        matches = match_gain(region, templates, threshold=0.50)
        text = "".join(m[1] for m in matches)
        if text:
            scores = [f"{m[1]}={m[2]:.3f}" for m in matches]
            print(f"  {scale_pct}% {stat}: '{text}'  ({', '.join(scores)})")


# Test 2: Known gain crops (known: +11 and +5)
print("\n=== Known gain crops ===")
crops = {
    "+11": "data/gain_ocr_samples/1774657070255_175_1185.png",
    "+5":  "data/gain_ocr_samples/1774657071547_510_1185.png",
}

for expected, path in crops.items():
    crop = cv2.imread(path)
    if crop is None:
        continue
    print(f"\n  Expected: {expected}")
    for scale_pct in range(30, 75, 5):
        scale = scale_pct / 100.0
        templates = load_templates(scale)
        matches = match_gain(crop, templates, threshold=0.50)
        text = "".join(m[1] for m in matches)
        if text:
            scores = [f"{m[1]}={m[2]:.3f}" for m in matches]
            print(f"    {scale_pct}%: '{text}'  ({', '.join(scores)})")


# Save debug images
for stat, (x1, y1, x2, y2) in gain_regions.items():
    region = frame[y1:y2, x1:x2]
    isolated = isolate_warm_colors(region)
    tag = stat.split("(")[0]
    cv2.imwrite(f"/tmp/v6_isolated_{tag}.png", isolated)
    cv2.imwrite(f"/tmp/v6_gray_{tag}.png", cv2.cvtColor(isolated, cv2.COLOR_BGR2GRAY))

# Also save template previews at 50%
templates = load_templates(0.50)
for label, (gray, w, h) in templates.items():
    tag = "plus" if label == "+" else label
    cv2.imwrite(f"/tmp/v6_tmpl_{tag}.png", gray)
