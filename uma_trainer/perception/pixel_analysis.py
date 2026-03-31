"""Pixel-based analysis for mood, training indicators, and support cards.

Uses colour sampling and simple heuristics instead of ML inference.
All functions operate on BGR numpy arrays at 1080×1920.
"""

from __future__ import annotations

import logging
from pathlib import Path

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


def _load_npc_templates() -> list[tuple[str, "np.ndarray"]]:
    """Load NPC portrait templates from data/npc_templates/.

    Returns list of (name, bgr_array) tuples. Cached after first call.
    """
    if hasattr(_load_npc_templates, "_cache"):
        return _load_npc_templates._cache

    import cv2
    templates = []
    template_dir = Path(__file__).resolve().parent.parent.parent / "data" / "npc_templates"
    if template_dir.is_dir():
        for png in sorted(template_dir.glob("*.png")):
            tmpl = cv2.imread(str(png))
            if tmpl is not None:
                templates.append((png.stem, tmpl))
                logger.debug("Loaded NPC template: %s (%s)", png.stem, tmpl.shape[:2])

    _load_npc_templates._cache = templates
    return templates


def _is_npc_portrait(frame: np.ndarray, portrait_region: tuple[int, int, int, int],
                     threshold: float = 0.65) -> str | None:
    """Check if a portrait region matches any NPC template.

    Args:
        frame: BGR numpy array (full 1080x1920 frame).
        portrait_region: (x1, y1, x2, y2) of the portrait area.
        threshold: Minimum normalized cross-correlation to count as match.

    Returns:
        NPC name if matched, None otherwise.
    """
    import cv2

    x1, y1, x2, y2 = portrait_region
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return None

    templates = _load_npc_templates()
    if not templates:
        return None

    for name, tmpl in templates:
        # Resize template to match ROI height if needed
        th, tw = tmpl.shape[:2]
        rh, rw = roi.shape[:2]
        if th != rh or tw != rw:
            tmpl_resized = cv2.resize(tmpl, (rw, rh))
        else:
            tmpl_resized = tmpl

        result = cv2.matchTemplate(roi, tmpl_resized, cv2.TM_CCOEFF_NORMED)
        score = float(result.max())
        logger.debug("NPC match '%s': score=%.3f (threshold=%.2f)", name, score, threshold)
        if score >= threshold:
            return name

    return None


def _classify_bond_color(frame: np.ndarray, bar_y: int,
                         segment_xs: list[int]) -> str:
    """Classify the bond bar color as 'blue', 'green', or 'orange'.

    The bar color indicates bond level range:
      - blue/cyan: low bond (below ~60%)
      - green: medium bond (approaching but below friendship threshold)
      - orange: friendship threshold reached (≥80%)

    Samples filled segments and classifies by average HSV hue.
    Returns 'none' if no filled segments found.
    """
    try:
        import cv2
    except ImportError:
        return "none"

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hues = []
    for sx in segment_xs:
        if sx >= frame.shape[1] or bar_y >= frame.shape[0]:
            continue
        b, g, r = int(frame[bar_y, sx, 0]), int(frame[bar_y, sx, 1]), int(frame[bar_y, sx, 2])
        sat = max(r, g, b) - min(r, g, b)
        if sat > 80:
            hues.append(int(hsv[bar_y, sx, 0]))

    if not hues:
        return "none"

    avg_hue = sum(hues) / len(hues)
    # OpenCV HSV hue: 0-179.  Orange ~10-25, Green ~35-55, Blue/Cyan ~85-110
    if avg_hue < 30:
        return "orange"
    elif avg_hue < 65:
        return "green"
    else:
        return "blue"


