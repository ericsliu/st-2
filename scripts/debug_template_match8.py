"""Template matching v8 — orange-only segmentation, better glyph separation.

Key fix: use only orange pixels for connected components (white connects glyphs).
Also try using vertical projection (column sums) to find gaps between glyphs.
"""
import cv2
import numpy as np
from pathlib import Path

TMPL_DIR = Path("data/digit_templates")
TARGET_H = 48


def load_template_masks():
    """Load template masks — use the orange fill area only (skip white outline)."""
    templates = {}
    for name in ["plus"] + [str(i) for i in range(10)]:
        path = TMPL_DIR / f"digit_{name}.png"
        rgba = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if rgba is None:
            continue

        # Convert to HSV to extract just the orange/yellow fill
        bgr = rgba[:, :, :3]
        alpha = rgba[:, :, 3]
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

        # Orange fill in the sprite
        orange = cv2.inRange(hsv, (5, 50, 100), (40, 255, 255))
        # Intersect with alpha to remove background
        orange = cv2.bitwise_and(orange, alpha)

        # Also keep white that's inside the glyph bounds
        white = cv2.inRange(hsv, (0, 0, 200), (180, 60, 255))
        white = cv2.bitwise_and(white, alpha)
        mask = cv2.bitwise_or(orange, white)
        _, mask = cv2.threshold(mask, 50, 255, cv2.THRESH_BINARY)

        # Normalize to TARGET_H
        h, w = mask.shape
        scale = TARGET_H / h
        new_w = max(1, int(w * scale))
        resized = cv2.resize(mask, (new_w, TARGET_H), interpolation=cv2.INTER_AREA)
        _, resized = cv2.threshold(resized, 128, 255, cv2.THRESH_BINARY)

        label = "+" if name == "plus" else name
        templates[label] = resized
    return templates


def segment_glyphs_projection(region_bgr):
    """Segment glyphs using vertical projection of orange pixels only."""
    hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)

    # Only orange — no white, to avoid connecting adjacent glyphs
    orange = cv2.inRange(hsv, (5, 50, 120), (38, 255, 255))

    h_region = region_bgr.shape[0]

    # Vertical projection: sum of orange pixels per column
    col_sums = np.sum(orange > 0, axis=0)

    # Find runs of columns with orange pixels
    in_glyph = False
    glyph_starts = []
    for x in range(len(col_sums)):
        if col_sums[x] > 2 and not in_glyph:
            glyph_starts.append(x)
            in_glyph = True
        elif col_sums[x] <= 2 and in_glyph:
            glyph_starts[-1] = (glyph_starts[-1], x)
            in_glyph = False
    if in_glyph:
        glyph_starts[-1] = (glyph_starts[-1], len(col_sums))

    # For each column range, find the vertical extent
    glyphs = []
    for start, end in glyph_starts:
        if not isinstance(start, int):
            continue
        strip = orange[:, start:end]
        row_sums = np.sum(strip > 0, axis=1)
        ys = np.where(row_sums > 0)[0]
        if len(ys) == 0:
            continue
        y_top = max(0, ys[0] - 1)
        y_bot = min(h_region, ys[-1] + 2)
        w = end - start
        h = y_bot - y_top

        # Filter: must be reasonably tall
        if h > h_region * 0.25 and w > 3:
            mask = orange[y_top:y_bot, start:end]
            glyphs.append((start, y_top, w, h, mask))

    return glyphs


