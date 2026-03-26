"""Fixed-coordinate region maps for 1080×1920 portrait mode.

Replaces YOLO object detection with deterministic coordinate lookups.
All coordinates are (x1, y1, x2, y2) at the canonical 1080×1920 resolution.

These regions were mapped from MuMuPlayer screenshots in portrait mode
running the Trackblazer scenario.  Run scripts/calibrate_regions.py to
visualise and tune them against live screenshots.
"""

from __future__ import annotations

from dataclasses import dataclass

from uma_trainer.types import ScreenState, StatType


# ── Canonical resolution ──────────────────────────────────────────────────────

CANONICAL_WIDTH = 1080
CANONICAL_HEIGHT = 1920


# ── Screen anchors (pixel colour checks for screen identification) ────────────

@dataclass(frozen=True)
class PixelAnchor:
    """A pixel position + expected RGB range used to identify a screen."""
    x: int
    y: int
    r_min: int
    r_max: int
    g_min: int
    g_max: int
    b_min: int
    b_max: int

    def matches(self, r: int, g: int, b: int, tolerance: int = 0) -> bool:
        return (
            self.r_min - tolerance <= r <= self.r_max + tolerance
            and self.g_min - tolerance <= g <= self.g_max + tolerance
            and self.b_min - tolerance <= b <= self.b_max + tolerance
        )


@dataclass(frozen=True)
class ScreenAnchorSet:
    """A set of pixel anchors that together identify a screen state."""
    screen: ScreenState
    anchors: tuple[PixelAnchor, ...]
    min_matches: int  # How many anchors must match


# Screen anchor definitions.
# These check distinctive, non-animated UI elements unique to each screen.
# The colour ranges should be calibrated with scripts/calibrate_regions.py.
#
# Calibrated 2026-03-24 from MuMuPlayer portrait 1080×1920 screenshots.
# Key sampled values (home screen):
#   Home tab highlight (540,1890) → R=27  G=156 B=242 (bright blue)
#   Nav bar edge (100,1870)       → R=101 G=94  B=125 (muted purple-grey)
# Key sampled values (career turn action):
#   Rest btn (187,1525)    → R=119 G=204 B=34  (bright green)
#   Training btn (510,1525) → R=52  G=139 B=223 (blue)
#   Turn counter (80,115)  → R=237 G=196 B=14  (gold)
# Key sampled values (stat selection):
#   Back btn text (110,1880)→ R=241 G=241 B=241 (white)
#   Speed tile (130,1660)   → R=91  G=176 B=99  (green)

SCREEN_ANCHORS: list[ScreenAnchorSet] = [
    # ── Main menu (game home screen) ──────────────────────────────────
    # Bottom nav bar with "Home" tab highlighted in bright blue.
    # Nav bar edges are a muted purple-grey unique to this screen.
    ScreenAnchorSet(
        screen=ScreenState.MAIN_MENU,
        anchors=(
            # "Home" tab highlight — bright blue (R≈27 G≈156 B≈242)
            PixelAnchor(540, 1890, 0, 60, 125, 190, 210, 255),
            # Nav bar left edge — muted purple-grey (R≈101 G≈94 B≈125)
            PixelAnchor(100, 1870, 70, 135, 60, 130, 90, 160),
            # Nav bar right edge — same muted purple-grey
            PixelAnchor(1000, 1870, 70, 135, 60, 130, 90, 160),
        ),
        min_matches=2,
    ),
    # ── Loading screen ────────────────────────────────────────────────
    ScreenAnchorSet(
        screen=ScreenState.LOADING,
        anchors=(
            # Center of screen — loading screens tend to have a dark overlay
            PixelAnchor(540, 960, 0, 40, 0, 40, 0, 40),
        ),
        min_matches=1,
    ),
    # ── Turn action screen (main menu during career) ──────────────────
    # Identified by the green Rest button and blue Training button.
    ScreenAnchorSet(
        screen=ScreenState.TRAINING,
        anchors=(
            # Rest button — bright green (R≈119 G≈204 B≈34)
            PixelAnchor(187, 1525, 90, 150, 175, 235, 10, 65),
            # Training button — blue (R≈52 G≈139 B≈223)
            PixelAnchor(510, 1525, 25, 80, 110, 170, 195, 255),
            # Turn counter area — gold text (R≈237 G≈196 B≈14)
            PixelAnchor(80, 115, 210, 255, 170, 225, 0, 45),
        ),
        min_matches=2,
    ),
    # ── Stat selection screen ─────────────────────────────────────────
    # After tapping "Training" — also maps to TRAINING; the assembler
    # uses is_stat_selection() to distinguish the sub-screen.
    ScreenAnchorSet(
        screen=ScreenState.TRAINING,
        anchors=(
            # Turn counter area — gold text (same as turn action)
            PixelAnchor(80, 115, 210, 255, 170, 225, 0, 45),
            # Speed tile center (130,1660) — green (R≈91 G≈176 B≈99)
            PixelAnchor(130, 1660, 60, 125, 145, 210, 65, 130),
            # Power tile center (540,1660) — dark green (R≈0 G≈132 B≈12)
            PixelAnchor(540, 1660, 0, 35, 100, 165, 0, 45),
        ),
        min_matches=2,
    ),
    # ── Event popup ───────────────────────────────────────────────────
    ScreenAnchorSet(
        screen=ScreenState.EVENT,
        anchors=(
            # Dark overlay in corners (semi-transparent black)
            PixelAnchor(30, 30, 0, 60, 0, 60, 0, 60),
            PixelAnchor(1050, 30, 0, 60, 0, 60, 0, 60),
        ),
        min_matches=2,
    ),
    # ── Skill shop ────────────────────────────────────────────────────
    # Needs calibration — placeholder, requires 2 anchors to avoid
    # false positives from other screens with similar header colours.
    ScreenAnchorSet(
        screen=ScreenState.SKILL_SHOP,
        anchors=(
            # Header area
            PixelAnchor(540, 50, 40, 100, 70, 140, 50, 120),
            # Turn counter gold (skill shop is within career, so this is present)
            PixelAnchor(80, 115, 210, 255, 170, 225, 0, 45),
        ),
        min_matches=2,
    ),
]


