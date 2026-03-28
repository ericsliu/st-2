"""Test template matching v3 — grayscale, no mask, alpha-composited on black."""
import cv2
import numpy as np
from pathlib import Path

TMPL_DIR = Path("data/digit_templates")

frame = cv2.imread("screenshots/debug_gains/stat_selection.png")
print(f"Frame: {frame.shape[1]}x{frame.shape[0]}")

# Known: speed=+13, guts=+5
gain_regions = {
    "speed":   (20, 1185, 200, 1255),
    "guts":    (510, 1185, 700, 1255),
}


def load_templates(scale):
    """Load templates composited on black, converted to grayscale."""
    templates = {}
    for name in ["plus"] + [str(i) for i in range(10)]:
        path = TMPL_DIR / f"digit_{name}.png"
        rgba = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if rgba is None:
            continue
        h = max(1, int(rgba.shape[0] * scale))
        w = max(1, int(rgba.shape[1] * scale))
        resized = cv2.resize(rgba, (w, h), interpolation=cv2.INTER_AREA)

        # Composite onto black (the gain numbers have bright orange/yellow
        # pixels against dark/varied backgrounds)
        alpha = resized[:, :, 3:4].astype(np.float32) / 255.0
        bgr = resized[:, :, :3].astype(np.float32)
        composited = (bgr * alpha).astype(np.uint8)

        # Convert to single-channel lightness (max of BGR)
        lightness = np.max(composited, axis=2)

        label = "+" if name == "plus" else name
        templates[label] = (lightness, w, h)
    return templates


def match_region(region_bgr, templates, threshold=0.65):
    """Match templates against a region converted to lightness."""
    # Convert region to lightness
    region_light = np.max(region_bgr, axis=2)

    matches = []
    for label, (tmpl, tw, th) in templates.items():
        if th > region_light.shape[0] or tw > region_light.shape[1]:
            continue
        result = cv2.matchTemplate(region_light, tmpl, cv2.TM_CCOEFF_NORMED)
        locations = np.where(result >= threshold)
        for y, x in zip(*locations):
            matches.append((x, label, float(result[y, x]), tw))

    matches.sort(key=lambda m: (m[0], -m[2]))
    filtered = []
    for m in matches:
        if filtered and abs(m[0] - filtered[-1][0]) < filtered[-1][3] * 0.6:
            if m[2] > filtered[-1][2]:
                filtered[-1] = m
        else:
            filtered.append(m)
    return filtered


for scale_pct in range(30, 75, 5):
    scale = scale_pct / 100.0
    templates = load_templates(scale)

    for stat, (x1, y1, x2, y2) in gain_regions.items():
        region = frame[y1:y2, x1:x2]
        matches = match_region(region, templates)
        text = "".join(m[1] for m in matches)
        scores = [f"{m[1]}={m[2]:.3f}@x{m[0]}" for m in matches]
        if text:
            print(f"  scale={scale_pct}% {stat:8s}: '{text}'  ({', '.join(scores)})")