def match_glyph(glyph_mask, templates):
    """Match a single glyph against all templates using IoU."""
    h, w = glyph_mask.shape
    if h < 5 or w < 3:
        return "?", 0.0

    scale = TARGET_H / h
    new_w = max(1, int(w * scale))
    resized = cv2.resize(glyph_mask, (new_w, TARGET_H), interpolation=cv2.INTER_AREA)
    _, resized = cv2.threshold(resized, 128, 255, cv2.THRESH_BINARY)

    best_label = "?"
    best_score = -1

    for label, tmpl in templates.items():
        max_w = max(resized.shape[1], tmpl.shape[1]) + 4
        glyph_padded = np.zeros((TARGET_H, max_w), dtype=np.uint8)
        tmpl_padded = np.zeros((TARGET_H, max_w), dtype=np.uint8)

        gx = (max_w - resized.shape[1]) // 2
        glyph_padded[:, gx:gx+resized.shape[1]] = resized
        tx = (max_w - tmpl.shape[1]) // 2
        tmpl_padded[:, tx:tx+tmpl.shape[1]] = tmpl

        intersection = np.count_nonzero(glyph_padded & tmpl_padded)
        union = np.count_nonzero(glyph_padded | tmpl_padded)
        if union == 0:
            continue
        iou = intersection / union

        if iou > best_score:
            best_score = iou
            best_label = label

    return best_label, best_score


def read_gain_value(region_bgr, templates, min_iou=0.3):
    """Read a gain value from a region."""
    glyphs = segment_glyphs_projection(region_bgr)
    if not glyphs:
        return None, []

    results = []
    for x, y, w, h, mask in glyphs:
        label, score = match_glyph(mask, templates)
        results.append((x, label, score, w, h))

    # Filter by minimum IoU
    results = [r for r in results if r[2] >= min_iou]

    # Parse: expect "+digits"
    text = "".join(r[1] for r in results)
    if text.startswith("+") and len(text) >= 2:
        try:
            return int(text[1:]), results
        except ValueError:
            pass
    # Try without plus
    digits_only = "".join(c for c in text if c.isdigit())
    if digits_only:
        try:
            val = int(digits_only)
            if 1 <= val <= 50:
                return val, results
        except ValueError:
            pass

    return None, results


# Load templates
templates = load_template_masks()
print("Templates loaded:")
for label, tmpl in sorted(templates.items()):
    print(f"  '{label}': {tmpl.shape[1]}x{tmpl.shape[0]}")

# Test 1: Full frame (speed=+13, guts=+5)
frame = cv2.imread("screenshots/debug_gains/stat_selection.png")
gain_regions = {
    "speed(+13)":  (20, 1185, 200, 1255),
    "stamina":     (175, 1185, 365, 1255),
    "power":       (335, 1185, 540, 1255),
    "guts(+5)":    (510, 1185, 700, 1255),
    "wit":         (670, 1185, 870, 1255),
}

print("\n=== Full frame regions ===")
for stat, (x1, y1, x2, y2) in gain_regions.items():
    region = frame[y1:y2, x1:x2]
    value, results = read_gain_value(region, templates)
    if results:
        text = "".join(r[1] for r in results)
        details = [f"'{r[1]}'={r[2]:.3f} ({r[3]}x{r[4]})" for r in results]
        print(f"  {stat:15s}: value={value}  text='{text}'  [{', '.join(details)}]")
    else:
        print(f"  {stat:15s}: (no glyphs)")

# Test 2: Known crops
print("\n=== Known gain crops ===")
crops = {
    "+11": "data/gain_ocr_samples/1774657070255_175_1185.png",
    "+5":  "data/gain_ocr_samples/1774657071547_510_1185.png",
}
for expected, path in crops.items():
    crop = cv2.imread(path)
    if crop is None:
        continue
    value, results = read_gain_value(crop, templates)
    text = "".join(r[1] for r in results) if results else ""
    details = [f"'{r[1]}'={r[2]:.3f} ({r[3]}x{r[4]})" for r in results]
    print(f"  expected={expected:5s}: value={value}  text='{text}'  [{', '.join(details)}]")

# Debug: save segmented glyphs for speed region
print("\n=== Debug: speed region glyphs ===")
region = frame[1185:1255, 20:200]
glyphs = segment_glyphs_projection(region)
for i, (x, y, w, h, mask) in enumerate(glyphs):
    cv2.imwrite(f"/tmp/v8_glyph_{i}.png", mask)
    label, score = match_glyph(mask, templates)
    print(f"  glyph {i}: x={x} size={w}x{h} -> '{label}' (IoU={score:.3f})")