# ── Region definitions per screen ─────────────────────────────────────────────

# Type alias for bounding box (x1, y1, x2, y2)
Region = tuple[int, int, int, int]


@dataclass(frozen=True)
class TileRegion:
    """Regions associated with a single training tile."""
    tap_target: Region         # Where to tap to select this tile
    label: Region              # "Speed Lvl 1" text area
    indicator: Region          # Area above tile for rainbow/gold/hint icons
    support_cards: Region      # Area where support card icons appear


# ── Turn action screen (main career menu) ─────────────────────────────────────
# This is the screen with Rest, Training, Skills, Infirmary, Recreation, Shop, Races

TURN_ACTION_REGIONS: dict[str, Region] = {
    # Header info
    "period_text":       (100, 48, 340, 78),       # "Junior Year, Early Jul"
    "turn_counter":      (15, 85, 155, 195),       # "12 turn(s) left"
    "result_pts":        (175, 88, 450, 118),      # "60 Result Pts"
    "goal_progress":     (175, 118, 550, 148),     # "Progress: After 50 pts"
    "details_btn":       (825, 95, 960, 140),      # "Details" button

    # Energy and mood
    "energy_bar":        (195, 155, 700, 180),     # The energy bar itself
    "mood_label":        (790, 152, 950, 185),     # "NORMAL" / mood text
    "mood_icon":         (730, 152, 785, 185),     # Arrow/face icon for mood

    # Scenario-specific
    "junior_result_pts": (30, 200, 250, 270),      # "Junior Result Pts 10 pts"
    "hint_icon":         (910, 195, 1020, 295),    # HINT indicator (top-right)

    # Stat display row
    "stat_speed":        (38, 445, 155, 490),      # Speed value "230"
    "stat_stamina":      (185, 445, 305, 490),     # Stamina value "195"
    "stat_power":        (335, 445, 455, 490),     # Power value "228"
    "stat_guts":         (485, 445, 600, 490),     # Guts value "251"
    "stat_wit":          (635, 445, 750, 490),     # Wit value "200"
    "stat_max_speed":    (38, 490, 155, 510),      # "/1200"
    "stat_max_stamina":  (185, 490, 305, 510),     # "/1200"
    "stat_max_power":    (335, 490, 455, 510),     # "/1200"
    "stat_max_guts":     (485, 490, 600, 510),     # "/1200"
    "stat_max_wit":      (635, 490, 750, 510),     # "/1200"
    "skill_pts":         (850, 445, 1000, 490),    # "211"

    # Grade letters (F/E/D/C/B/A/S)
    "grade_speed":       (155, 445, 185, 490),
    "grade_stamina":     (305, 445, 335, 490),
    "grade_power":       (455, 445, 485, 490),
    "grade_guts":        (600, 445, 630, 490),
    "grade_wit":         (750, 445, 780, 490),

    # Action buttons (Trackblazer has 7: 3 top row + 4 bottom row)
    "btn_rest":          (45, 1460, 330, 1590),
    "btn_training":      (340, 1460, 680, 1590),
    "btn_skills":        (690, 1460, 1000, 1590),
    "btn_infirmary":     (45, 1600, 260, 1730),
    "btn_recreation":    (275, 1600, 520, 1730),
    "btn_shop":          (540, 1600, 760, 1730),
    "btn_races":         (780, 1600, 1000, 1730),

    # Bottom bar
    "btn_skip":          (100, 1840, 320, 1890),
    "btn_quick":         (420, 1840, 640, 1890),
    "btn_log":           (740, 1840, 850, 1890),
    "btn_menu":          (950, 1840, 1050, 1890),

    # Training Items and Full Stats buttons
    "btn_training_items": (570, 330, 730, 370),
    "btn_full_stats":     (770, 330, 890, 370),
}


