"""Pixel-based analysis for mood, training indicators, and support cards.

Uses colour sampling and simple heuristics instead of ML inference.
All functions operate on BGR numpy arrays at 1080×1920.
"""

from __future__ import annotations

import logging

import numpy as np

from uma_trainer.perception.regions import Region
from uma_trainer.types import Mood

logger = logging.getLogger(__name__)


# ── Mood detection ────────────────────────────────────────────────────────────
# The mood text label (e.g. "NORMAL") sits in a coloured pill/badge.
# We detect mood by reading the dominant hue of the mood indicator region.

# HSV hue ranges for each mood (OpenCV uses H=0-179, S=0-255, V=0-255)
# These need calibration against actual screenshots.
_MOOD_HUE_RANGES: list[tuple[Mood, int, int, int]] = [
    # (mood, hue_min, hue_max, min_saturation)
    (Mood.GREAT,     5,  20,  80),   # Orange/red warm glow
    (Mood.GOOD,     20,  45,  80),   # Yellow-ish
    (Mood.NORMAL,   75, 100,  40),   # Green
    (Mood.BAD,     100, 130,  60),   # Blue-ish
    (Mood.TERRIBLE, 130, 170,  60),  # Purple/violet
]


def detect_mood(frame: np.ndarray, region: Region) -> Mood:
    """Detect the trainee's mood from the mood indicator region.

    Samples the region, converts to HSV, and matches the dominant hue
    against known mood colours.

    Args:
        frame: BGR numpy array.
        region: (x1, y1, x2, y2) bounding box of the mood indicator.

    Returns:
        Detected Mood, defaulting to Mood.NORMAL on failure.
    """
    x1, y1, x2, y2 = region
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return Mood.NORMAL

    try:
        import cv2
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    except ImportError:
        logger.warning("OpenCV not available for mood detection; defaulting to NORMAL")
        return Mood.NORMAL

    # Compute median hue and saturation (more robust than mean)
    median_h = int(np.median(hsv[:, :, 0]))
    median_s = int(np.median(hsv[:, :, 1]))

    for mood, h_min, h_max, s_min in _MOOD_HUE_RANGES:
        if h_min <= median_h <= h_max and median_s >= s_min:
            logger.debug("Mood detected: %s (H=%d, S=%d)", mood.value, median_h, median_s)
            return mood

    logger.debug("Mood hue not matched (H=%d, S=%d); defaulting to NORMAL", median_h, median_s)
    return Mood.NORMAL


def detect_mood_from_text(mood_text: str) -> Mood:
    """Parse mood from OCR'd mood label text (e.g. 'NORMAL', 'GREAT')."""
    text = mood_text.strip().upper()
    for mood in Mood:
        if mood.value.upper() in text:
            return mood
    return Mood.NORMAL


# ── Training indicators ───────────────────────────────────────────────────────

def detect_training_indicators(
    frame: np.ndarray,
    region: Region,
) -> dict[str, bool]:
    """Detect rainbow, gold, hint, and director indicators on a training tile.

    Analyses colour distribution in the indicator region above each tile.

    Args:
        frame: BGR numpy array.
        region: (x1, y1, x2, y2) indicator region above a training tile.

    Returns:
        Dict with keys: is_rainbow, is_gold, has_hint, has_director.
    """
    x1, y1, x2, y2 = region
    roi = frame[y1:y2, x1:x2]
    result = {
        "is_rainbow": False,
        "is_gold": False,
        "has_hint": False,
        "has_director": False,
    }

    if roi.size == 0:
        return result

    try:
        import cv2
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    except ImportError:
        return result

    # Rainbow: high colour variance (many different hues present)
    hue_std = float(np.std(hsv[:, :, 0]))
    sat_mean = float(np.mean(hsv[:, :, 1]))
    if hue_std > 40 and sat_mean > 60:
        result["is_rainbow"] = True
        logger.debug("Rainbow indicator detected (hue_std=%.1f)", hue_std)
        return result  # Rainbow supersedes gold

    # Gold: narrow hue in yellow range with high saturation
    hue_median = int(np.median(hsv[:, :, 0]))
    if 15 <= hue_median <= 35 and sat_mean > 80:
        result["is_gold"] = True
        logger.debug("Gold indicator detected (hue=%d, sat=%.0f)", hue_median, sat_mean)

    # Hint: look for a small red notification dot
    # Red pixels have hue near 0 or near 170+ in OpenCV HSV
    red_mask = ((hsv[:, :, 0] < 10) | (hsv[:, :, 0] > 165)) & (hsv[:, :, 1] > 100)
    red_ratio = float(np.mean(red_mask))
    if red_ratio > 0.05:
        result["has_hint"] = True
        logger.debug("Hint indicator detected (red_ratio=%.3f)", red_ratio)

    # Director: look for a distinctive icon — placeholder heuristic
    # The director icon is typically a small character portrait; hard to detect
    # purely by colour.  For now we leave this as False and rely on the
    # scoring engine's other signals.

    return result


