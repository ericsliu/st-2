"""Template matching v7 — segment individual glyphs, then match each one.

Strategy:
1. HSV-isolate orange/white gain pixels
2. Find connected components (each digit/plus is one blob)
3. Normalize each blob to fixed height
4. Compare against normalized templates using correlation
"""
import cv2
import numpy as np
from pathlib import Path

TMPL_DIR = Path("data/digit_templates")
TARGET_H = 48  # normalize all glyphs to this height


def load_template_masks():
    """Load template alpha masks normalized to TARGET_H."""
    templates = {}
    for name in ["plus"] + [str(i) for i in range(10)]:
        path = TMPL_DIR / f"digit_{name}.png"
        rgba = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if rgba is None:
            continue
        mask = (rgba[:, :, 3] > 50).astype(np.uint8) * 255

        # Normalize to TARGET_H
        h, w = mask.shape
        scale = TARGET_H / h
        new_w = max(1, int(w * scale))
        resized = cv2.resize(mask, (new_w, TARGET_H), interpolation=cv2.INTER_AREA)
        _, resized = cv2.threshold(resized, 128, 255, cv2.THRESH_BINARY)

        label = "+" if name == "plus" else name
        templates[label] = resized
    return templates


def segment_glyphs(region_bgr):
    """Find individual glyph bounding boxes from orange pixel blobs."""
    hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)

    # Orange/yellow fill
    orange = cv2.inRange(hsv, (5, 50, 120), (38, 255, 255))
    # White highlight/outline (but be careful — too broad picks up background)
    white = cv2.inRange(hsv, (0, 0, 220), (180, 30, 255))

    combined = cv2.bitwise_or(orange, white)

    # Close small gaps within glyphs
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel)

    # Find connected components
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        combined, connectivity=8
    )

    h_region = region_bgr.shape[0]
    glyphs = []
    for i in range(1, num_labels):  # skip background
        x, y, w, h, area = stats[i]
        # Filter: must be tall enough (>30% of region height) and have enough area
        if h > h_region * 0.3 and area > 50 and w > 3:
            glyphs.append((x, y, w, h, combined[y:y+h, x:x+w]))

    # Sort by x position
    glyphs.sort(key=lambda g: g[0])

    # Merge overlapping/touching glyphs (x overlap)
    merged = []
    for g in glyphs:
        if merged:
            prev = merged[-1]
            px, py, pw, ph = prev[0], prev[1], prev[2], prev[3]
            gx, gy, gw, gh = g[0], g[1], g[2], g[3]
            if gx < px + pw + 3:  # overlapping or touching
                # Merge bounding boxes
                nx = min(px, gx)
                ny = min(py, gy)
                nx2 = max(px + pw, gx + gw)
                ny2 = max(py + ph, gy + gh)
                nw, nh = nx2 - nx, ny2 - ny
                merged[-1] = (nx, ny, nw, nh, combined[ny:ny+nh, nx:nx+nw])
                continue
        merged.append(g)

    return merged


def match_glyph(glyph_mask, templates):
    """Match a single glyph against all templates. Return (label, score)."""
    # Normalize glyph to TARGET_H
    h, w = glyph_mask.shape
    scale = TARGET_H / h
    new_w = max(1, int(w * scale))
    resized = cv2.resize(glyph_mask, (new_w, TARGET_H), interpolation=cv2.INTER_AREA)
    _, resized = cv2.threshold(resized, 128, 255, cv2.THRESH_BINARY)

    best_label = "?"
    best_score = -1

    for label, tmpl in templates.items():
        # Pad both to same width for comparison
        max_w = max(resized.shape[1], tmpl.shape[1]) + 4
        glyph_padded = np.zeros((TARGET_H, max_w), dtype=np.uint8)
        tmpl_padded = np.zeros((TARGET_H, max_w), dtype=np.uint8)

        # Center horizontally
        gx = (max_w - resized.shape[1]) // 2
        glyph_padded[:, gx:gx+resized.shape[1]] = resized
        tx = (max_w - tmpl.shape[1]) // 2
        tmpl_padded[:, tx:tx+tmpl.shape[1]] = tmpl

        # Compute IoU (intersection over union) of the binary shapes
        intersection = np.count_nonzero(glyph_padded & tmpl_padded)
        union = np.count_nonzero(glyph_padded | tmpl_padded)
        if union == 0:
            continue
        iou = intersection / union

        if iou > best_score:
            best_score = iou
            best_label = label

    return best_label, best_score


def read_gain(region_bgr, templates):
    """Read a gain value from a region using template matching."""
    glyphs = segment_glyphs(region_bgr)
    if not glyphs:
        return None, []

    results = []
    for x, y, w, h, mask in glyphs:
        label, score = match_glyph(mask, templates)
        results.append((x, label, score, w, h))

    return results


# Load templates
templates = load_template_masks()
print("Templates loaded:", sorted(templates.keys()))
for label, tmpl in templates.items():
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
    results = read_gain(region, templates)
    if results:
        text = "".join(r[1] for r in results)
        details = [f"'{r[1]}'={r[2]:.3f} ({r[3]}x{r[4]})" for r in results]
        print(f"  {stat:15s}: '{text}'  [{', '.join(details)}]")
    else:
        print(f"  {stat:15s}: (no glyphs found)")

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
    results = read_gain(crop, templates)
    if results:
        text = "".join(r[1] for r in results)
        details = [f"'{r[1]}'={r[2]:.3f} ({r[3]}x{r[4]})" for r in results]
        print(f"  expected={expected:5s}: got '{text}'  [{', '.join(details)}]")
    else:
        print(f"  expected={expected:5s}: (no glyphs)")

# Debug: save segmented glyphs
region = frame[1185:1255, 20:200]  # speed region
glyphs = segment_glyphs(region)
for i, (x, y, w, h, mask) in enumerate(glyphs):
    cv2.imwrite(f"/tmp/glyph_{i}_{x}.png", mask)
    print(f"\nGlyph {i}: x={x} size={w}x{h}")