# ── Training stat selection screen ────────────────────────────────────────────
# After tapping "Training", shows 5 stat tiles at the bottom

STAT_SELECTION_REGIONS: dict[str, Region] = {
    # Header (same as turn action)
    "period_text":       (100, 45, 340, 75),
    "turn_counter":      (15, 80, 145, 165),
    "result_pts":        (175, 85, 400, 115),
    "energy_bar":        (170, 145, 700, 175),
    "mood_label":        (780, 145, 950, 180),

    # Selected training info (appears when a tile is selected/raised)
    "selected_label":    (30, 190, 350, 220),      # e.g. "Stamina Lvl 1"
    "selected_subtitle": (30, 220, 350, 250),      # e.g. "Breaststroke"

    # Stat gain previews (small green "+X" above each stat in the bottom bar)
    "gain_speed":        (38, 395, 155, 430),
    "gain_stamina":      (185, 395, 305, 430),
    "gain_power":        (335, 395, 455, 430),
    "gain_guts":         (485, 395, 600, 430),
    "gain_wit":          (635, 395, 750, 430),
    "gain_skill_pts":    (850, 395, 1000, 430),

    # Stat display row (same positions as turn action screen)
    "stat_speed":        (38, 445, 155, 490),
    "stat_stamina":      (185, 445, 305, 490),
    "stat_power":        (335, 445, 455, 490),
    "stat_guts":         (485, 445, 600, 490),
    "stat_wit":          (635, 445, 750, 490),
    "skill_pts":         (850, 445, 1000, 490),

    # Stat gain preview in the right panel (vertical list for selected tile)
    "panel_gain_speed":   (330, 285, 450, 320),
    "panel_gain_stamina": (330, 335, 450, 370),
    "panel_gain_power":   (330, 385, 450, 420),
    "panel_gain_guts":    (330, 435, 450, 470),
    "panel_gain_wit":     (330, 485, 450, 520),
    "panel_gain_skill":   (330, 535, 450, 570),

    # Failure rate display
    "failure_rate":      (60, 1400, 280, 1450),    # "Failure 34%"

    # Bottom bar
    "btn_back":          (30, 1850, 200, 1900),
    "btn_skip":          (250, 1850, 470, 1900),
    "btn_quick":         (520, 1850, 740, 1900),
    "btn_log":           (790, 1850, 900, 1900),
    "btn_menu":          (950, 1850, 1050, 1900),
}

# The 5 training tiles at the bottom of the stat selection screen.
# Tiles are arranged in a row; the selected one is raised.
# Tap targets are based on the default (unselected) position.
TRAINING_TILES: list[TileRegion] = [
    TileRegion(  # Speed (leftmost)
        tap_target=(30, 1530, 230, 1740),
        label=(30, 1660, 230, 1700),        # "Speed Lvl 1"
        indicator=(30, 1480, 230, 1530),     # Indicator icons above tile
        support_cards=(30, 1540, 230, 1660), # Card icons on tile
    ),
    TileRegion(  # Stamina
        tap_target=(235, 1530, 435, 1740),
        label=(235, 1660, 435, 1700),
        indicator=(235, 1480, 435, 1530),
        support_cards=(235, 1540, 435, 1660),
    ),
    TileRegion(  # Power
        tap_target=(440, 1530, 640, 1740),
        label=(440, 1660, 640, 1700),
        indicator=(440, 1480, 640, 1530),
        support_cards=(440, 1540, 640, 1660),
    ),
    TileRegion(  # Guts
        tap_target=(645, 1530, 845, 1740),
        label=(645, 1660, 845, 1700),
        indicator=(645, 1480, 845, 1530),
        support_cards=(645, 1540, 845, 1660),
    ),
    TileRegion(  # Wit (rightmost)
        tap_target=(850, 1530, 1050, 1740),
        label=(850, 1660, 1050, 1700),
        indicator=(850, 1480, 1050, 1530),
        support_cards=(850, 1540, 1050, 1660),
    ),
]

