"""Test template matching v5 — tighter color isolation, vertical crop, multi-scale.

Key insight from v4: the HSV mask picks up "+13" clearly in speed region
but also picks up background noise. Solutions:
1. Tighter HSV range (focus on the warm orange-yellow of digits)
2. Vertical crop to center 60% of region (digits are vertically centered)
3. Try matching against the color-composited template (not just binary shape)
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

# Also test on the gain_ocr_samples crops (known: +11 and +5)
gain_samples = {
    "+11_stamina": "data/gain_ocr_samples/1774657070255_175_1185.png",
    "+5_guts": "data/gain_ocr_samples/1774657071547_510_1185.png",
}


def load_templates_bgr(scale):
    """Load templates composited on black, in BGR."""
    templates = {}
    for name in ["plus"] + [str(i) for i in range(10)]:
        path = TMPL_DIR / f"digit_{name}.png"
        rgba = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if rgba is None:
            continue
        h = max(1, int(rgba.shape[0] * scale))
        w = max(1, int(rgba.shape[1] * scale))
        resized = cv2.resize(rgba, (w, h), interpolation=cv2.INTER_AREA)

        # Composite on black
        alpha = resized[:, :, 3:4].astype(np.float32) / 255.0
        bgr = (resized[:, :, :3].astype(np.float32) * alpha).astype(np.uint8)
        mask = (resized[:, :, 3] > 50).astype(np.uint8) * 255

        label = "+" if name == "plus" else name
        templates[label] = (bgr, mask, w, h)
    return templates


def isolate_gain_colors(bgr):
    """Create a version of the image with only gain-colored pixels preserved.

    Non-gain pixels become black, making the template matching focus
    only on the orange/yellow/white gain digit colors.
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    # Orange/yellow fill: H=5-35, S>60, V>130
    orange = cv2.inRange(hsv, (5, 60, 130), (35, 255, 255))

    # White/cream outline: S<50, V>200
    white = cv2.inRange(hsv, (0, 0, 200), (180, 50, 255))

    combined = cv2.bitwise_or(orange, white)

    # Dilate slightly to connect fill and outline
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    combined = cv2.dilate(combined, kernel, iterations=1)

    # Apply mask to original image
    result = cv2.bitwise_and(bgr, bgr, mask=combined)
    return result


def match_region(region_bgr, templates, threshold=0.55):
    """Match templates against color-isolated region."""
    isolated = isolate_gain_colors(region_bgr)

    matches = []
    for label, (tmpl, mask, tw, th) in templates.items():
        if th > isolated.shape[0] or tw > isolated.shape[1]:
            continue

        # Match each channel and average
        scores = []
        for c in range(3):
            result = cv2.matchTemplate(
                isolated[:, :, c], tmpl[:, :, c],
                cv2.TM_CCOEFF_NORMED, mask=mask
            )
            scores.append(result)

        avg_result = np.mean(scores, axis=0)
        locations = np.where(avg_result >= threshold)
        for y, x in zip(*locations):
            matches.append((x, label, float(avg_result[y, x]), tw))

    matches.sort(key=lambda m: (m[0], -m[2]))
    filtered = []
    for m in matches:
        if filtered and abs(m[0] - filtered[-1][0]) < filtered[-1][3] * 0.5:
            if m[2] > filtered[-1][2]:
                filtered[-1] = m
        else:
            filtered.append(m)
    return filtered


# Test on full frame regions
for scale_pct in range(30, 70, 5):
    scale = scale_pct / 100.0
    templates = load_templates_bgr(scale)
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
        print(f"\n=== Full frame, Scale {scale_pct}% ===")
        for r in results:
            print(r)

# Test on known gain crops
print("\n\n=== Known gain crops ===")
for label, path in gain_samples.items():
    crop = cv2.imread(path)
    if crop is None:
        print(f"  {label}: cannot read {path}")
        continue
    print(f"\n  {label} ({crop.shape[1]}x{crop.shape[0]}):")
    isolated = isolate_gain_colors(crop)
    cv2.imwrite(f"/tmp/isolated_{label}.png", isolated)

    for scale_pct in range(30, 70, 5):
        scale = scale_pct / 100.0
        templates = load_templates_bgr(scale)
        matches = match_region(crop, templates)
        text = "".join(m[1] for m in matches)
        if text:
            scores = [f"{m[1]}={m[2]:.3f}" for m in matches]
            print(f"    scale={scale_pct}%: '{text}'  ({', '.join(scores)})")