# ── Support card counting ─────────────────────────────────────────────────────

def count_support_cards(frame: np.ndarray, region: Region) -> int:
    """Count the number of support card icons visible on a training tile.

    Uses edge detection to find distinct circular card icons.

    Args:
        frame: BGR numpy array.
        region: (x1, y1, x2, y2) of the card icon area on a tile.

    Returns:
        Estimated number of support cards (0–6).
    """
    x1, y1, x2, y2 = region
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return 0

    try:
        import cv2
    except ImportError:
        return 0

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    # Use Canny edge detection to find card boundaries.
    # Stricter thresholds to reduce noise from tile backgrounds and labels.
    edges = cv2.Canny(gray, 80, 200)
    edge_ratio = float(np.mean(edges > 0))

    # Thresholds recalibrated 2026-03-27.
    # Background noise (tile borders, labels) produces ~0.05-0.09 edge ratio
    # even with zero cards. Each actual card adds ~0.04-0.06.
    if edge_ratio < 0.05:
        return 0
    elif edge_ratio < 0.10:
        return 1
    elif edge_ratio < 0.15:
        return 2
    elif edge_ratio < 0.20:
        return 3
    elif edge_ratio < 0.25:
        return 4
    elif edge_ratio < 0.30:
        return 5
    else:
        return 6


def count_panel_portraits(frame: np.ndarray, region: Region) -> int:
    """Count support card character portraits on the right panel.

    When a training tile is selected, the support cards assigned to that
    training appear as circular character portraits stacked vertically
    along the right side of the screen. Each portrait is ~120px tall
    (circle + bond gauge) with the first starting near the top of the panel.

    Detection: check fixed vertical slots for colour variance.
    Background (classroom walls, empty space) has low variance;
    a character portrait has high variance from the detailed art.

    Args:
        frame: BGR numpy array (1080x1920).
        region: (x1, y1, x2, y2) of the right panel area.

    Returns:
        Number of detected portraits (0–6).
    """
    x1, y1, x2, y2 = region
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return 0

    try:
        import cv2
    except ImportError:
        return 0

    # Portrait slots: each ~140px tall, starting from y=0 in the ROI.
    # The first portrait center is at ~y=80, subsequent ones ~140px apart.
    SLOT_HEIGHT = 140
    SLOT_START = 10
    MAX_SLOTS = 6

    panel_h = y2 - y1
    count = 0

    for slot in range(MAX_SLOTS):
        slot_y1 = SLOT_START + slot * SLOT_HEIGHT
        slot_y2 = slot_y1 + SLOT_HEIGHT
        if slot_y2 > panel_h:
            break

        slot_roi = roi[slot_y1:slot_y2, :]
        if slot_roi.size == 0:
            break

        # Convert to HSV and check saturation variance.
        # Character portraits are colourful (high sat variance);
        # background is mostly uniform (low sat variance).
        hsv = cv2.cvtColor(slot_roi, cv2.COLOR_BGR2HSV)
        sat = hsv[:, :, 1].astype(float)
        sat_std = float(np.std(sat))

        # Also check edge density as a secondary signal
        gray_slot = cv2.cvtColor(slot_roi, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray_slot, 60, 150)
        edge_ratio = float(np.mean(edges > 0))

        has_portrait = sat_std > 40 and edge_ratio > 0.08

        logger.debug(
            "Portrait slot %d (y=%d-%d): sat_std=%.1f, edge=%.3f -> %s",
            slot, y1 + slot_y1, y1 + slot_y2,
            sat_std, edge_ratio,
            "YES" if has_portrait else "no",
        )

        if has_portrait:
            count += 1
        else:
            # Portraits stack from the top; once we hit an empty slot, stop
            break

    return count


def region_has_content(frame: np.ndarray, region: Region, threshold: float = 0.15) -> bool:
    """Check whether a region has non-background content.

    Uses edge density as a proxy for content presence.

    Args:
        frame: BGR numpy array.
        region: (x1, y1, x2, y2) to check.
        threshold: Minimum edge density to count as having content.

    Returns:
        True if the region appears to contain content.
    """
    x1, y1, x2, y2 = region
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return False

    try:
        import cv2
    except ImportError:
        return False

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    return float(np.mean(edges > 0)) > threshold
