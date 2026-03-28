"""Test template matching v4 — HSV color isolation then shape matching.

Strategy:
1. Isolate orange/yellow gain pixels via HSV filter (same colors as sprites)
2. Create binary masks for both template and region
3. Match the binary shapes
"""
import cv2
import numpy as np
from pathlib import Path

TMPL_DIR = Path("data/digit_templates")

frame = cv2.imread("screenshots/debug_gains/stat_selection.png")
print(f"Frame: {frame.shape[1]}x{frame.shape[0]}")

# Known: speed=+13, guts=+5
gain_regions = {
    "speed":   (20, 1185, 200, 1255),
    "stamina": (175, 1185, 365, 1255),
    "power":   (335, 1185, 540, 1255),
    "guts":    (510, 1185, 700, 1255),
    "wit":     (670, 1185, 870, 1255),
}


def make_sprite_mask(rgba):
    """Convert sprite RGBA to binary mask using alpha channel."""
    return (rgba[:, :, 3] > 128).astype(np.uint8) * 255


def make_region_mask(bgr):
    """Isolate orange/yellow gain digits from a region using HSV filtering.

    The gain numbers are rendered with:
    - Orange/yellow fill: H≈10-35, S>80, V>150
    - White outline/highlight: S<40, V>200
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    # Orange/yellow fill
    orange = cv2.inRange(hsv, (5, 80, 150), (35, 255, 255))

    # White outline
    white = cv2.inRange(hsv, (0, 0, 200), (180, 40, 255))

    combined = cv2.bitwise_or(orange, white)
    return combined


def load_templates(scale):
    """Load templates as binary masks at given scale."""
    templates = {}
    for name in ["plus"] + [str(i) for i in range(10)]:
        path = TMPL_DIR / f"digit_{name}.png"
        rgba = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if rgba is None:
            continue
        h = max(1, int(rgba.shape[0] * scale))
        w = max(1, int(rgba.shape[1] * scale))
        resized = cv2.resize(rgba, (w, h), interpolation=cv2.INTER_AREA)
        mask = make_sprite_mask(resized)

        label = "+" if name == "plus" else name
        templates[label] = (mask, w, h)
    return templates


def match_region(region_bgr, templates, threshold=0.60):
    """Match binary template shapes against HSV-filtered region."""
    region_mask = make_region_mask(region_bgr)

    matches = []
    for label, (tmpl, tw, th) in templates.items():
        if th > region_mask.shape[0] or tw > region_mask.shape[1]:
            continue
        result = cv2.matchTemplate(region_mask, tmpl, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
        if max_val >= threshold:
            matches.append((max_loc[0], label, max_val, tw))
        # Also check for secondary matches above threshold
        locations = np.where(result >= threshold)
        for y, x in zip(*locations):
            val = float(result[y, x])
            matches.append((x, label, val, tw))

    # Deduplicate
    matches.sort(key=lambda m: (m[0], -m[2]))
    filtered = []
    for m in matches:
        if filtered and abs(m[0] - filtered[-1][0]) < filtered[-1][3] * 0.5:
            if m[2] > filtered[-1][2]:
                filtered[-1] = m
        else:
            filtered.append(m)
    return filtered


for scale_pct in range(30, 75, 5):
    scale = scale_pct / 100.0
    templates = load_templates(scale)
    any_match = False

    results = []
    for stat, (x1, y1, x2, y2) in gain_regions.items():
        region = frame[y1:y2, x1:x2]
        matches = match_region(region, templates)
        text = "".join(m[1] for m in matches)
        if text:
            scores = [f"{m[1]}={m[2]:.3f}@x{m[0]}" for m in matches]
            results.append(f"  {stat:8s}: '{text}'  ({', '.join(scores)})")
            any_match = True

    if any_match:
        print(f"\n=== Scale {scale_pct}% ===")
        for r in results:
            print(r)

# Also save debug images at scale 50%
scale = 0.50
print("\n\n=== Debug images at 50% ===")
for stat, (x1, y1, x2, y2) in gain_regions.items():
    region = frame[y1:y2, x1:x2]
    region_mask = make_region_mask(region)
    cv2.imwrite(f"/tmp/gain_mask_{stat}.png", region_mask)
    cv2.imwrite(f"/tmp/gain_region_{stat}.png", region)
    print(f"  {stat}: mask saved, white_pixels={np.count_nonzero(region_mask)}")