# Mapping from tile index to stat type
TILE_INDEX_TO_STAT: dict[int, StatType] = {
    0: StatType.SPEED,
    1: StatType.STAMINA,
    2: StatType.POWER,
    3: StatType.GUTS,
    4: StatType.WIT,
}

# Stat name to region key (for OCR on both screens)
STAT_REGION_KEYS: dict[StatType, str] = {
    StatType.SPEED:   "stat_speed",
    StatType.STAMINA: "stat_stamina",
    StatType.POWER:   "stat_power",
    StatType.GUTS:    "stat_guts",
    StatType.WIT:     "stat_wit",
}


# ── Event screen regions ──────────────────────────────────────────────────────
# Placeholder — to be calibrated when we capture an event screenshot

EVENT_REGIONS: dict[str, Region] = {
    "event_text":  (100, 300, 980, 700),       # Main event description text
    "choice_0":    (100, 1200, 980, 1300),     # First choice button
    "choice_1":    (100, 1320, 980, 1420),     # Second choice button
    "choice_2":    (100, 1440, 980, 1540),     # Third choice (if present)
}


# ── Skill shop regions ────────────────────────────────────────────────────────
# Placeholder — to be calibrated

SKILL_SHOP_REGIONS: dict[str, Region] = {
    "header":       (100, 30, 980, 80),
    "skill_list":   (50, 200, 1030, 1600),     # Scrollable skill list area
    "btn_confirm":  (600, 1700, 980, 1800),
    "btn_cancel":   (100, 1700, 480, 1800),
    "skill_pts":    (800, 80, 1000, 130),       # Available skill points
}


# ── Race list screen regions (after tapping "Races" button) ──────────────────
# Placeholder — to be calibrated from a real race list screenshot.
# The race list is a scrollable list of upcoming races.

RACE_LIST_REGIONS: dict[str, Region] = {
    "header":       (100, 30, 980, 80),        # "Race" header
    "btn_back":     (30, 1850, 200, 1900),     # Back button

    # Visible race slots (up to ~4–5 races visible at once)
    # Each slot has: race name, grade badge, distance, surface, season
    "race_0_name":     (200, 220, 850, 270),
    "race_0_detail":   (200, 270, 850, 310),   # "G1 | 2400m | Turf"
    "race_0_tap":      (100, 200, 980, 330),   # Full tap target

    "race_1_name":     (200, 360, 850, 410),
    "race_1_detail":   (200, 410, 850, 450),
    "race_1_tap":      (100, 340, 980, 470),

    "race_2_name":     (200, 500, 850, 550),
    "race_2_detail":   (200, 550, 850, 590),
    "race_2_tap":      (100, 480, 980, 610),

    "race_3_name":     (200, 640, 850, 690),
    "race_3_detail":   (200, 690, 850, 730),
    "race_3_tap":      (100, 620, 980, 750),

    "race_4_name":     (200, 780, 850, 830),
    "race_4_detail":   (200, 830, 850, 870),
    "race_4_tap":      (100, 760, 980, 890),
}

# Max visible race slots before scrolling is needed
RACE_LIST_VISIBLE_SLOTS = 5


# ── Race running screen regions ──────────────────────────────────────────────
# Placeholder — for during-race screens (passive, mainly for detection)

RACE_REGIONS: dict[str, Region] = {
    "btn_enter":    (600, 1700, 980, 1800),
    "btn_skip":     (100, 1700, 480, 1800),
    "race_name":    (200, 200, 880, 260),
}


# ── Lookup helpers ────────────────────────────────────────────────────────────

SCREEN_REGION_MAP: dict[ScreenState, dict[str, Region]] = {
    ScreenState.TRAINING: TURN_ACTION_REGIONS,
    ScreenState.EVENT: EVENT_REGIONS,
    ScreenState.SKILL_SHOP: SKILL_SHOP_REGIONS,
    ScreenState.RACE_ENTRY: RACE_LIST_REGIONS,
    ScreenState.RACE: RACE_REGIONS,
}


def get_region(screen: ScreenState, name: str) -> Region | None:
    """Look up a named region for a screen state."""
    regions = SCREEN_REGION_MAP.get(screen, {})
    return regions.get(name)


def get_tap_center(region: Region) -> tuple[int, int]:
    """Get the center point of a region (for tap coordinates)."""
    x1, y1, x2, y2 = region
    return ((x1 + x2) // 2, (y1 + y2) // 2)
