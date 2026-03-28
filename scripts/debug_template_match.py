"""Test template matching against the full stat_selection screenshot.

Known values from that screenshot: speed=+13, guts=+5
"""
import cv2
import numpy as np
from pathlib import Path

TMPL_DIR = Path("data/digit_templates")

frame = cv2.imread("screenshots/debug_gains/stat_selection.png")
print(f"Frame: {frame.shape[1]}x{frame.shape[0]}")

# Gain regions from regions.py
gain_regions = {
    "speed":   (20, 1185, 200, 1255),
    "stamina": (175, 1185, 365, 1255),
    "power":   (335, 1185, 540, 1255),
    "guts":    (510, 1185, 700, 1255),
    "wit":     (670, 1185, 870, 1255),
}


def load_templates(scale):
    """Load and scale all digit templates."""
    templates = {}
    for name in ["plus"] + [str(i) for i in range(10)]:
        fname = f"digit_{name}.png"
        path = TMPL_DIR / fname
        rgba = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if rgba is None:
            continue
        h = max(1, int(rgba.shape[0] * scale))
        w = max(1, int(rgba.shape[1] * scale))
        resized = cv2.resize(rgba, (w, h), interpolation=cv2.INTER_AREA)
        bgr = resized[:, :, :3]
        alpha = resized[:, :, 3]
        mask = (alpha > 128).astype(np.uint8) * 255
        label = "+" if name == "plus" else name
        templates[label] = (bgr, mask, w, h)
    return templates


def match_region(region_img, templates, threshold=0.85):
    """Find all digit matches in a region, return sorted by x position."""
    matches = []
    for label, (tmpl, mask, tw, th) in templates.items():
        if th > region_img.shape[0] or tw > region_img.shape[1]:
            continue
        result = cv2.matchTemplate(region_img, tmpl, cv2.TM_CCORR_NORMED, mask=mask)
        locations = np.where(result >= threshold)
        for y, x in zip(*locations):
            matches.append((x, label, float(result[y, x]), tw))

    # Sort by x, then deduplicate overlapping matches (keep highest score)
    matches.sort(key=lambda m: m[0])
    filtered = []
    for m in matches:
        if filtered and abs(m[0] - filtered[-1][0]) < filtered[-1][3] * 0.5:
            # Overlapping — keep higher score
            if m[2] > filtered[-1][2]:
                filtered[-1] = m
        else:
            filtered.append(m)
    return filtered


# Try multiple scales
for scale_pct in [35, 40, 45, 50, 55, 60]:
    scale = scale_pct / 100.0
    templates = load_templates(scale)
    print(f"\n=== Scale {scale_pct}% ===")

    for stat, (x1, y1, x2, y2) in gain_regions.items():
        region = frame[y1:y2, x1:x2]
        matches = match_region(region, templates)
        text = "".join(m[1] for m in matches)
        scores = [f"{m[1]}={m[2]:.3f}@x{m[0]}" for m in matches]
        if text:
            print(f"  {stat:8s}: '{text}'  ({', '.join(scores)})")
