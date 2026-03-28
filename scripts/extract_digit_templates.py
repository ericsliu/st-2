"""Extract digit templates from gain region screenshots.

The gain numbers use a distinctive orange gradient fill with white outline.
We isolate them using HSV color filtering for the orange hue range, then
find connected components to split into individual glyphs.

Usage:
    python scripts/extract_digit_templates.py

Reads from saved tile screenshots and writes templates to
data/digit_templates/glyphs/.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
from uma_trainer.perception.regions import STAT_SELECTION_REGIONS

OUT_DIR = Path("data/digit_templates/glyphs")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def isolate_gain_digits(roi_up):
    """Create a binary mask of gain digit pixels using color filtering.

    The gain numbers are orange/yellow (H=10-30, S>80, V>150) with
    white outline (S<30, V>220).  We combine both to get the full glyph.
    """
    hsv = cv2.cvtColor(roi_up, cv2.COLOR_BGR2HSV)

    # Orange/yellow fill: H=5-35, S>60, V>140
    orange_mask = cv2.inRange(hsv, (5, 60, 140), (35, 255, 255))

    # White outline: low saturation, high value
    white_mask = cv2.inRange(hsv, (0, 0, 220), (180, 40, 255))

    # Combine
    combined = cv2.bitwise_or(orange_mask, white_mask)

    # Close small gaps (the outline and fill may not perfectly connect)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel)

    return combined


def extract_glyphs_from_region(frame, region, label: str, expected: str):
    """Extract individual glyph images from a gain region."""
    x1, y1, x2, y2 = region
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return []

    # Upscale 3x
    roi_up = cv2.resize(roi, (roi.shape[1] * 3, roi.shape[0] * 3),
                        interpolation=cv2.INTER_LANCZOS4)

    binary = isolate_gain_digits(roi_up)

    # Find contours
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)

    # Filter and sort by x position
    h_roi = roi_up.shape[0]
    bboxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        # Glyphs must be tall enough (>25% of ROI) and not too small
        if h > h_roi * 0.25 and w > 5 and h > 15:
            bboxes.append((x, y, w, h))

    bboxes.sort(key=lambda b: b[0])

    # Merge overlapping bboxes (outline fragments)
    merged = []
    for box in bboxes:
        if merged and box[0] < merged[-1][0] + merged[-1][2] + 5:
            # Overlapping or very close — merge
            prev = merged[-1]
            x = min(prev[0], box[0])
            y = min(prev[1], box[1])
            x2m = max(prev[0] + prev[2], box[0] + box[2])
            y2m = max(prev[1] + prev[3], box[1] + box[3])
            merged[-1] = (x, y, x2m - x, y2m - y)
        else:
            merged.append(box)

    bboxes = merged

    print(f"\n{label}: expected '{expected}', found {len(bboxes)} glyphs")

    # Save debug images
    cv2.imwrite(str(OUT_DIR / f"{label}_color.png"), roi_up)
    cv2.imwrite(str(OUT_DIR / f"{label}_binary.png"), binary)

    expected_chars = list(expected)
    extracted = []

    for i, (x, y, w, h) in enumerate(bboxes):
        char = expected_chars[i] if i < len(expected_chars) else f"unk{i}"

        # Extract glyph from lightness (better for template matching)
        lightness = np.max(roi_up, axis=2)
        glyph = lightness[y:y+h, x:x+w]

        # Also get the binary mask for this glyph
        glyph_mask = binary[y:y+h, x:x+w]

        # Normalize to fixed height (48px) preserving aspect ratio
        target_h = 48
        scale = target_h / h
        target_w = max(1, int(w * scale))
        glyph_norm = cv2.resize(glyph, (target_w, target_h),
                                interpolation=cv2.INTER_LANCZOS4)
        mask_norm = cv2.resize(glyph_mask, (target_w, target_h),
                               interpolation=cv2.INTER_NEAREST)

        char_label = "plus" if char == "+" else char
        filename = f"{char_label}_{label}_{i}.png"
        mask_filename = f"{char_label}_{label}_{i}_mask.png"
        cv2.imwrite(str(OUT_DIR / filename), glyph_norm)
        cv2.imwrite(str(OUT_DIR / mask_filename), mask_norm)

        print(f"  [{i}] char='{char}' bbox=({x},{y},{w},{h}) → {filename}")
        extracted.append((char, glyph_norm, mask_norm))

    # Save annotated image
    annotated = roi_up.copy()
    for i, (x, y, w, h) in enumerate(bboxes):
        cv2.rectangle(annotated, (x, y), (x+w, y+h), (0, 255, 0), 2)
        char = expected_chars[i] if i < len(expected_chars) else "?"
        cv2.putText(annotated, char, (x, y-5), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 255, 0), 2)
    cv2.imwrite(str(OUT_DIR / f"{label}_annotated.png"), annotated)

    return extracted


def main():
    tile_data = [
        ("/tmp/tile_stamina.png", {
            "gain_stamina": "+11",
            "gain_guts": "+5",
        }),
        ("/tmp/tile_power.png", {
            "gain_stamina": "+7",
            "gain_power": "+19",
        }),
        ("/tmp/tile_speed.png", {
            "gain_speed": "+8",
            "gain_power": "+5",
        }),
        ("/tmp/tile_default.png", {
            "gain_speed": "+2",
            "gain_wit": "+9",
        }),
    ]

    all_chars = {}  # char -> list of (glyph, mask) samples
    for img_path, gains in tile_data:
        frame = cv2.imread(img_path)
        if frame is None:
            print(f"Cannot read {img_path}")
            continue

        print(f"\n{'='*60}")
        print(f"Processing {img_path}")

        for region_key, expected in gains.items():
            region = STAT_SELECTION_REGIONS.get(region_key)
            if region is None:
                continue
            label = f"{region_key}_{expected.replace('+', 'plus')}"
            extracted = extract_glyphs_from_region(frame, region, label, expected)
            for char, glyph, mask in extracted:
                all_chars.setdefault(char, []).append((glyph, mask))

    print(f"\n{'='*60}")
    print(f"Extracted templates for: {sorted(all_chars.keys())}")
    missing = set("0123456789+") - set(all_chars.keys())
    if missing:
        print(f"Missing: {sorted(missing)}")
        print("Need more screenshots with these digits visible as gains.")
    else:
        print("All digits 0-9 and + covered!")

    # Save best template for each character (pick the one with most contrast)
    print(f"\nFinal templates:")
    for char in sorted(all_chars.keys()):
        samples = all_chars[char]
        # Pick sample with highest contrast (std dev of lightness)
        best = max(samples, key=lambda s: np.std(s[0]))
        char_label = "plus" if char == "+" else char
        cv2.imwrite(str(OUT_DIR / f"template_{char_label}.png"), best[0])
        cv2.imwrite(str(OUT_DIR / f"template_{char_label}_mask.png"), best[1])
        print(f"  '{char}': {len(samples)} samples, saved template_{char_label}.png")


if __name__ == "__main__":
    main()
