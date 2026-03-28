"""Template matching v9 — refined, with wider HSV range and better IoU scoring.

Fixes from v8:
- Wider orange range to catch more gain digit variants
- Use Hu moments for matching instead of IoU (rotation/scale invariant)
- Better glyph merging for split components
"""
import cv2
import numpy as np
from pathlib import Path

TMPL_DIR = Path("data/digit_templates")
TARGET_H = 48


def load_template_masks():
    """Load template alpha masks normalized to TARGET_H."""
    templates = {}
    for name in ["plus"] + [str(i) for i in range(10)]:
        path = TMPL_DIR / f"digit_{name}.png"
        rgba = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if rgba is None:
            continue
        mask = (rgba[:, :, 3] > 50).astype(np.uint8) * 255

        h, w = mask.shape
        scale = TARGET_H / h
        new_w = max(1, int(w * scale))
        resized = cv2.resize(mask, (new_w, TARGET_H), interpolation=cv2.INTER_AREA)
        _, resized = cv2.threshold(resized, 128, 255, cv2.THRESH_BINARY)

        label = "+" if name == "plus" else name
        templates[label] = resized
    return templates


def get_orange_mask(bgr):
    """Get mask of orange/yellow gain digit pixels."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    # Wider range: catch both saturated orange AND lighter yellow-orange
    orange1 = cv2.inRange(hsv, (3, 40, 100), (40, 255, 255))
    # Also catch reddish-orange (plus sign can be more red)
    orange2 = cv2.inRange(hsv, (0, 80, 150), (5, 255, 255))
    return cv2.bitwise_or(orange1, orange2)


def segment_glyphs(region_bgr, min_height_ratio=0.25):
    """Segment glyphs using vertical projection on orange pixels."""
    orange = get_orange_mask(region_bgr)
    h_region = region_bgr.shape[0]

    # Vertical projection
    col_sums = np.sum(orange > 0, axis=0)

    # Find runs of columns with orange pixels (gap threshold = 3 cols)
    runs = []
    in_run = False
    for x in range(len(col_sums)):
        if col_sums[x] > 1:
            if not in_run:
                runs.append([x, x])
                in_run = True
            else:
                runs[-1][1] = x
        else:
            if in_run:
                in_run = False

    # Merge runs that are very close (gap < 4px, likely same glyph)
    merged_runs = []
    for run in runs:
        if merged_runs and run[0] - merged_runs[-1][1] < 4:
            merged_runs[-1][1] = run[1]
        else:
            merged_runs.append(run)

    glyphs = []
    for start, end in merged_runs:
        w = end - start + 1
        strip = orange[:, start:end+1]
        row_sums = np.sum(strip > 0, axis=1)
        ys = np.where(row_sums > 0)[0]
        if len(ys) == 0:
            continue
        y_top = ys[0]
        y_bot = ys[-1] + 1
        h = y_bot - y_top

        if h > h_region * min_height_ratio and w > 3:
            mask = orange[y_top:y_bot, start:end+1]
            glyphs.append((start, y_top, w, h, mask))

    return glyphs


def iou_match(glyph_mask, templates):
    """Match a glyph against templates using IoU with horizontal alignment search."""
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
        # Try a few horizontal offsets to find best alignment
        max_w = max(resized.shape[1], tmpl.shape[1]) + 8
        best_iou_for_label = 0

        for offset in range(-2, 3):
            glyph_padded = np.zeros((TARGET_H, max_w), dtype=np.uint8)
            tmpl_padded = np.zeros((TARGET_H, max_w), dtype=np.uint8)

            gx = (max_w - resized.shape[1]) // 2 + offset
            gx = max(0, min(gx, max_w - resized.shape[1]))
            glyph_padded[:, gx:gx+resized.shape[1]] = resized

            tx = (max_w - tmpl.shape[1]) // 2
            tmpl_padded[:, tx:tx+tmpl.shape[1]] = tmpl

            intersection = np.count_nonzero(glyph_padded & tmpl_padded)
            union = np.count_nonzero(glyph_padded | tmpl_padded)
            if union == 0:
                continue
            iou = intersection / union
            best_iou_for_label = max(best_iou_for_label, iou)

        if best_iou_for_label > best_score:
            best_score = best_iou_for_label
            best_label = label

    return best_label, best_score


def read_gain(region_bgr, templates, min_iou=0.30):
    """Read a gain value from a region."""
    glyphs = segment_glyphs(region_bgr)
    if not glyphs:
        return None, []

    results = []
    for x, y, w, h, mask in glyphs:
        label, score = iou_match(mask, templates)
        results.append((x, label, score, w, h))

    # Parse value
    good = [r for r in results if r[2] >= min_iou]
    text = "".join(r[1] for r in good)

    # Try "+N" pattern
    if "+" in text:
        idx = text.index("+")
        digits = text[idx+1:]
        digit_str = "".join(c for c in digits if c.isdigit())
        if digit_str:
            return int(digit_str), results

    # Try bare digits
    digit_str = "".join(c for c in text if c.isdigit())
    if digit_str:
        val = int(digit_str)
        if 1 <= val <= 50:
            return val, results

    return None, results


# ============================================================
templates = load_template_masks()

# Test on full frame
frame = cv2.imread("screenshots/debug_gains/stat_selection.png")
print("=== Full frame (speed=+13, guts=+5) ===")
regions = {
    "speed":   (20, 1185, 200, 1255),
    "stamina": (175, 1185, 365, 1255),
    "power":   (335, 1185, 540, 1255),
    "guts":    (510, 1185, 700, 1255),
    "wit":     (670, 1185, 870, 1255),
}
for stat, (x1, y1, x2, y2) in regions.items():
    region = frame[y1:y2, x1:x2]
    value, results = read_gain(region, templates)
    text = "".join(f"{r[1]}" for r in results)
    scores = " ".join(f"{r[1]}={r[2]:.2f}" for r in results)
    print(f"  {stat:8s}: value={str(value):>4s}  raw='{text}'  [{scores}]")

# Test on known crops
print("\n=== Known crops ===")
crops = [
    ("+11", "data/gain_ocr_samples/1774657070255_175_1185.png"),
    ("+5",  "data/gain_ocr_samples/1774657071547_510_1185.png"),
]
for expected, path in crops:
    crop = cv2.imread(path)
    if crop is None:
        continue
    value, results = read_gain(crop, templates)
    text = "".join(f"{r[1]}" for r in results)
    scores = " ".join(f"{r[1]}={r[2]:.2f}" for r in results)
    print(f"  expect={expected:5s}: value={str(value):>4s}  raw='{text}'  [{scores}]")
