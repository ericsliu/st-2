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
    # Order matters: more-specific (more anchors) screens first.
    # TRAINING must come before LOADING to avoid false positives from
    # brief "Connecting" overlays that darken the center pixel.

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
    # Require multiple dark pixels to avoid false positives from
    # brief overlays or dark character art.
    ScreenAnchorSet(
        screen=ScreenState.LOADING,
        anchors=(
            # Center of screen — loading screens tend to have a dark overlay
            PixelAnchor(540, 960, 0, 40, 0, 40, 0, 40),
            # Upper area also dark
            PixelAnchor(540, 400, 0, 40, 0, 40, 0, 40),
            # Lower area also dark
            PixelAnchor(540, 1500, 0, 40, 0, 40, 0, 40),
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
    # ── Race list (race entry) screen ──────────────────────────────────
    # Green "Race" button band at bottom, plus turn counter to confirm career.
    ScreenAnchorSet(
        screen=ScreenState.RACE_ENTRY,
        anchors=(
            # Green "Race" button band — R≈97-121 G≈189-206 B≈5-36
            PixelAnchor(540, 1675, 90, 140, 175, 220, 10, 55),
            # Turn counter gold text (career screen)
            PixelAnchor(80, 115, 210, 255, 170, 225, 0, 45),
            # Header bar — dark purple-grey R≈73-97 G≈66-92 B≈93-125
            PixelAnchor(140, 20, 65, 110, 60, 105, 85, 140),
        ),
        min_matches=2,
    ),
    # ── Warning popup (e.g. consecutive race warning) ────────────────
    # Green "Warning" header bar at y≈600-660, white dialog body,
    # Cancel (white) and OK (green) buttons at bottom.
    ScreenAnchorSet(
        screen=ScreenState.WARNING_POPUP,
        anchors=(
            # Green header bar (avoid "Warning" text at y≈615-630)
            PixelAnchor(540, 600, 125, 155, 200, 220, 0, 20),
            # White dialog body center
            PixelAnchor(540, 960, 240, 255, 240, 255, 240, 255),
            # Green OK button (avoid "OK" text at y≈1240-1260)
            PixelAnchor(775, 1220, 145, 175, 210, 235, 0, 20),
        ),
        min_matches=3,
    ),
    # ── Pre-race screen (character + View Results / Race buttons) ─────
    # After confirming race entry, shows character stats with
    # "View Results" (white) and "Race" (green) buttons at bottom.
    # Green Race button at x=570-830, y=1715-1815 (lower than RACE_ENTRY).
    ScreenAnchorSet(
        screen=ScreenState.PRE_RACE,
        anchors=(
            # Green "Race" button — bright green at y≈1760
            PixelAnchor(700, 1760, 80, 170, 160, 230, 0, 70),
            # White "View Results" button at left — white at y≈1760
            PixelAnchor(380, 1760, 220, 255, 220, 255, 220, 255),
            # Menu icon area at far right — white square
            PixelAnchor(960, 1760, 200, 255, 200, 255, 200, 255),
        ),
        min_matches=2,
    ),
    # ── Post-race screens (results, rewards, standings) ─────────────
    # Multiple post-race screens share a bright background with result
    # info. The standings screen has "Try Again" + "Next" buttons.
    # The rewards screen has "Watch Concert" + "Next" buttons.
    # We detect the green "Next" button at x=570-970, y=1715-1815.
    ScreenAnchorSet(
        screen=ScreenState.POST_RACE,
        anchors=(
            # Green "Next" button — same position as pre-race "Race" button
            PixelAnchor(765, 1760, 80, 170, 160, 230, 0, 70),
            # Light/white background at center (result info area)
            PixelAnchor(540, 960, 200, 255, 200, 255, 200, 255),
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
    # Calibrated 2026-03-27 from MuMuPlayer 1080×1920 strips.
    "period_text":       (20, 68, 380, 110),       # "Classic Year Late Sep"
    "turn_counter":      (15, 130, 200, 290),      # "7 turn(s) left"
    "result_pts":        (280, 120, 620, 155),     # "200 Result Pts"
    "goal_progress":     (280, 155, 620, 190),     # "Progress: After 46 pts"
    "details_btn":       (900, 120, 1060, 170),    # "Details" button

    # Energy and mood
    "energy_bar":        (260, 240, 750, 275),     # The energy bar itself
    "mood_label":        (830, 230, 1000, 275),    # "GREAT" / mood text
    "mood_icon":         (780, 230, 830, 275),     # Arrow/face icon for mood

    # Scenario-specific
    "junior_result_pts": (30, 310, 250, 400),      # "Classic Result Pts 154 pts"
    "hint_icon":         (920, 280, 1060, 400),    # HINT indicator (top-right)

    # Stat display row
    # Measured from screenshots/calibration/stat_row_tight.png (y=1295-1330)
    # Grade badges are circular (~50px), followed by the number (~100px).
    # Thin vertical dividers separate each column.
    # y range 1295-1330 excludes the "/1200" line below.
    "stat_speed":        (115, 1295, 205, 1330),   # Speed value "457"
    "stat_stamina":      (270, 1295, 365, 1330),   # Stamina value "285"
    "stat_power":        (430, 1295, 525, 1330),   # Power value "429"
    "stat_guts":         (610, 1295, 700, 1330),   # Guts value "360"
    "stat_wit":          (775, 1295, 870, 1330),   # Wit value "328"
    "stat_max_speed":    (100, 1340, 210, 1380),   # "/1200"
    "stat_max_stamina":  (260, 1340, 370, 1380),   # "/1200"
    "stat_max_power":    (420, 1340, 530, 1380),   # "/1200"
    "stat_max_guts":     (585, 1340, 695, 1380),   # "/1200"
    "stat_max_wit":      (750, 1340, 860, 1380),   # "/1200"
    "skill_pts":         (900, 1295, 1050, 1330),  # "755"

    # Grade letters (F/E/D/C/B/A/S) — circular badges left of numbers
    "grade_speed":       (30, 1295, 110, 1340),
    "grade_stamina":     (195, 1295, 265, 1340),
    "grade_power":       (360, 1295, 425, 1340),
    "grade_guts":        (525, 1295, 595, 1340),
    "grade_wit":         (690, 1295, 760, 1340),

    # Action buttons (Trackblazer has 7: 3 top row + 4 bottom row)
    "btn_rest":          (45, 1420, 330, 1570),
    "btn_training":      (340, 1420, 680, 1570),
    "btn_skills":        (690, 1420, 1000, 1570),
    "btn_infirmary":     (45, 1590, 260, 1740),
    "btn_recreation":    (275, 1590, 520, 1740),
    "btn_shop":          (540, 1590, 760, 1740),
    "btn_races":         (780, 1590, 1000, 1740),

    # Bottom bar
    "btn_skip":          (100, 1830, 320, 1890),
    "btn_quick":         (420, 1830, 640, 1890),
    "btn_log":           (740, 1830, 850, 1890),
    "btn_menu":          (950, 1830, 1050, 1890),

    # Training Items and Full Stats buttons
    "btn_training_items": (620, 1120, 780, 1180),
    "btn_full_stats":     (820, 1120, 980, 1180),
}


# ── Training stat selection screen ────────────────────────────────────────────
# After tapping "Training", shows 5 stat tiles at the bottom.
# Calibrated 2026-03-27 from MuMuPlayer 1080×1920 stat_selection.png.

STAT_SELECTION_REGIONS: dict[str, Region] = {
    # Header info (same as turn action screen)
    "period_text":       (20, 50, 380, 100),        # "Senior Year Late Jun"
    "turn_counter":      (15, 100, 200, 270),       # "13 turn(s) left"
    "result_pts":        (180, 100, 500, 140),      # "300 Result Pts"
    "energy_bar":        (365, 220, 715, 226),      # Energy bar inner (trimmed past rounded caps)
    "mood_label":        (470, 195, 540, 230),      # "GREAT"

    # Selected training info (appears when a tile is selected/raised)
    "selected_label":    (10, 290, 350, 340),       # e.g. "Speed Lvl 3"
    "selected_subtitle": (10, 340, 350, 390),       # e.g. "Exercise Bike"

    # Stat gain previews (small green "+X" above each stat label)
    # Measured from OCR strip scan: gains at y=1190-1250.
    # Regions are slightly wider than stat columns to catch full numbers.
    "gain_speed":        (20, 1185, 175, 1255),
    "gain_stamina":      (175, 1185, 335, 1255),
    "gain_power":        (335, 1185, 510, 1255),
    "gain_guts":         (510, 1185, 670, 1255),
    "gain_wit":          (670, 1185, 845, 1255),
    "gain_skill_pts":    (860, 1185, 1060, 1255),

    # Stat label + value row: labels at y=1250-1300, values at y=1300-1370
    "stat_speed":        (30, 1260, 170, 1370),
    "stat_stamina":      (180, 1260, 320, 1370),
    "stat_power":        (340, 1260, 500, 1370),
    "stat_guts":         (510, 1260, 660, 1370),
    "stat_wit":          (680, 1260, 860, 1370),
    "skill_pts":         (870, 1290, 1050, 1370),

    # Right panel: support card character portraits (stacked vertically)
    # Portraits appear here for the currently selected/raised tile.
    "support_panel":     (850, 280, 1070, 1200),

    # Failure rate display — below stat values
    "failure_rate":      (10, 1360, 280, 1460),     # "Failure 8%"

    # Bottom bar
    "btn_back":          (10, 1840, 180, 1910),
    "btn_skip":          (220, 1840, 450, 1910),
    "btn_quick":         (490, 1840, 700, 1910),
    "btn_log":           (750, 1840, 870, 1910),
    "btn_menu":          (920, 1840, 1060, 1910),
}

# The 5 training tiles at the bottom of the stat selection screen.
# Tiles are arranged in a row; the selected one is raised (shifts up).
# Calibrated 2026-03-27: tile labels at y=1550-1700, bubble centers ~y=1500-1550.
# Tap targets cover the full bubble area including labels.
TRAINING_TILES: list[TileRegion] = [
    TileRegion(  # Speed (leftmost)
        tap_target=(10, 1460, 190, 1700),
        label=(10, 1580, 190, 1660),         # "Speed Lvl 3"
        indicator=(10, 1410, 190, 1460),      # Indicator icons above tile
        support_cards=(10, 1470, 190, 1580),  # Card icons on tile
    ),
    TileRegion(  # Stamina
        tap_target=(200, 1460, 380, 1700),
        label=(200, 1580, 380, 1660),
        indicator=(200, 1410, 380, 1460),
        support_cards=(200, 1470, 380, 1580),
    ),
    TileRegion(  # Power
        tap_target=(400, 1460, 580, 1700),
        label=(400, 1580, 580, 1660),
        indicator=(400, 1410, 580, 1460),
        support_cards=(400, 1470, 580, 1580),
    ),
    TileRegion(  # Guts
        tap_target=(590, 1460, 770, 1700),
        label=(590, 1580, 770, 1660),
        indicator=(590, 1410, 770, 1460),
        support_cards=(590, 1470, 770, 1580),
    ),
    TileRegion(  # Wit (rightmost)
        tap_target=(780, 1460, 960, 1700),
        label=(780, 1580, 960, 1660),
        indicator=(780, 1410, 960, 1460),
        support_cards=(780, 1470, 960, 1580),
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

# ── Warning popup regions ─────────────────────────────────────────────────
# Generic warning dialog with green header, text body, Cancel + OK buttons.

WARNING_POPUP_REGIONS: dict[str, Region] = {
    "header":       (50, 560, 1030, 665),     # Green "Warning" header bar
    "text":         (50, 670, 1030, 850),      # Warning message text
    "detail":       (50, 850, 1030, 1100),     # Detail/explanation text
    "btn_cancel":   (30, 1195, 540, 1300),     # Cancel button (white)
    "btn_ok":       (560, 1195, 990, 1300),    # OK button (green)
}


RACE_LIST_REGIONS: dict[str, Region] = {
    "header":       (100, 30, 980, 80),        # "Race List" header
    "btn_back":     (20, 1840, 130, 1900),     # Back button
    "btn_race":     (370, 1640, 710, 1720),    # Green "Race" button
    "btn_predictions": (30, 1640, 270, 1720),  # Predictions button
    "btn_agenda":   (810, 1640, 1050, 1720),   # Agenda button

    # Race list entries. Each entry is ~220px tall.
    # The "name" region captures the grade badge + venue/distance detail line.
    # The "detail" line has "Venue Surface Distancem (Category) Track / Position".
    "race_0_name":     (370, 1080, 1060, 1130),  # "G1 Nakayama Turf 1200m..."
    "race_0_detail":   (370, 1130, 1060, 1200),  # Reward line: "+100 pts..."
    "race_0_tap":      (50, 1060, 1060, 1240),   # Full tap target

    "race_1_name":     (370, 1300, 1060, 1350),
    "race_1_detail":   (370, 1350, 1060, 1420),
    "race_1_tap":      (50, 1280, 1060, 1460),

    # Races 2+ may require scrolling; keep them but they may not be visible
    "race_2_name":     (370, 1520, 1060, 1570),
    "race_2_detail":   (370, 1570, 1060, 1640),
    "race_2_tap":      (50, 1500, 1060, 1680),
}

# Max visible race slots before scrolling is needed
RACE_LIST_VISIBLE_SLOTS = 2


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
    ScreenState.WARNING_POPUP: WARNING_POPUP_REGIONS,
}


def get_region(screen: ScreenState, name: str) -> Region | None:
    """Look up a named region for a screen state."""
    regions = SCREEN_REGION_MAP.get(screen, {})
    return regions.get(name)


def get_tap_center(region: Region) -> tuple[int, int]:
    """Get the center point of a region (for tap coordinates)."""
    x1, y1, x2, y2 = region
    return ((x1 + x2) // 2, (y1 + y2) // 2)