def read_bond_levels(frame: np.ndarray) -> list[int]:
    """Read bond meter levels from support card portraits on the stat selection screen.

    Each portrait has a segmented bond gauge bar below it. The bar has 5 segments
    separated by dark gray dividers (~73,72,73). Filled segments are colored;
    unfilled segments are gray (~109,109,117).

    Bar color indicates bond range:
      - blue/cyan = low bond
      - green = medium bond (below friendship threshold)
      - orange = friendship threshold reached (≥80%)

    The segment count gives a coarse reading (0/20/40/60/80/100) but the color
    is authoritative for the friendship boundary: green bars are capped at 79
    (below friendship) even if segment count suggests 80+.

    NPC portraits (Director Akikawa, Reporter) are filtered out via template
    matching against data/npc_templates/.

    Returns a list of bond percentages (0-100) for each detected support card,
    ordered top to bottom. Empty list if no bars found.
    """
    # Bond bar absolute positions (1080x1920 portrait, stat selection screen).
    # Bar y-centers for up to 6 support card slots, spaced ~180px apart.
    BAR_Y_CENTERS = [424, 604, 784, 964, 1144, 1324]

    # Portrait region: ~140px above bar center, x=940..1060
    PORTRAIT_X = (940, 1060)
    PORTRAIT_Y_OFFSET = 130  # portrait top is this far above bar_y

    # Dividers at x=926, 949, 972, 995, 1021 create 5 segments:
    #   Seg1: 915-925, Seg2: 928-948, Seg3: 951-971, Seg4: 974-994, Seg5: 998-1020
    # One sample point near the center of each segment.
    SEGMENT_SAMPLE_XS = [920, 938, 961, 984, 1009]

    # Segment divider x positions — dark gray (~73,72,73) between segments.
    DIVIDER_XS = [926, 949, 972, 995]

    results = []

    for bar_y in BAR_Y_CENTERS:
        if bar_y >= frame.shape[0]:
            break

        # First, verify this is a real bond bar by checking for dividers.
        has_divider = False
        for dx in DIVIDER_XS:
            if dx >= frame.shape[1]:
                continue
            b, g, r = int(frame[bar_y, dx, 0]), int(frame[bar_y, dx, 1]), int(frame[bar_y, dx, 2])
            if r < 90 and g < 90 and b < 90 and (max(r, g, b) - min(r, g, b)) < 15:
                has_divider = True
                break

        if not has_divider:
            break  # No bond bar here — no more cards below

        # Check if this portrait is an NPC (Director/Reporter) — skip if so
        portrait_top = bar_y - PORTRAIT_Y_OFFSET
        portrait_bot = bar_y - 10
        portrait_region = (PORTRAIT_X[0], max(0, portrait_top), PORTRAIT_X[1], portrait_bot)
        npc_name = _is_npc_portrait(frame, portrait_region)
        if npc_name:
            logger.info("Skipping NPC '%s' at bar_y=%d", npc_name, bar_y)
            continue  # Skip this slot but keep checking lower slots

        filled = 0
        for sx in SEGMENT_SAMPLE_XS:
            if sx >= frame.shape[1] or bar_y >= frame.shape[0]:
                break
            b, g, r = int(frame[bar_y, sx, 0]), int(frame[bar_y, sx, 1]), int(frame[bar_y, sx, 2])
            sat = max(r, g, b) - min(r, g, b)
            if sat > 80:
                filled += 1

        bond_pct = (filled * 100) // 5  # 0, 20, 40, 60, 80, 100

        # Use bar color to enforce friendship boundary.
        # Green = not yet at friendship, so cap at 79 even if segments say 80+.
        # Orange = friendship reached, so floor at 80.
        color = _classify_bond_color(frame, bar_y, SEGMENT_SAMPLE_XS)
        if color == "green" and bond_pct >= 80:
            bond_pct = 79
        elif color == "orange" and bond_pct < 80:
            bond_pct = 80
        elif color == "blue" and bond_pct >= 60:
            bond_pct = 59  # Blue = low bond, cap conservatively

        results.append(bond_pct)
        logger.debug("Bond bar y=%d: %d/5 filled, color=%s -> %d%%",
                      bar_y, filled, color, bond_pct)

    return results


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
