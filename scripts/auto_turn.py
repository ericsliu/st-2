"""Automated turn executor. Runs one turn at a time with full logging.

Uses uma_trainer decision components for training scoring, skill buying,
and race selection. Screen detection and tap handling remain in this script.
"""
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.career_helper import adb, screenshot, tap
from scripts.ocr_util import ocr_region, ocr_full_screen
from PIL import Image

# uma_trainer decision components
from uma_trainer.config import ScorerConfig
from uma_trainer.decision.scorer import TrainingScorer
from uma_trainer.decision.skill_buyer import SkillBuyer
from uma_trainer.decision.shop_manager import ShopManager
from uma_trainer.decision.race_selector import RaceSelector
from uma_trainer.decision.event_handler import EventHandler
from uma_trainer.knowledge.overrides import OverridesLoader
from uma_trainer.types import (
    ActionType,
    BotAction,
    EventChoice,
    GameState,
    Mood,
    RaceOption,
    ScreenState,
    SkillOption,
    StatType,
    SupportCard,
    TraineeStats,
    TrainingTile,
)

LOG = Path("screenshots/run_log/run_current.md")
DEVICE = os.environ.get("ADB_SERIAL", "emulator-5554")

# State tracking to avoid loops
_last_result = None
# Last race placement (1=win, 99=unknown)
_last_race_placement = 99
_last_race_was_g1 = False
_last_race_distance = 0  # metres, set when race is selected

# --- uma_trainer component initialization ---
_overrides = OverridesLoader("data/overrides")
_scorer_config = ScorerConfig()
_scorer = TrainingScorer(_scorer_config, overrides=_overrides)

# Knowledge base (auto-creates SQLite DB, accumulates event/skill data over runs)
from uma_trainer.knowledge.database import KnowledgeBase
_kb = KnowledgeBase("data/uma_trainer.db")

_skill_buyer = SkillBuyer(kb=_kb, scorer=_scorer)
_shop_manager = ShopManager(overrides=_overrides)

# Load scenario and runspec so the scorer knows about summer camp, stat targets, etc.
from uma_trainer.scenario import load_scenario
from uma_trainer.decision.runspec import load_runspec
from uma_trainer.decision.summer_planner import plan_summer_turn
from uma_trainer.decision.lookahead import should_conserve_energy
_scenario = load_scenario("trackblazer")
_runspec = load_runspec("end_guts_v1")
_scorer.scenario = _scenario
_scorer.runspec = _runspec
_scorer.shop_manager = _shop_manager
# Inventory is read from Training Items screen on first career_home — no yaml loading
_race_selector = RaceSelector(kb=_kb, overrides=_overrides, scenario=_scenario)
_event_handler = EventHandler(kb=_kb, local_llm=None, claude_client=None, overrides=_overrides)

from uma_trainer.perception.card_tracker import CardTracker
_card_tracker = CardTracker()

# Live packet→GameState pipeline. Tailer watches the most recent capture
# session under data/packet_captures/; adapter turns the latest decoded
# response into the same GameState shape OCR produces. UMA_PACKET_STATE=0
# disables the overlay (OCR-only mode); freshness gate inside the helper
# keeps us safe when the probe isn't running.
import os as _os
from uma_trainer.perception.carrotjuicer.session_tailer import SessionTailer
from uma_trainer.perception.carrotjuicer.state_adapter import (
    CardRegistry,
    game_state_from_response,
)
from uma_trainer.perception.carrotjuicer.card_semantic import load_card_semantic_map
from uma_trainer.knowledge.skill_catalog import SkillCatalog
_session_tailer = SessionTailer(max_age_s=30.0)
_card_registry: CardRegistry | None = None
try:
    _card_registry = CardRegistry()
except Exception:
    _card_registry = None
_skill_catalog: SkillCatalog | None = None
try:
    _skill_catalog = SkillCatalog()
except Exception:
    _skill_catalog = None
_PACKET_STATE_ENABLED = _os.environ.get("UMA_PACKET_STATE", "1") != "0"
# Opt-in (default OFF) for the packet-driven training-tile path. Skips
# the per-tile preview tap+OCR loop when the live capture is fresh; the
# OCR loop remains the safe default while we validate it on real runs.
_PACKET_TRAINING_ENABLED = _os.environ.get("UMA_PACKET_TRAINING", "1") == "1"
_CARD_SEMANTIC_MAP = load_card_semantic_map()

# Race plaque matcher (lazy init — loads 302 templates on first use)
from uma_trainer.perception.plaque_matcher import PlaqueMatcher
_plaque_matcher: PlaqueMatcher | None = None


def _get_plaque_matcher() -> PlaqueMatcher:
    global _plaque_matcher
    if _plaque_matcher is None:
        _plaque_matcher = PlaqueMatcher()
    return _plaque_matcher

# Playbook (optional — None means legacy behavior, no turn schedule)
from uma_trainer.decision.playbook import load_playbook, PlaybookEngine
_playbook_engine: PlaybookEngine | None = None
_FALLBACK_FLAG = Path("data/sirius_fallback.flag")
_strategy_name = "sirius_riko_v1_fallback" if _FALLBACK_FLAG.exists() else "sirius_riko_v1"
# UMA_STRATEGY=none disables the playbook entirely (legacy scorer-only mode);
# any other value selects a strategy yaml from data/strategies/.
_strategy_override = _os.environ.get("UMA_STRATEGY")
if _strategy_override:
    _strategy_name = _strategy_override
if _strategy_name.lower() in ("none", "off", "disabled"):
    print("[strategy] Playbook disabled (legacy scorer-only mode)")
    _playbook_engine = None
else:
    print(f"[strategy] Loaded: {_strategy_name}{' (FALLBACK active)' if _FALLBACK_FLAG.exists() else ''}")
    _playbook_engine = load_playbook(_strategy_name)
if _playbook_engine:
    # Playbook can override the default runspec (e.g. Sirius uses sirius_speed_v1)
    if _playbook_engine.playbook.runspec:
        _runspec = load_runspec(_playbook_engine.playbook.runspec)
        _scorer.runspec = _runspec
        if _runspec.low_bond_threshold_cards:
            _scorer.set_card_bond_thresholds(
                {c: 60 for c in _runspec.low_bond_threshold_cards}
            )
    _playbook_engine.scorer = _scorer
    _playbook_engine.race_selector = _race_selector
    _playbook_engine._scenario = _scenario
    if _playbook_engine.playbook.item_priorities:
        _shop_manager.set_item_priorities(_playbook_engine.playbook.item_priorities)
    if _playbook_engine.playbook.race:
        _race_selector.set_race_policy(_playbook_engine.playbook.race)
    if _playbook_engine.playbook.friendship and _playbook_engine.playbook.friendship.priority_order:
        priority_order = list(_playbook_engine.playbook.friendship.priority_order)
        # Once Sirius bond is unlocked, drop team_sirius so the next card
        # (Riko) becomes the top priority for bond-building.
        if Path("data/sirius_bond_unlocked.flag").exists():
            priority_order = [c for c in priority_order if c != "team_sirius"]
            _scorer.mark_bond_complete("team_sirius")
        if Path("data/riko_recreation_unlocked.flag").exists():
            priority_order = [c for c in priority_order if c != "riko"]
            _scorer.set_bond_override("riko", 80)
            _scorer.mark_bond_complete("riko")
        _scorer.set_friendship_priorities(priority_order)

def _promote_post_sirius_priorities():
    """After Sirius bond unlock, drop team_sirius from friendship priority so
    the next card (Riko) becomes the top bond-building target."""
    if not (_playbook_engine and _playbook_engine.playbook.friendship
            and _playbook_engine.playbook.friendship.priority_order):
        return
    new_order = [c for c in _playbook_engine.playbook.friendship.priority_order
                 if c != "team_sirius"]
    _scorer.set_friendship_priorities(new_order)
    log(f"Post-Sirius bond: friendship priority promoted to {new_order}")


BTN_RECREATION = (378, 1750)

# Persistent state across turns (updated as we learn more)
_current_turn = 0
_current_stats = TraineeStats()
_skill_pts = 0
_cached_aptitudes = None  # Read once from Full Stats screen, then reused
_active_conditions = []   # Negative conditions detected this session
_positive_statuses = []   # Positive statuses (charming, practice perfect, etc.)
_game_state = None        # Last built GameState, reused across screens
_summer_whistle_used = False  # Reset each turn; prevents double-whistling
_playbook_force_train = False  # Set by playbook TRAIN turns; handle_training must not rest
_train_drink_used = False      # Set after we drink-and-retry in handle_training (prevents loops)
# Set when handle_training backs out to use an item (ankle weight, drink, whistle)
# and plans to re-enter to train a specific stat. On re-entry, handle_training
# taps the decided tile directly and skips the preview/score loop.
_pending_training_stat = None   # "speed" | "stamina" | "power" | "guts" | "wit"
_pending_training_turn = -1
_ts_climax_retries = 0        # Retry counter for TS Climax races (max 3)
_g1_retries = 0               # Total alarm clocks used this career (max 5)
_g1_retried_this_race = False # True after we retry the current race (1 retry per race max)
_backed_out_to_home_this_turn = False  # Prevents infinite back-out loop on recreation turns
_race_attempted_turn = -1             # Turn number of last race attempt — prevents re-entry loop
_recovery_skills_bought = 0           # Track recovery skills bought (need 2+ before Kikuka Sho)
_RECOVERY_SKILL_NAMES = {"corner recovery", "straightaway recover", "standing by", "after-school stroll"}
_prev_stats = None                # Previous turn's stats for suspicious jump detection
# Persisted flag: set True when Sirius bond event fires. Survives run_one.py
# restarts via data/sirius_bond_unlocked.flag so the playbook friendship
# deadline check doesn't false-alarm every turn after Junior Nov.
_SIRIUS_BOND_FILE = Path("data/sirius_bond_unlocked.flag")
_sirius_bond_unlocked = _SIRIUS_BOND_FILE.exists()

# Persisted flag: set True when Riko's "Unexpected Side" event fires (gates
# her recreation chain). Survives run_one.py restarts.
_RIKO_REC_FILE = Path("data/riko_recreation_unlocked.flag")
_riko_recreation_unlocked = _RIKO_REC_FILE.exists()

# Map negative conditions to their cure items in the shop catalogue
CONDITION_CURES = {
    "night owl": "fluffy_pillow",
    "migraine": "aroma_diffuser",
    "skin outbreak": "rich_hand_cream",
    "slacker": "pocket_planner",
    "practice poor": "practice_dvd",
    "overweight": "smart_scale",
}

# Career home button coordinates (1080x1920 portrait, never move)
BTN_REST = (185, 1525)
BTN_TRAINING = (540, 1550)
BTN_INFIRMARY = (162, 1750)
# These two move depending on screen layout — only valid on career_home
BTN_HOME_SKILLS = (918, 1535)
BTN_HOME_RACES = (920, 1750)
BTN_SHOP = (620, 1640)
BTN_TRAINING_ITEMS = (827, 1130)
BTN_TRAINING_ITEMS_RACE = (827, 1260)  # Training Items on race screens (ts_climax_race, required_race)
BTN_ITEMS_CONFIRM = (779, 1772)  # "Confirm Use" / "Use Training Items" right button
BTN_ITEMS_CLOSE = (303, 1772)    # "Close" left button
BTN_LOG = (810, 1870)            # Log button on career_home bottom bar


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def px(img, x, y):
    return img.getpixel((x, y))[:3]




def detect_mood(img):
    """Detect mood from the mood icon text via OCR.

    Returns one of: GREAT, GOOD, NORMAL, BAD, AWFUL, UNKNOWN
    """
    mood_text = ocr_region(img, 800, 160, 1080, 260, save_path="/tmp/mood_crop.png")
    if isinstance(mood_text, list):
        mood_text = " ".join(t for t, c in mood_text)
    mood_upper = mood_text.strip().upper()
    # Check for substrings — OCR may pick up arrow icon as extra chars
    for mood in ("GREAT", "AWFUL", "GOOD", "NORMAL", "BAD"):
        if mood in mood_upper:
            return mood
    # Fallback: partial matches (OCR may clip first letters)
    if "WFUL" in mood_upper:
        return "AWFUL"
    if "REAT" in mood_upper:
        return "GREAT"
    return "UNKNOWN"


def _parse_aptitudes_from_image(img):
    """OCR the Full Stats screen image and extract aptitude grades.

    Returns dict like {'turf': 'A', 'dirt': 'E', 'short': 'D', 'mile': 'A', 'medium': 'A', 'long': 'S'}
    """
    import re
    from scripts.ocr_util import ocr_image
    from PIL import ImageOps

    valid_grades = {"S", "A", "B", "C", "D", "E", "F", "G"}
    label_map = {
        "turf": "turf", "dirt": "dirt",
        "sprint": "short", "mile": "mile", "medium": "medium", "long": "long",
        "front": "front", "end": "end", "pace": "pace", "late": "late",
    }
    aptitudes = {}
    w, h = img.size

    def _extract_from_results(results):
        """Extract aptitudes from OCR results list."""
        # Look for combined "Label Grade" strings (e.g. "Turf A", "Mile A")
        for text, conf, bbox in results:
            if conf < 0.3:
                continue
            t = text.strip()
            m = re.match(r'^(Turf|Dirt|Sprint|Mile|Medium|Long)\s+([A-GS])\)?$', t, re.IGNORECASE)
            if m:
                label = m.group(1).lower()
                grade = m.group(2).upper()
                if label in label_map and grade in valid_grades:
                    aptitudes[label_map[label]] = grade

        # For any missing, match isolated labels to nearby grade letters
        if len(aptitudes) < 6:
            labels_found = {}
            single_letters = []
            for text, conf, bbox in results:
                if conf < 0.2:
                    continue
                t = text.strip()
                tl = t.lower()
                if tl in label_map and label_map[tl] not in aptitudes:
                    cx = bbox[0] + bbox[2] / 2
                    cy = bbox[1] + bbox[3] / 2
                    labels_found[label_map[tl]] = (cx, cy)
                if len(t) == 1 and t.upper() in valid_grades:
                    cx = bbox[0] + bbox[2] / 2
                    cy = bbox[1] + bbox[3] / 2
                    single_letters.append((t.upper(), cx, cy))

            for key, (lx, ly) in labels_found.items():
                best = None
                best_dist = 999
                for grade, gx, gy in single_letters:
                    if gx > lx and abs(gy - ly) < 0.04 and (gx - lx) < 0.15:
                        # Combined distance so y-proximity breaks x-ties
                        dist = (gx - lx) + abs(gy - ly)
                        if dist < best_dist:
                            best = grade
                            best_dist = dist
                if best:
                    aptitudes[key] = best

    # Pass 1: Wide crop covering Track + Distance rows
    y1 = int(h * 0.20)
    y2 = int(h * 0.55)
    crop = img.crop((0, y1, w, y2))
    crop.save("/tmp/aptitude_crop.png")
    _extract_from_results(ocr_image("/tmp/aptitude_crop.png"))

    # Pass 2: Tighter Track row crop (y=600-680 at 1080x1920) for small grade icons
    if len(aptitudes) < 6:
        track_crop = img.crop((0, 600, w, 680))
        track_crop.save("/tmp/aptitude_track.png")
        _extract_from_results(ocr_image("/tmp/aptitude_track.png"))

    # Pass 3: Tighter Distance row crop (y=680-760) for any missing distance aptitudes
    if len(aptitudes) < 6:
        dist_crop = img.crop((0, 680, w, 760))
        dist_crop.save("/tmp/aptitude_dist.png")
        _extract_from_results(ocr_image("/tmp/aptitude_dist.png"))

    # Pass 4: Style row crop (y=720-780) for front/end/pace/late aptitudes
    if not all(k in aptitudes for k in ("front", "end", "pace", "late")):
        style_crop = img.crop((0, 720, w, 780))
        style_crop.save("/tmp/aptitude_style.png")
        _extract_from_results(ocr_image("/tmp/aptitude_style.png"))

    return aptitudes


def read_fullstats():
    """Navigate to Full Stats screen, OCR aptitudes + conditions, close.

    Fallback path. Primary source is the packet overlay in
    ``build_game_state()``; this only runs when ``_session_tailer.is_fresh()``
    is False or ``UMA_PACKET_STATE=0`` (see ``_should_call_fullstats``).
    Returns dict of aptitudes or None on failure.
    """
    global _cached_aptitudes, _active_conditions, _positive_statuses, _sirius_bond_unlocked

    log("Reading Full Stats...")
    tap(990, 1160, delay=2.0)

    img = screenshot(f"full_stats_{int(time.time())}")

    # Verify we're on the full stats screen
    texts = [t.strip().lower() for t, c, y in ocr_full_screen(img) if c > 0.3]
    all_text = " ".join(texts)
    if "track" not in all_text or "distance" not in all_text:
        log("WARNING: Full Stats screen not detected, falling back to strategy.yaml")
        tap(540, 1800, delay=1.0)
        return None

    # Read aptitudes
    aptitudes = _parse_aptitudes_from_image(img)
    if len(aptitudes) >= 4:
        _cached_aptitudes = aptitudes

    # Read conditions using header bar colors to distinguish:
    #   Blue bar (R<100, B>150) = negative condition (Night Owl, Slacker, etc.)
    #   Orange bar (R>200, G:100-200, B<100) = positive buff (Charming, Pure Passion, etc.)
    # This avoids false positives from skill descriptions mentioning condition names.
    import numpy as np
    arr = np.array(img)
    raw_entries = [(t.strip().lower(), c, y) for t, c, y in ocr_full_screen(img) if c > 0.3]
    neg_texts = []
    pos_texts = []
    for text, conf, y_pos in raw_entries:
        if y_pos < 930:
            continue
        # Sample bar color at this text's Y position, center of screen
        py = min(int(y_pos), arr.shape[0] - 1)
        r, g, b = int(arr[py, 540, 0]), int(arr[py, 540, 1]), int(arr[py, 540, 2])
        if b > 150 and b > r + 30 and b > g:
            neg_texts.append(text)
        elif r > 200 and 80 < g < 200 and b < 120:
            pos_texts.append(text)
    neg_text = " ".join(neg_texts)
    pos_text = " ".join(pos_texts)
    conditions = []
    for condition_name in CONDITION_CURES:
        if condition_name in neg_text:
            conditions.append(condition_name)
    _active_conditions = conditions

    # Detect positive statuses from orange-bar entries
    POSITIVE_KEYWORDS = ["charming", "practice perfect", "hot topic", "pure passion"]
    _positive_statuses = [s for s in POSITIVE_KEYWORDS if s in pos_text]

    # NOTE: Sirius bond unlock detection moved to the packet overlay block in
    # build_game_state() — driven by ``state.positive_statuses`` carrying
    # ``"pure passion"`` (chara_effect_id 100/101). This OCR fallback path
    # only runs when the capture is stale, in which case the game-log
    # "shining stars" probe in _read_game_log() catches the unlock.

    if aptitudes:
        log(f"Aptitudes: {aptitudes}")
    if conditions:
        log(f"Active conditions: {conditions}")
    else:
        log("No negative conditions")
    if _positive_statuses:
        log(f"Positive statuses: {_positive_statuses}")

    # Tap Close button (bottom center of Full Stats screen)
    # y=1770 avoids hitting Quick button (y≈1860) if career home loads early
    tap(540, 1770, delay=1.5)

    return aptitudes


def cure_conditions_from_inventory():
    """If we have cure items for active conditions, use them from Training Items."""
    if not _active_conditions:
        return

    # Check which conditions we can cure with inventory
    inventory = _shop_manager.inventory
    cure_keys = []
    for condition in _active_conditions:
        cure_key = CONDITION_CURES.get(condition)
        if cure_key and inventory.get(cure_key, 0) > 0:
            cure_keys.append(cure_key)
            log(f"Can cure '{condition}' with {cure_key}")

    # Also check miracle_cure for any condition
    if _active_conditions and not cure_keys and inventory.get("miracle_cure", 0) > 0:
        cure_keys.append("miracle_cure")
        log(f"Using miracle_cure for: {_active_conditions}")

    if not cure_keys:
        return

    _use_training_items(cure_keys)
    # Remove used items from inventory and clear cured conditions
    for key in cure_keys:
        if _shop_manager._inventory.get(key, 0) > 0:
            _shop_manager._inventory[key] -= 1
            if _shop_manager._inventory[key] <= 0:
                del _shop_manager._inventory[key]
    # Mark conditions as cured so Phase 3 doesn't re-act on them
    cured = {c for c in _active_conditions
             if CONDITION_CURES.get(c) in cure_keys or "miracle_cure" in cure_keys}
    for c in cured:
        _active_conditions.remove(c)
    _shop_manager.save_inventory()


_PACKET_OVERLAY_SCREENS = {
    "career_home", "career_home_summer", "ts_climax_home", "training",
}


def _build_packet_training_tiles() -> list[TrainingTile] | None:
    """Return TrainingTile list built from the latest packet, or None.

    Returns None when ``UMA_PACKET_TRAINING`` is off, the session is stale,
    or the response doesn't contain ``home_info.command_info_array``. The
    adapter has already populated gains, failure_rate, support_cards, and
    bond_levels for each tile — we just paste auto_turn's tap coordinates
    in so the rest of ``handle_training`` can use them as drop-in tiles.
    """
    if not _PACKET_TRAINING_ENABLED or not _session_tailer.is_fresh():
        return None
    state = _packet_overlay_state("training")
    if state is None or not state.training_tiles:
        return None
    out: list[TrainingTile] = []
    for tile in state.training_tiles:
        tile_name = tile.stat_type.value.capitalize()
        coords = TRAINING_TILES.get(tile_name, (0, 0))
        out.append(
            TrainingTile(
                stat_type=tile.stat_type,
                tap_coords=coords,
                stat_gains=tile.stat_gains,
                support_cards=tile.support_cards,
                bond_levels=tile.bond_levels,
                has_hint=tile.has_hint,
                failure_rate=tile.failure_rate,
            )
        )
    return out


def _should_call_fullstats() -> bool:
    """Return True when the OCR Full Stats screen pass must run.

    Packet path is preferred when the live capture is fresh AND
    ``UMA_PACKET_STATE`` isn't explicitly disabled. Otherwise we fall
    through to the OCR screen pass that ``read_fullstats()`` performs
    (aptitudes, conditions, positive statuses, Sirius bond probe).
    """
    if _os.environ.get("UMA_PACKET_STATE") == "0":
        return True
    if not _session_tailer.is_fresh():
        return True
    return False


def _packet_overlay_state(screen_type: str) -> GameState | None:
    """Build a GameState from the latest captured response, or None.

    Returns None when packet capture is disabled, the latest session has
    gone stale (probe not running), or the screen type is not one the
    adapter currently covers. Bot decision code reads the OCR-built
    GameState in those cases.
    """
    if not _PACKET_STATE_ENABLED:
        return None
    if screen_type not in _PACKET_OVERLAY_SCREENS:
        return None
    if not _session_tailer.is_fresh():
        return None
    response = _session_tailer.latest_response(
        endpoint_keys=("chara_info", "home_info"),
    )
    if response is None:
        return None
    try:
        return game_state_from_response(
            response,
            registry=_card_registry,
            screen=ScreenState.TRAINING,
            card_semantic_map=_CARD_SEMANTIC_MAP or None,
            skill_catalog=_skill_catalog,
        )
    except Exception as e:
        log(f"[packet-state] adapter failed: {e!r}")
        return None


def build_game_state(img, screen_type: str, energy: int = -1) -> GameState:
    """Build a GameState from auto_turn's screen data.

    This bridges auto_turn.py's raw OCR/pixel data into the uma_trainer
    type system so decision components can consume it.
    """
    global _current_turn, _current_stats, _skill_pts, _cached_aptitudes
    global _active_conditions, _positive_statuses, _sirius_bond_unlocked

    # Map auto_turn screen names to ScreenState
    screen_map = {
        "career_home": ScreenState.TRAINING,
        "career_home_summer": ScreenState.TRAINING,
        "ts_climax_home": ScreenState.TRAINING,
        "ts_climax_race": ScreenState.RACE_ENTRY,
        "training": ScreenState.TRAINING,
        "event": ScreenState.EVENT,
        "race_list": ScreenState.RACE_ENTRY,
        "skill_shop": ScreenState.SKILL_SHOP,
        "complete_career": ScreenState.RESULT_SCREEN,
    }

    # Prefer packet-driven state when a fresh capture is available. This
    # skips period+stat OCR entirely; the rest of the bot keeps reading
    # _current_turn / _current_stats / _skill_pts globals.
    overlay = _packet_overlay_state(screen_type)
    if overlay is not None:
        if overlay.current_turn:
            _current_turn = overlay.current_turn
        _current_stats = overlay.stats
        _skill_pts = overlay.skill_pts
        # Sync aptitudes so module-level callers (run-style picker, etc.)
        # see packet-derived values without needing the Full Stats OCR pass.
        if overlay.trainee_aptitudes:
            _cached_aptitudes = dict(overlay.trainee_aptitudes)
        # Sync conditions + positive statuses from packet so read_fullstats()
        # (the OCR fallback) only runs when the capture is stale or disabled.
        _active_conditions = list(overlay.condition_keys)
        _positive_statuses = list(overlay.positive_statuses)
        # Pure Passion (chara_effect_id 100/101) === Team Sirius bond unlock.
        # Server-authoritative — appears the moment the bond event resolves
        # and persists for the rest of the run. Replaces the OCR detector
        # in read_fullstats() and the game-log "shining stars" probe.
        if "pure passion" in _positive_statuses and not _sirius_bond_unlocked:
            _sirius_bond_unlocked = True
            try:
                _SIRIUS_BOND_FILE.touch()
            except Exception as e:
                log(f"[packet-state] could not touch sirius bond flag: {e!r}")
            log("[packet-state] Pure Passion detected — Sirius bond unlocked")
            try:
                _scorer.set_bond_override("team_sirius", 60)
                _scorer.mark_bond_complete("team_sirius")
                _promote_post_sirius_priorities()
            except Exception as e:
                log(f"[packet-state] sirius bond hooks failed: {e!r}")
        overlay.screen = screen_map.get(screen_type, ScreenState.UNKNOWN)
        if screen_type == "career_home_summer":
            overlay.current_turn = max(overlay.current_turn, 25)
        if energy >= 0:
            overlay.energy = energy
        log(
            f"[packet-state] Stats: Spd={overlay.stats.speed} "
            f"Sta={overlay.stats.stamina} Pow={overlay.stats.power} "
            f"Gut={overlay.stats.guts} Wit={overlay.stats.wit} "
            f"SP={overlay.skill_pts} energy={overlay.energy} turn={overlay.current_turn}"
        )
        return overlay

    # Read period text from top-left (e.g. "Classic Year Early Apr")
    # The "X turn(s) left" is a GOAL DEADLINE, not a turn counter.
    # Derive absolute turn from year + month + half.
    MONTH_OFFSETS = {
        "jan": 0, "feb": 2, "mar": 4, "apr": 6, "may": 8, "jun": 10,
        "jul": 12, "aug": 14, "sep": 16, "oct": 18, "nov": 20, "dec": 22,
    }
    YEAR_OFFSETS = {"junior": 0, "classic": 24, "senior": 48}
    try:
        period_text = ocr_region(img, 20, 68, 380, 110, save_path="/tmp/period_crop.png")
        if isinstance(period_text, list):
            period_text = " ".join(t for t, c in period_text if c > 0.3)
        pt = period_text.strip().lower()
        # Split into words to avoid substring false positives (e.g. "jun" in "junior")
        words = pt.split()
        year_offset = next((v for k, v in YEAR_OFFSETS.items() if k in words), None)
        # Handle special periods (Pre-Debut, Debut) that don't have month names
        if "pre-debut" in pt or "pre debut" in pt:
            if year_offset is not None:
                _current_turn = year_offset + 1  # Turns 1-11
                log(f"Period: '{period_text.strip()}' → Turn {_current_turn} (Pre-Debut)")
        elif "debut" in pt and "pre" not in pt:
            if year_offset is not None:
                _current_turn = year_offset + 12  # Turn 12
                log(f"Period: '{period_text.strip()}' → Turn {_current_turn} (Debut)")
        else:
            month_offset = next((v for k, v in MONTH_OFFSETS.items() if k in words), None)
            if year_offset is not None and month_offset is not None:
                half = 1 if "late" in pt else 0
                _current_turn = year_offset + month_offset + half + 1
                log(f"Period: '{period_text.strip()}' → Turn {_current_turn}")
            else:
                log(f"Period OCR: '{period_text.strip()}' — could not parse turn")
    except Exception as e:
        log(f"Period OCR failed: {e}")
    # Read current stat values from the stat bar (y=1240-1360)
    # Layout: Speed | Stamina | Power | Guts | Wit | Skill Pts
    # Blend out diamond dividers between columns to prevent "1" misreads
    try:
        from scripts.ocr_util import ocr_image as _ocr_img
        stat_crop = img.crop((0, 1240, 1080, 1360))
        import numpy as np
        arr = np.array(stat_crop)
        # Blend out diamond separators between columns (horizontal avg, 5px wide)
        for dx in (208, 378, 546, 715):
            left = arr[:, max(0, dx - 4):max(0, dx - 3), :].mean(axis=1, keepdims=True)
            right = arr[:, min(arr.shape[1]-1, dx + 3):min(arr.shape[1], dx + 4), :].mean(axis=1, keepdims=True)
            blend = ((left + right) / 2).astype(np.uint8)
            arr[:, dx - 2:dx + 3, :] = blend
        # White-out grade badges at the start of each stat column (value + denom rows)
        # Badge is ~40px wide; fill with white to prevent OCR merging with numbers
        grade_badge_regions = [
            (38, 90),    # speed: badge at x=38-88
            (222, 264),  # stamina: badge at x=222-262
            (397, 443),  # power: badge at x=397-442
            (572, 600),  # guts: badge at x=572-598
            (734, 768),  # wit: badge at x=734-766
        ]
        for gx_start, gx_end in grade_badge_regions:
            arr[45:, gx_start:gx_end, :] = 255  # White-out below the header row
        from PIL import Image as _Img
        stat_crop = _Img.fromarray(arr)
        stat_crop.save("/tmp/stat_bar_crop.png")
        stat_cols = [
            (0.0, 0.20, "speed"),
            (0.20, 0.37, "stamina"),
            (0.37, 0.53, "power"),
            (0.53, 0.68, "guts"),
            (0.68, 0.83, "wit"),
            (0.83, 1.0, "skill_pts"),
        ]
        for text, conf, bbox in _ocr_img("/tmp/stat_bar_crop.png"):
            if conf < 0.3:
                continue
            t = text.strip().replace(":", "").replace("|", "").replace("-", "").replace("—", "").replace("\\", "")
            if t.startswith("/"):
                continue
            digits = re.findall(r'\d+', t)
            if not digits:
                continue
            t = max(digits, key=len)
            val = int(t)
            cx = bbox[0] + bbox[2] / 2
            matched_stat = None
            for x_min, x_max, stat_name in stat_cols:
                if x_min <= cx < x_max:
                    matched_stat = stat_name
                    break
            if matched_stat is None:
                continue
            if matched_stat == "skill_pts":
                if val > 9999:
                    continue
                _skill_pts = val
            else:
                if val >= 1200:
                    continue  # Skip "/1200" denominator labels
                if val < 50:
                    continue
                setattr(_current_stats, stat_name, val)
        log(f"Stats: Spd={_current_stats.speed} Sta={_current_stats.stamina} Pow={_current_stats.power} Gut={_current_stats.guts} Wit={_current_stats.wit} SP={_skill_pts}")
        # Detect suspicious stat jumps and save upscaled screenshot for debugging
        global _prev_stats
        if _prev_stats is not None:
            JUMP_THRESHOLD = 80
            for sname in ("speed", "stamina", "power", "guts", "wit"):
                prev_val = getattr(_prev_stats, sname)
                curr_val = getattr(_current_stats, sname)
                if prev_val > 0 and curr_val > 0 and abs(curr_val - prev_val) > JUMP_THRESHOLD:
                    log(f"⚠ Suspicious OCR: {sname} jumped {prev_val}→{curr_val} (Δ{curr_val - prev_val})")
                    try:
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        ocr_debug_dir = Path("screenshots/ocr_debug")
                        ocr_debug_dir.mkdir(parents=True, exist_ok=True)
                        upscaled = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
                        debug_path = ocr_debug_dir / f"suspicious_{sname}_{prev_val}to{curr_val}_t{_current_turn}_{ts}.png"
                        upscaled.save(debug_path)
                        stat_crop.save(ocr_debug_dir / f"suspicious_{sname}_{prev_val}to{curr_val}_t{_current_turn}_{ts}_statbar.png")
                        log(f"  Saved debug screenshot: {debug_path}")
                    except Exception as e:
                        log(f"  Failed to save debug screenshot: {e}")
                    break  # One save per turn is enough
        _prev_stats = TraineeStats(
            speed=_current_stats.speed, stamina=_current_stats.stamina,
            power=_current_stats.power, guts=_current_stats.guts, wit=_current_stats.wit
        )
    except Exception:
        pass

    # Use cached aptitudes from Full Stats screen, fall back to strategy.yaml
    aptitudes = _cached_aptitudes
    if not aptitudes:
        strategy = _overrides.get_strategy()
        aptitudes = strategy.raw.get("trainee_aptitudes", {})

    # Build support card list from card tracker bonds
    tracked_cards = [
        SupportCard(card_id=cid, bond_level=_card_tracker.get_bond(cid))
        for cid in _card_tracker._bonds
    ]

    state = GameState(
        screen=screen_map.get(screen_type, ScreenState.UNKNOWN),
        stats=_current_stats,
        energy=energy if energy >= 0 else 50,
        current_turn=_current_turn,
        skill_pts=_skill_pts,
        scenario="trackblazer",
        trainee_aptitudes=aptitudes,
        support_cards=tracked_cards,
    )

    # Summer camp flag
    if screen_type == "career_home_summer":
        state.current_turn = max(state.current_turn, 25)  # Ensure scorer knows it's summer

    return state


def is_green(r, g, b):
    return g > 150 and g > r and g > b and (g - r) > 30


def swipe(x1, y1, x2, y2, duration_ms=300, settle=3.0):
    """Raw swipe from (x1,y1) to (x2,y2), then wait for momentum to settle."""
    import random
    jx = random.randint(-10, 10)
    jy = random.randint(-10, 10)
    subprocess.run(
        ["adb", "-s", DEVICE, "shell", "input", "swipe",
         str(x1 + jx), str(y1 + jy), str(x2 + jx), str(y2 + jy), str(duration_ms)],
        capture_output=True, timeout=10,
    )
    time.sleep(settle)


def scroll_down(distance="normal", settle=3.0):
    """Scroll down (drag finger upward). Conservative distance with jitter."""
    if distance == "short":
        swipe(540, 1000, 540, 800, settle=settle)
    else:
        swipe(540, 1350, 540, 750, settle=settle)


def scroll_up(distance="normal", settle=3.0):
    """Scroll up (drag finger downward). Conservative distance with jitter."""
    if distance == "short":
        swipe(540, 750, 540, 1050, settle=settle)
    else:
        swipe(540, 750, 540, 1350, settle=settle)


def press_back():
    """Press hardware back button via ADB."""
    subprocess.run(
        ["adb", "-s", DEVICE, "shell", "input", "keyevent", "BACK"],
        capture_output=True, timeout=10,
    )
    time.sleep(2)


def find_green_button(img, y_range, x_range=(300, 950)):
    """Find center of a green button in the given ranges."""
    green_ys = []
    for y in range(y_range[0], y_range[1], 5):
        green_xs = []
        for x in range(x_range[0], x_range[1], 5):
            r, g, b = px(img, x, y)
            if is_green(r, g, b):
                green_xs.append(x)
        if len(green_xs) >= 3:
            cx = (min(green_xs) + max(green_xs)) // 2
            green_ys.append((y, cx))
    if not green_ys:
        return None
    mid = len(green_ys) // 2
    return (green_ys[mid][1], green_ys[mid][0])


def is_button_active(img, cx, cy, half_w=60, half_h=20, min_ratio=0.35):
    """Check whether a green CTA button is enabled (saturated) vs grayed.

    Disabled buttons share the same shape but are desaturated — the green
    channel still dominates slightly but the (g - r) gap is small. Sample
    a small grid around the button center and require min_ratio of points
    to clear the is_green threshold.
    """
    hits = 0
    samples = 0
    for dy in range(-half_h, half_h + 1, 4):
        for dx in range(-half_w, half_w + 1, 6):
            samples += 1
            r, g, b = px(img, cx + dx, cy + dy)
            if is_green(r, g, b):
                hits += 1
    if samples == 0:
        return False
    return hits / samples >= min_ratio


def _confirm_race_entry():
    """Confirm race entry, dismissing the consecutive-races Warning popup if shown.

    Returns True if a Race button was tapped.
    """
    img2 = screenshot(f"race_confirm_{int(time.time())}")

    # Detect consecutive-races warning popup via OCR. If present, tap its OK
    # button (located mid-screen) then re-screenshot to find the real Race
    # button. Without this check, find_green_button scans the bottom strip and
    # returns a bogus centroid from background bleed-through under the popup.
    try:
        all_text = " ".join(t for t, c, _ in ocr_full_screen(img2) if c > 0.3)
    except Exception:
        all_text = ""
    if "Warning" in all_text and ("consecutive" in all_text or "3 " in all_text):
        # Warning OK button: large green cluster around (778, 1248)
        warn_btn = find_green_button(img2, (1180, 1310), x_range=(500, 1050))
        tap_xy = warn_btn if warn_btn else (778, 1248)
        log(f"Consecutive-races warning detected — tapping OK at {tap_xy}")
        tap(tap_xy[0], tap_xy[1], delay=1.5)
        img2 = screenshot(f"race_confirm_post_warn_{int(time.time())}")

    # Find the actual Race confirm button. Narrow x_range excludes the left
    # "Predictions" button which is also green-tinted.
    race_btn = find_green_button(img2, (1550, 1700), x_range=(440, 780))
    if race_btn:
        log(f"Confirming race at {race_btn}")
        tap(race_btn[0], race_btn[1])
        return True
    log("Race button not found — tapping expected location (540, 1610)")
    tap(540, 1610)
    return False


def detect_screen(img):
    """Detect current screen type using OCR text markers."""
    try:
        results = ocr_full_screen(img)
    except Exception as e:
        log(f"OCR failed in detect_screen: {e}")
        return "unknown"

    # Collect all text into a set for fast lookup
    all_texts = set()
    all_texts_lower = set()
    for text, conf, y_pos in results:
        if conf > 0.3:
            all_texts.add(text)
            all_texts_lower.add(text.lower())

    def has(*keywords):
        """Check if any keyword appears in any OCR text."""
        for kw in keywords:
            kw_l = kw.lower()
            for t in all_texts_lower:
                if kw_l in t:
                    return True
        return False

    # Tutorial / scenario info slides
    # Check for bottom navigation buttons (y > 1700)
    has_back_bottom = False
    has_next_bottom = False
    has_close_bottom = False
    has_help_bottom = False
    for text, conf, y_pos in results:
        if conf < 0.3:
            continue
        t = text.strip()
        if y_pos > 1700:
            if t == "Back":
                has_back_bottom = True
            if t == "Next":
                has_next_bottom = True
            if t == "Close":
                has_close_bottom = True
            if t == "Help":
                has_help_bottom = True
    # Back + Next = tutorial slide (page through)
    if has_back_bottom and has_next_bottom:
        return "tutorial_slide"
    # Back + Close + Help = scenario info overlay (dismiss)
    if has_back_bottom and has_close_bottom:
        return "tutorial_slide"

    # Live race animation — "Photo" at bottom + "Commentary" visible
    has_photo_bottom = False
    for text, conf, y_pos in results:
        if conf >= 0.5 and text.strip() == "Photo" and y_pos > 1800:
            has_photo_bottom = True
    if has_photo_bottom and has("Commentary"):
        return "race_live"

    # Victory concert — "Photo" at bottom, no game UI
    if has_photo_bottom and not has("Training") and not has("Energy") and not has("Back"):
        return "concert"

    # Goal Complete / Goals progress screen — has Next button, tap through
    # "GOAL COMPLETE" screen has both words as large standalone text, not in dialogue
    if any("goal" in t and "complete" in t for t in all_texts_lower):
        return "goal_complete"
    if has("GOAL COMPLETE"):
        return "goal_complete"
    if has("Goals") and has("Result Pts") and has("Next"):
        return "goal_complete"

    # Quick Mode Settings dialog — Cancel + Confirm + radio buttons
    # Triggered accidentally when Full Stats close tap lands on Quick button
    if has("Quick Mode") and has("Confirm") and has("Cancel"):
        return "quick_mode_settings"

    # Continue Career dialog — resume a saved career
    if has("Continue Career") and has("Resume"):
        return "continue_career"

    # Skill purchase confirmation dialog: "Learn the above skills?"
    if has("Confirmation") and has("Learn the above skills"):
        return "skill_confirm_dialog"

    # Skills Learned popup: "Your trainee learned new skills!"
    if has("Skills Learned") and has("Close"):
        return "skills_learned"

    # Race confirm popup: has Cancel + Race + "Enter race?"
    # Must be checked BEFORE Cancel+OK block — background OCR can bleed "OK" through overlay
    if has("Cancel") and has("Race") and has("Enter race"):
        return "race_confirm"

    # Recreation member selection screen: "Choose Recreation Partner" + Cancel, no Friendship Gauge
    if has("Choose Recreation Partner") or (has("Choose") and has("Recreation Partner")):
        return "recreation_member_select"

    # Recreation selection screen: "Recreation" header + "Friendship Gauge" + Cancel, no OK
    if has("Recreation") and has("Friendship Gauge") and has("Cancel") and not has("OK"):
        return "recreation_select"

    # Popup screens (checked first — they overlay other screens)
    if has("Cancel") and has("OK"):
        if has("Rest") and has("recover energy"):
            return "rest_confirm"
        if has("Infirmary") or has("infirmary"):
            return "infirmary_confirm"
        if has("enter this race"):
            return "race_confirm"
        if has("Playback") or has("Songs") or has("Landscape") or has("Portrait"):
            return "concert_confirm"
        if has("Recreation") or has("fun outing"):
            return "recreation_confirm"
        if has("Photo Album") or has("Save image"):
            return "photo_save_popup"
        return "warning_popup"

    # Try Again confirmation: has "alarm clock" text and Cancel/Try Again buttons
    if has("alarm clock") and has("Try Again") and has("Cancel"):
        return "try_again_confirm"

    # Insufficient Result Pts warning — has Cancel + Race buttons
    if has("Insufficient") and has("Result Pts") and has("Race"):
        return "insufficient_pts"
    if has("Cancel") and has("Race") and has("Pts"):
        return "insufficient_pts"

    # Pre-race screen: has "View Results" and "Race" buttons, plus strategy info
    if has("View Results") and has("Strategy"):
        return "pre_race"

    # Post-race standings: has "Try Again" and "Next"
    if has("Try Again") and has("Next"):
        return "post_race_standings"

    # Post-race placement screen (pyramid with Next but no Try Again yet)
    if has("Placing") and has("Next") and has("Fans"):
        return "post_race_placement"

    # TS Climax standings (RANK + Next + "Twinkle Star" or "Climax")
    if has("RANK") and has("Next") and (has("Twinkle") or has("Climax")) and has("Victory Pts"):
        return "ts_climax_standings"

    # Post-race result (animation done, shows WIN/placement, no nav buttons)
    # Check for exact "WIN" token, not substring (avoid "Showing" etc.)
    has_win = any(t.strip().upper() == "WIN" for t in all_texts)
    if has_win and not has("Race List") and not has("Back") and not has("Effects"):
        return "post_race_result"

    # Fan class / post-race reward screen
    if has("Watch Concert"):
        return "fan_class"

    # Event screen — check BEFORE career_home since events overlay the Career screen
    if has("Effects"):
        return "event"
    if has("Trainee Event") or has("Main Scenario Event"):
        return "event"
    if has("Support Card Event") or has("Random Event"):
        return "event"

    # Race list: header says "Race List"
    if has("Race List"):
        return "race_list"

    # Complete Career finish dialog: "Finish this Career playthrough?"
    if has("Finish") and has("Cancel") and has("Remaining Skill Points"):
        return "complete_career_finish"

    # Career Complete: "To Home" + "Edit Team" after all results
    if has("Career Complete") and has("To Home"):
        return "career_complete_done"

    # Career Rank / Sparks / Rewards / Epithet post-career screens with Next
    if has("CAREER") and has("RANK") and has("Next"):
        return "post_career_next"
    if has("SPARKS") and has("Next"):
        return "post_career_next"
    if has("REWARDS") and has("Next"):
        return "post_career_next"
    if has("Epithet") and has("Confirm"):
        return "post_career_confirm"

    # Umamusume Details (post-career summary) with Close
    if has("Umamusume Details") and has("Close"):
        return "post_career_details"

    # Complete Career screen — end of run, can buy skills or finish
    if has("Complete Career") and has("Skills") and has("Skill Pts"):
        return "complete_career"

    # Skill shop (Learn screen) — has skill list with Confirm button
    if has("Learn") and has("Confirm") and has("Skill Points"):
        return "skill_shop"

    # Training Items / Exchange Complete dialogs — recognized only when we
    # find ourselves looking at one without an active flow driving it. The
    # in-flow handlers (`_use_training_items`, `_use_exchange_items`) drive
    # these screens directly, so this classifier only fires when they exit
    # back into the main loop or get interrupted mid-tap.
    if has("Confirm Use") and has("Close"):
        if has("Exchange Complete"):
            return "exchange_complete_idle"
        if has("Training Items"):
            return "training_items_idle"

    # Shop screen
    if has("Shop Coins") or (has("Shop") and has("Cost")):
        return "shop"

    # Training screen: has "Failure" indicator and stat tile labels
    if has("Failure") and has("Back"):
        return "training"

    # Career home: has the action buttons
    if has("Training") and has("Races") and has("Rest"):
        # Check if it's summer camp (Jul/Aug in Classic or Senior year only)
        # Junior Year does NOT have summer camp
        if (has("Jul") or has("Aug")) and (has("Classic") or has("Senior")):
            return "career_home_summer"
        # TS Climax mode — training turn vs race turn
        if has("TS CLIMAX") or has("Climax"):
            return "ts_climax_home"
        return "career_home"

    # Race Day — no Training/Rest buttons, just Race! + Skills
    # TS Climax variant also has Shop
    if has("Race Day") and has("Race!"):
        if has("TS CLIMAX") or has("Climax"):
            return "ts_climax_race"
        return "race_day"

    # Trophy won popup
    if has("TROPHY") and has("Close"):
        return "trophy_won"

    # Race photo screen (post-race photo editor with Save/filters)
    if has("Save") and (has("Original") or has("Monochrome") or has("Sepia") or has("Photo Album")):
        return "race_photo"

    # Race lineup screen (Race! button to start race animation)
    if has("Race!") and has("Fav") and not has("Race List"):
        return "race_lineup"

    # Unlock Requirements popup (tapped locked View Results)
    if has("Unlock Requirements") and has("Close"):
        return "unlock_popup"

    # Shop refresh popup: has "refreshed" and Cancel/Shop buttons
    if has("refreshed") and has("Cancel"):
        return "shop_popup"

    # Inspiration screen: has "GO!" button
    if has("GO!"):
        return "inspiration"

    # Cutscene / animation result: has Skip/Quick but no main nav.
    # Event screens also have "Skip Off" + "Quick" + "Log" — exclude via Log.
    if has("Skip") and has("Quick") and not has("Rest") and not has("Races") and not has("Log"):
        return "cutscene"

    # Dark overlay / TAP prompt — check pixel brightness as fallback
    total_brightness = 0
    for x in range(300, 800, 20):
        r, g, b = px(img, x, 960)
        total_brightness += r + g + b
    if total_brightness < 5000:
        return "tap_prompt"

    # Result Pts popup — white popup over dark background
    if has("Result Pts") and has("Close"):
        return "result_pts_popup"

    # Log screen — "Log" header at top, "Close" button at bottom
    if has("Log") and has("Close"):
        return "log_close"

    return "unknown"


def _detect_tile_hints(frame_bgr) -> dict:
    """Detect pink ! hint badges on training tile buttons.

    Checks each tile button in TRAINING_TILES for the pink/magenta ! badge
    that appears at the bottom-right of the tile circle. Returns a dict
    mapping tile name to bool (has hint).

    Badge color: HSV H=165-178, S>150, V>200 (deep pink, distinct from red
    stat grade letters). Badge offset: ~(+42, -20) from button center.
    """
    import cv2
    import numpy as np
    BADGE_DX = 42
    BADGE_DY = -20
    BADGE_R = 25
    LOWER = np.array([165, 150, 200])
    UPPER = np.array([178, 255, 255])
    THRESHOLD = 100

    hints = {}
    for name, (tx, ty) in TRAINING_TILES.items():
        cx = tx + BADGE_DX
        cy = ty + BADGE_DY
        x1 = max(0, cx - BADGE_R)
        x2 = min(frame_bgr.shape[1], cx + BADGE_R)
        y1 = max(0, cy - BADGE_R)
        y2 = min(frame_bgr.shape[0], cy + BADGE_R)
        roi = frame_bgr[y1:y2, x1:x2]
        if roi.size == 0:
            hints[name] = False
            continue
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, LOWER, UPPER)
        hints[name] = np.count_nonzero(mask) > THRESHOLD
    return hints


# Map playbook source keys to OCR-matchable strings on the recreation screen
_RECREATION_SOURCE_NAMES = {
    "team_sirius": ["team sirius", "sirius"],
    "riko": ["riko", "kashimoto", "riko kashimoto"],
}


def _get_recreation_source() -> str:
    """Get the playbook's desired recreation source for this turn.

    Checks the schedule note for explicit source, falls back to best_source().
    Returns the source key (e.g. 'team_sirius', 'riko').
    """
    if _playbook_engine:
        scheduled = _playbook_engine._get_scheduled_action(_current_turn)
        if scheduled and scheduled.note:
            note_lower = scheduled.note.lower()
            if "sirius" in note_lower:
                return "team_sirius"
            if "riko" in note_lower:
                return "riko"
        return _playbook_engine.rec_tracker.best_source(
            _current_turn, _playbook_engine._scenario
        ) or "team_sirius"
    return "team_sirius"


def _find_recreation_card(img, source_key: str) -> int | None:
    """Find the y coordinate of a recreation card on the selection screen.

    OCRs the screen and looks for the card name matching source_key.
    Skips cards with 'Event Complete' nearby.
    Returns the y coordinate to tap, or None if not found.
    """
    results = ocr_full_screen(img)
    match_names = _RECREATION_SOURCE_NAMES.get(source_key, [source_key])

    # Collect y positions of "Event Complete" badges
    complete_ys = set()
    for text, conf, y_pos in results:
        if conf < 0.3:
            continue
        if "event complete" in text.strip().lower() or "completed" in text.strip().lower():
            complete_ys.add(int(y_pos))

    log(f"  Recreation card OCR ({len(results)} results, looking for {match_names}, complete_ys={complete_ys}):")
    for text, conf, y_pos in results:
        if conf < 0.3:
            continue
        text_lower = text.strip().lower()
        log(f"    y={y_pos:.0f} conf={conf:.2f} '{text.strip()}'")
        for name in match_names:
            if name in text_lower:
                # Skip if an "Event Complete" badge is near this card (within 80px)
                nearby_complete = any(abs(int(y_pos) - cy) < 120 for cy in complete_ys)
                if nearby_complete:
                    log(f"  Skipping '{name}' at y={y_pos:.0f} — Event Complete")
                    continue
                return int(y_pos)
    return None


def _get_recreation_member() -> str:
    """Get the specific Sirius member name for this turn's recreation.

    Handles two note formats:
      "Sirius: Member Name (details)"     — main strategy
      "Sirius recreation (Member Name)"   — fallback strategy
    Returns member name string, or "" if not found.
    """
    if _playbook_engine:
        scheduled = _playbook_engine._get_scheduled_action(_current_turn)
        if scheduled and scheduled.note:
            note = scheduled.note
            if "sirius:" in note.lower():
                after_colon = note.split(":", 1)[1].strip()
                member = after_colon.split("(")[0].strip()
                return member
            if "sirius recreation" in note.lower() and "(" in note:
                member = note.split("(", 1)[1].split(")")[0].strip()
                if " — " in member:
                    member = member.split(" — ")[0].strip()
                return member
    return ""


def _find_member_on_screen(img, member_name: str) -> int | None:
    """Find the y coordinate of a member on the Choose Recreation Partner screen.

    Skips entries with 'Event Completed' nearby.
    Returns the y coordinate to tap, or None if not found.
    """
    if not member_name:
        return None
    results = ocr_full_screen(img)
    target = member_name.lower()

    # Build a set of y positions that have "Event Completed" label
    completed_ys = set()
    for text, conf, y in results:
        if "completed" in text.strip().lower() or "event completed" in text.strip().lower():
            completed_ys.add(int(y))

    def is_completed(y_pos: int) -> bool:
        """Check if a y position is near an Event Completed label."""
        for cy in completed_ys:
            if abs(y_pos - cy) < 60:
                return True
        return False

    # Exact match
    for text, conf, y in results:
        if conf < 0.3:
            continue
        if target in text.strip().lower() or text.strip().lower() in target:
            if not is_completed(int(y)):
                return int(y)

    # Partial match (first word)
    first_word = target.split()[0] if target.split() else ""
    if first_word:
        for text, conf, y in results:
            if conf < 0.3:
                continue
            if first_word in text.strip().lower():
                if not is_completed(int(y)):
                    return int(y)
    return None


def count_portraits(img):
    """Count support card portraits on a training preview screenshot.

    Detects the dark gray friendship gauge bar backgrounds that appear below
    each portrait icon on the right side of the screen (x=940-1060).
    Real gauge bars are uniform neutral gray ~(73-77, 72-77, 73-77).
    Character art can have dark pixels too, so we require neutral gray
    (low channel spread) to avoid false positives from hair/clothing.
    """
    bar_ys = []
    for y in range(350, 980):
        gray_count = 0
        for x in range(940, 1060, 10):
            r, g, b = img.getpixel((x, y))[:3]
            # Must be dark AND neutral gray (channels within 10 of each other)
            is_dark = r < 85 and g < 85 and b < 85
            is_neutral = abs(r - g) < 10 and abs(g - b) < 10 and abs(r - b) < 10
            if is_dark and is_neutral:
                gray_count += 1
        if gray_count >= 6:
            bar_ys.append(y)

    if not bar_ys:
        return 0

    # Cluster consecutive y values (gap > 30 = new portrait)
    clusters = [[bar_ys[0]]]
    for i in range(1, len(bar_ys)):
        if bar_ys[i] - bar_ys[i - 1] > 30:
            clusters.append([])
        clusters[-1].append(bar_ys[i])

    # Only count clusters with 3+ rows (filters out single-row noise)
    return sum(1 for c in clusters if len(c) >= 3)


def get_energy_level(img):
    """Estimate energy percentage from the energy bar fill.

    The bar is a gradient (blue→cyan→green) when filled, gray (117,117,117) when empty.
    Detect filled vs empty by checking if the pixel differs from the gray background.
    """
    BAR_Y = 236
    BAR_X_START = 340
    BAR_X_END = 750
    filled_count = 0
    total = 0
    for x in range(BAR_X_START, BAR_X_END, 5):
        r, g, b = px(img, x, BAR_Y)
        total += 1
        # Gray empty bar is ~(117,117,117). Filled bar has color (blue/cyan/green).
        # Also skip white pixels at the bar edges.
        is_gray = abs(r - g) < 15 and abs(g - b) < 15 and 100 < r < 140
        is_white = r > 240 and g > 240 and b > 240
        if not is_gray and not is_white:
            filled_count += 1
    return int(100 * filled_count / max(total, 1))


def has_green_aptitude_badge(img, card_y_start, card_y_end):
    """Check if a race card has green aptitude badges (B+ aptitude).

    Green aptitude badges are bright green rectangles: G>200, B<130, R>100.
    Scans the right portion of the card where surface/distance badges appear.
    """
    green_count = 0
    for y in range(card_y_start, card_y_end, 4):
        for x in range(700, 1060, 4):
            r, g, b = px(img, x, y)
            if g > 200 and b < 130 and r > 100:
                green_count += 1
    return green_count >= 8


def _ocr_race_list(img):
    """OCR the race list screen and return list of RaceOption objects.

    Each race card has the race name as a stylized plaque on the LEFT (x<330),
    with the track/distance text in the MIDDLE (x=400-800). The plaque text
    usually wraps onto two lines (e.g. "Asahi Hai" / "Futurity Stakes").
    We split by x-coordinate to avoid confusing the two.
    """
    import re
    import tempfile
    from scripts.ocr_util import ocr_image as _ocr_image

    # OCR the full image with bbox so we have x-coordinates
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name)
    raw = _ocr_image(tmp.name)
    try:
        import os
        os.unlink(tmp.name)
    except Exception:
        pass

    img_w, img_h = img.size
    # Convert each result to (text, conf, x, y, w, h) in top-left pixel coords
    items = []
    for text, conf, bbox in raw:
        if conf < 0.3:
            continue
        x = int(bbox[0] * img_w)
        y = int((1.0 - bbox[1] - bbox[3]) * img_h)
        w = int(bbox[2] * img_w)
        h = int(bbox[3] * img_h)
        items.append((text.strip(), conf, x, y, w, h))

    # Race cards are stacked vertically. Each card spans ~135 pixels.
    # Card regions tuned to match the in-game layout (header + banner + 1-3 races).
    races = []
    CARD_REGIONS = [
        {"y_range": (1080, 1245), "tap_y": 1165},
        {"y_range": (1245, 1460), "tap_y": 1355},
        {"y_range": (1460, 1680), "tap_y": 1570},
    ]

    # Common plaque OCR artifacts to strip
    def _clean_plaque(s: str) -> str:
        s = s.strip()
        s = re.sub(r'[=\-–—•·]+$', '', s).strip()
        s = re.sub(r'^[=\-–—•·]+', '', s).strip()
        return s

    for i, region in enumerate(CARD_REGIONS):
        y_min, y_max = region["y_range"]
        card_items = [it for it in items if y_min <= it[3] <= y_max]
        if not card_items:
            continue

        # Split by x: plaque zone (left) vs track zone (right)
        PLAQUE_X_MAX = 360   # plaque text bbox starts at x<330 in samples
        TRACK_X_MIN = 380    # track desc starts at x>400

        plaque_items = sorted(
            [it for it in card_items if it[2] < PLAQUE_X_MAX],
            key=lambda it: it[3],
        )
        track_items = [it for it in card_items if it[2] >= TRACK_X_MIN]

        # Build race name by joining plaque lines top-to-bottom.
        # Drop pure grade badges and digit-only entries from the plaque.
        plaque_parts = []
        for text, conf, x, y, w, h in plaque_items:
            if text in ("G1", "G2", "G3", "OP", "Pre-OP"):
                continue
            if text.isdigit():
                continue
            cleaned = _clean_plaque(text)
            if len(cleaned) >= 3:
                plaque_parts.append(cleaned)
        name = " ".join(plaque_parts).strip()

        # Grade from anywhere in the card
        grade = ""
        for text, conf, x, y, w, h in card_items:
            if text in ("G1", "G2", "G3", "OP", "Pre-OP"):
                grade = text
                break

        # Distance/surface/fans from track zone (right side of card)
        distance = 0
        surface = "turf"
        fan_reward = 0
        for text, conf, x, y, w, h in track_items:
            m = re.search(r'(\d{3,4})m', text)
            if m:
                distance = int(m.group(1))
            tl = text.lower()
            if "dirt" in tl:
                surface = "dirt"
            elif "turf" in tl:
                surface = "turf"
            fm = re.search(r'\+?([\d,]+)\s*fans?', tl)
            if fm:
                fan_reward = int(fm.group(1).replace(",", ""))

        if not name:
            # Fall back to the longest track-zone text so we don't drop the card entirely
            longest = max(
                (it for it in track_items if len(it[0]) > 3 and not it[0].isdigit()),
                key=lambda it: len(it[0]),
                default=None,
            )
            if longest:
                name = longest[0]
            else:
                continue

        # Skip UI elements that aren't real races (no distance detected)
        if distance == 0:
            continue

        # Build a track description for logging/debugging. Concatenate all
        # track-zone text snippets so venue / direction tokens (which may
        # OCR as separate items) are available to the resolver.
        track_desc_main = ""
        for text, conf, x, y, w, h in track_items:
            if re.search(r'\d{3,4}m', text):
                track_desc_main = text
                break
        track_desc_full = " ".join(it[0] for it in track_items).strip()
        track_desc = track_desc_main or track_desc_full

        # Extract direction from the track zone ("Left", "Right", "Line").
        direction_ocr = ""
        tdf_low = track_desc_full.lower()
        if "right" in tdf_low:
            direction_ocr = "right"
        elif "left" in tdf_low:
            direction_ocr = "left"
        elif "line" in tdf_low:
            direction_ocr = "line"

        apt_ok = has_green_aptitude_badge(img, y_min + 20, y_max - 20)

        # Combined plaque + feature resolver — authoritative race identity.
        # Falls back to the OCR'd plaque name if the resolver can't agree
        # with the observed distance/surface.
        ocr_name = name
        banner_id: int | None = None
        resolved_name: str | None = None
        plaque_conf: float = 0.0
        feature_score: float = 0.0
        combined_conf: float = 0.0
        try:
            matcher = _get_plaque_matcher()
            resolved = matcher.resolve_race(
                img,
                region,
                distance=distance,
                surface=surface,
                direction=direction_ocr,
                track_desc=track_desc_full,
            )
            if resolved is not None:
                banner_id = resolved.banner_id
                resolved_name = resolved.race_name
                plaque_conf = resolved.plaque_confidence
                feature_score = resolved.feature_score
                combined_conf = resolved.combined_confidence
                if resolved_name and combined_conf >= 0.60:
                    name = resolved_name
            else:
                # No resolver match; fall back to raw plaque match so we
                # still record a banner_id when it's usable.
                raw = matcher.match_card(img, region)
                if raw is not None:
                    banner_id = raw.banner_id
                    plaque_conf = raw.confidence
                    if raw.race_names:
                        resolved_name = raw.race_names[0]
                        if plaque_conf >= 0.60:
                            name = resolved_name
        except Exception as e:  # pragma: no cover — matcher should never crash a turn
            log(f"  Race {i+1}: plaque matcher error: {e}")

        race = RaceOption(
            name=name,
            grade=grade,
            distance=distance,
            surface=surface,
            fan_reward=fan_reward,
            is_aptitude_ok=apt_ok,
            position=i,
            tap_coords=(540, region["tap_y"]),
            banner_id=banner_id,
        )
        races.append(race)
        plaque_log = ""
        if banner_id is not None:
            plaque_log = (
                f" plaque={banner_id}/{plaque_conf:.2f}"
                f" feat={feature_score:.2f} combined={combined_conf:.2f}"
            )
            if resolved_name and resolved_name != ocr_name:
                plaque_log += f" ocr='{ocr_name}' -> race='{resolved_name}'"
        log(
            f"  Race {i+1}: '{name}' [{track_desc}] grade={grade} "
            f"dist={distance}m {surface} fans={fan_reward} apt_ok={apt_ok}"
            f"{plaque_log}"
        )

    return races


def _use_cleat_for_race(is_ts_climax=False):
    """Use a cleat hammer before a race if available.
    For TS Climax: use Master Cleat.
    For G1: use Artisan first; use Master only if we have >3 Master cleats (reserve 3 for TS Climax).
    """
    inventory = _shop_manager.inventory
    masters = inventory.get("master_hammer", 0)
    artisans = inventory.get("artisan_hammer", 0)

    cleat_key = None
    if is_ts_climax:
        if masters > 0:
            cleat_key = "master_hammer"
    else:
        if artisans > 0:
            cleat_key = "artisan_hammer"
        elif masters > 3:
            cleat_key = "master_hammer"

    if cleat_key:
        cleat_name = "Master Cleat Hammer" if cleat_key == "master_hammer" else "Artisan Cleat Hammer"
        log(f"Using {cleat_name} before race (have {masters} master, {artisans} artisan)")
        _use_training_items([cleat_key])
        if _shop_manager._inventory.get(cleat_key, 0) > 0:
            _shop_manager._inventory[cleat_key] -= 1
            if _shop_manager._inventory[cleat_key] <= 0:
                del _shop_manager._inventory[cleat_key]
        _shop_manager.save_inventory()
        time.sleep(1)


def _read_game_log():
    """Tap Log button on career_home, OCR the log screen, close it.

    Looks for key phrases like Sirius bond unlock event.
    Returns the full OCR text from the log screen.
    """
    global _sirius_bond_unlocked
    from scripts.ocr_util import ocr_image as ocr_full
    import tempfile, os

    tap(BTN_LOG[0], BTN_LOG[1], delay=1.5)

    log_img = screenshot(f"game_log_{int(time.time())}")
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    log_img.save(tmp.name)
    results = ocr_full(tmp.name)
    os.unlink(tmp.name)

    full_text = " ".join(t.strip() for t, c, _ in results if c > 0.25)
    log(f"Game log OCR ({len(full_text)} chars): {full_text[:200]}")

    # Check for Sirius bond unlock event. When the packet capture is fresh
    # the overlay path in build_game_state() already drives this signal off
    # ``state.positive_statuses`` (chara_effect_id 100/101 = Pure Passion);
    # only fall through to game-log scraping when the session is stale, so
    # we don't double-fire on the same career.
    if (
        not _sirius_bond_unlocked
        and not _session_tailer.is_fresh()
        and "shining stars" in full_text.lower()
    ):
        _sirius_bond_unlocked = True
        _SIRIUS_BOND_FILE.touch()
        log("Sirius bond event detected — bond unlocked!")
        _scorer.set_bond_override("team_sirius", 60)
        _scorer.mark_bond_complete("team_sirius")
        _promote_post_sirius_priorities()

    # Close the log — press back
    press_back()
    time.sleep(1.0)

    return full_text


def _detect_active_effects():
    """Detect active item effects by tapping the effect indicator icon.

    Taps the left-side effect icon to open the Active Item Effects popup,
    reads it via OCR, then closes it. Updates _shop_manager._active_effects.
    Returns True if any effects were detected.

    Packet-state shortcut: when the live capture is fresh, the
    Trackblazer ``free_data_set.item_effect_array`` already lists every
    active effect (with item_id and end_turn). ``apply_packet_state``
    rebuilds ``_shop_manager._active_effects`` from it, so the OCR popup
    never has to open. Falls through to the OCR path on stale/missing
    packet or non-Trackblazer scenarios.
    """
    overlay = _packet_overlay_state("career_home")
    if overlay is not None and _shop_manager.apply_packet_state(overlay):
        active = _shop_manager._active_effects
        if active:
            log(f"Active effects from packet: {[(e.item_key, e.turns_remaining) for e in active]}")
        else:
            log("Active effects from packet: none")
        return bool(active)

    from scripts.ocr_util import ocr_image as ocr_full
    import tempfile, os

    # Tap the effect indicator icon (left side, below Result Pts)
    tap(87, 568, delay=2.0)

    popup_img = screenshot(f"active_effects_{int(time.time())}")
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    popup_img.save(tmp.name)
    results = ocr_full(tmp.name)
    os.unlink(tmp.name)

    texts = [(t.strip().lower(), c) for t, c, _ in results if c > 0.25]
    all_text = " ".join(t for t, c in texts)

    # Verify the popup actually opened
    if "active item effects" not in all_text:
        log("Active effects popup did not open — no effects active or icon not present")
        return False

    # Close the popup
    tap(460, 1361, delay=1.5)

    # Parse effects from OCR text
    found_effects = []

    # Megaphone detection
    mega_keywords = {
        "empowering": "empowering_mega",
        "motivating": "motivating_mega",
        "coaching": "coaching_mega",
    }
    for keyword, item_key in mega_keywords.items():
        if keyword in all_text:
            found_effects.append(item_key)

    # Cleat detection
    if "cleat" in all_text:
        found_effects.append("master_cleat")

    # Extract turns remaining
    turns_left = 1
    for t, c in texts:
        if "turn" in t:
            digits = re.findall(r'\d+', t)
            if digits:
                turns_left = int(digits[0])

    # Update _shop_manager._active_effects
    from uma_trainer.decision.shop_manager import ActiveEffect, ITEM_TRAINING_EFFECTS
    for item_key in found_effects:
        already_tracked = any(e.item_key == item_key for e in _shop_manager._active_effects)
        if not already_tracked:
            effect_def = ITEM_TRAINING_EFFECTS.get(item_key, {})
            _shop_manager._active_effects.append(
                ActiveEffect(
                    item_key=item_key,
                    turns_remaining=turns_left + 1,
                    multiplier=effect_def.get("multiplier", 1.0),
                    zero_failure=effect_def.get("zero_failure", False),
                )
            )
            log(f"Detected active effect: {item_key} ({turns_left} turn(s) left, ×{effect_def.get('multiplier', 1.0):.1f})")

    if not found_effects:
        log(f"Active effects popup opened but no recognized effects. OCR: {all_text[:200]}")

    return len(found_effects) > 0


def _use_megaphone_if_needed(is_ts_climax=False):
    """Use a megaphone before training if one isn't already active.

    Reserves: 2 per remaining summer camp + 3 for TS Climax training turns.
    Uses extras on any training turn. Always uses on TS Climax training turns.
    """
    _shop_manager.tick_effects(_current_turn)
    has_mega = any(
        e.item_key in ("empowering_mega", "motivating_mega", "coaching_mega")
        for e in _shop_manager._active_effects
    )
    if has_mega:
        active = next(e for e in _shop_manager._active_effects if "mega" in e.item_key)
        log(f"Megaphone already active: {active.item_key} ({active.turns_remaining} turns left)")
        return

    inventory = _shop_manager.inventory
    empowering = inventory.get("empowering_mega", 0)
    motivating = inventory.get("motivating_mega", 0)
    total_megas = empowering + motivating

    # Calculate reserves needed
    ts_climax_reserve = 3
    summer_reserve = 0
    if _current_turn < 37:
        summer_reserve = 4  # 2 for classic summer + 2 for senior summer
    elif _current_turn < 61:
        summer_reserve = 2  # 2 for senior summer
    reserve = ts_climax_reserve + summer_reserve

    if is_ts_climax:
        # Always use for TS Climax, no reserve check
        pass
    elif total_megas <= reserve:
        log(f"No spare megaphones ({total_megas} held, {reserve} reserved)")
        return

    mega_key = None
    if empowering > 0:
        mega_key = "empowering_mega"
    elif motivating > 0:
        mega_key = "motivating_mega"

    if mega_key:
        context = "TS Climax" if is_ts_climax else "training"
        log(f"Using {mega_key} for {context} (have {empowering}E + {motivating}M, reserve={reserve})")
        _use_training_items([mega_key])
        if _shop_manager._inventory.get(mega_key, 0) > 0:
            _shop_manager._inventory[mega_key] -= 1
            if _shop_manager._inventory[mega_key] <= 0:
                del _shop_manager._inventory[mega_key]
        _shop_manager.save_inventory()
        _shop_manager.activate_item(mega_key)
        time.sleep(1)


def handle_race_list(img):
    """Handle race list screen using RaceSelector."""
    global _last_race_distance
    races = _ocr_race_list(img)
    if not races:
        log("No races detected on list — pressing Back")
        press_back()
        return "race_back"

    # Re-seed playbook target race from the schedule. The target may have been
    # set in a previous process that already ended (run_one_turn stops on turn
    # advance, which can leave us mid-turn on race_list). Since _race_selector
    # is a fresh instance in each process, we need to reconstruct the target
    # from the playbook schedule for the current turn.
    if _playbook_engine and not _race_selector._target_race_name:
        scheduled = _playbook_engine._get_scheduled_action(_current_turn)
        target_name = ""
        if scheduled and scheduled.action == "race":
            target_name = scheduled.race or ""
        elif scheduled and scheduled.pair and scheduled.role == "lead":
            # Pair lead committed to race branch — look up the commitment
            commitment = _playbook_engine._get_commitment(scheduled.pair)
            if commitment and commitment.get("choice") == "race":
                target_name = scheduled.race or ""
        if target_name:
            _race_selector._target_race_name = target_name
            log(f"Re-seeded playbook target from schedule: {target_name}")

    # Race Day forced race — pick first real race (skip header entries)
    # Detect Race Day from OCR: "Race Day" text visible on race list screen
    all_text = " ".join(t for t, c, _ in ocr_full_screen(img) if c > 0.3)
    is_race_day = "Race Day" in all_text or _last_result == "race_day_racing"
    if is_race_day:
        real_races = [r for r in races if r.distance > 0]
        pick = real_races[0] if real_races else races[-1]
        _last_race_distance = pick.distance
        log(f"Race Day — selecting '{pick.name}' at {pick.tap_coords}")
        tap(pick.tap_coords[0], pick.tap_coords[1], delay=1.5)
        _confirm_race_entry()
        return "race_enter"

    # Use game state from career_home (built earlier in the same process).
    if _game_state:
        state = _game_state
    else:
        log("Warning: no cached game state — building from race list (stats may be inaccurate)")
        state = build_game_state(img, "race_list")
    # If we have a playbook target and it's not in the visible races, scroll
    # down to look for it. Some months have 3+ races and only 2 fit on screen.
    _RACE_ABBREVS = {"jcc": "jockey club cup", "nhk": "nhk", "qe": "queen elizabeth"}
    def _race_name_matches(target_lower, race_name_lower):
        if target_lower in race_name_lower or race_name_lower in target_lower:
            return True
        expanded = race_name_lower
        for abbr, full in _RACE_ABBREVS.items():
            if abbr in race_name_lower.split():
                expanded = race_name_lower.replace(abbr, full)
        return target_lower in expanded or expanded in target_lower

    target = _race_selector._target_race_name.lower().strip() if _race_selector._target_race_name else ""
    if target:
        target_found = any(_race_name_matches(target, r.name.lower()) for r in races)
        if not target_found:
            log(f"  Target race '{target}' not visible — scrolling to find it")
            for scroll_attempt in range(4):
                # Gentle scroll — one card height (~180px) at a time
                swipe(540, 1400, 540, 1220, settle=1.5)
                scroll_img = screenshot(f"race_list_scroll_{scroll_attempt}")
                extra = _ocr_race_list(scroll_img)
                for r in extra:
                    if r.name.lower() not in [er.name.lower() for er in races]:
                        races.append(r)
                        log(f"  Race {len(races)} (scroll): '{r.name}' grade={r.grade} dist={r.distance}m")
                # Check if target is now found — use the scrolled-page races
                # since those have valid tap coords for current scroll position
                if any(_race_name_matches(target, r.name.lower()) for r in extra):
                    races = extra
                    log(f"  Found target after scroll — using scrolled race list")
                    break

    state.available_races = races
    action = _race_selector.decide(state)
    log(f"RaceSelector: {action.reason}")

    if action.action_type == ActionType.RACE and action.tap_coords != (0, 0):
        selected = [r for r in races if r.name == action.target]
        _last_race_distance = selected[0].distance if selected else 0
        is_g1 = "(G1," in action.reason or "G1" in action.reason
        if is_g1:
            # Back out to career home to use cleat hammer, then re-enter races
            inventory = _shop_manager.inventory
            has_cleat = inventory.get("artisan_hammer", 0) > 0 or inventory.get("master_hammer", 0) > 3
            if has_cleat:
                press_back()
                time.sleep(1)
                _use_cleat_for_race(is_ts_climax=False)
                tap(*BTN_HOME_RACES)
                time.sleep(2)
                # Re-screenshot and re-select the race
                img = screenshot(f"race_reenter_{int(time.time())}")
        log(f"Selecting race at {action.tap_coords}")
        tap(action.tap_coords[0], action.tap_coords[1], delay=1.5)
        _confirm_race_entry()
        return "race_enter"

    # No good race — go back
    log("No worthwhile race — pressing Back")
    press_back()
    return "race_back"


def _ocr_event_name(img):
    """OCR the event title from the banner area, with fallback to wider scan."""
    _skip = {
        "Main Scenario Event", "Trackblazer",
        "Support Card Event", "Random Event",
        "Trainee Event",
        "T GREAT", "Energy",
    }
    try:
        texts = ocr_region(img, 0, 280, 1080, 420,
                           save_path="/tmp/event_banner.png")
        for text, conf in texts:
            if conf > 0.4 and text not in _skip:
                return text
    except Exception as e:
        log(f"OCR error: {e}")
    # Fallback: scan wider area (y=280-550) for the actual event title
    try:
        texts = ocr_region(img, 0, 280, 1080, 550,
                           save_path="/tmp/event_banner_wide.png")
        for text, conf in texts:
            if conf > 0.4 and text not in _skip:
                return text
    except Exception as e:
        log(f"OCR error (wide): {e}")
    return "unknown"


def _find_effects_button(img):
    """Find the Effects button on event screen.

    It's a small white label in the lower-right area of the event description,
    typically around x=750-820, y=1450-1510.
    Returns (x, y) center if found, else None.
    """
    # Look for small cluster of white pixels on the right side of the description
    for y in range(1420, 1540, 5):
        white_xs = []
        for x in range(700, 900, 3):
            r, g, b = px(img, x, y)
            if r > 230 and g > 230 and b > 230:
                white_xs.append(x)
        if 3 <= len(white_xs) <= 25:
            cx = sum(white_xs) // len(white_xs)
            return (cx, y)
    return None



def _is_victory_event(img):
    """Check if this is a post-race victory event (1st place).

    Victory events have text like "Did I do well?", "Solid Showing",
    "You sure did!", "You can do even better!" etc.
    """
    try:
        from scripts.ocr_util import ocr_full_screen
        all_text = ocr_full_screen(img)
        text_lower = " ".join(t.lower() for t, c, _ in all_text if c > 0.3)
        victory_phrases = [
            "did i do well", "solid showing", "you sure did",
            "you can do even better", "gave it your all",
            "well, you gave it", "let's make them",
        ]
        return any(phrase in text_lower for phrase in victory_phrases)
    except Exception:
        return False


def handle_event(img):
    """Handle event screen using EventHandler with overrides-based decisions."""
    # OCR full event screen text
    event_name = _ocr_event_name(img)
    log(f"Event: '{event_name}'")

    # Tutorial event — find the right button to dismiss
    # But NOT the Trackblazer scenario tutorial ("Would you like an explanation?")
    # which is a real event with Yes/No choices — let it fall through to normal handling.
    if event_name.lower() == "tutorial":
        try:
            all_text = ocr_full_screen(img)
            text_joined = " ".join(t.lower() for t, c, _ in all_text if c > 0.3)
            # Trackblazer tutorial is a regular event — handle normally
            if "would you like an explanation" in text_joined or "no, thank you" in text_joined:
                log("Trackblazer tutorial event — picking 'No, thank you' (choice 2)")
                tap(540, 1340)
                return "event_choice"
            for t, c, y in sorted(all_text, key=lambda r: r[2]):
                tl = t.strip().lower()
                if "all i need to know" in tl:
                    log(f"Tutorial — tapping 'That's all I need to know' at y={y:.0f}")
                    tap(540, int(y))
                    return "tutorial_dismiss"
                if tl in ("yes.", "yes", "yes, please."):
                    log(f"Tutorial — tapping '{t}' at y={y:.0f}")
                    tap(540, int(y))
                    return "tutorial_dismiss"
        except Exception:
            pass
        log("Tutorial — tapping Skip")
        tap(90, 1853)
        return "tutorial_dismiss"

    # Build full event text from OCR for override matching
    try:
        all_text = ocr_full_screen(img)
        full_text = " ".join(t for t, c, _ in all_text if c > 0.3)
    except Exception:
        full_text = event_name
    log(f"Event full text: '{full_text[:200]}'")

    # Riko's "Unexpected Side" event = recreation chain unlocked.
    global _riko_recreation_unlocked
    if not _riko_recreation_unlocked and "unexpected side" in full_text.lower():
        _riko_recreation_unlocked = True
        _RIKO_REC_FILE.touch()
        log("Unexpected Side detected — Riko recreation unlocked!")
        # Treat Riko's bond as maxed so the scorer stops chasing bond on her tiles.
        _scorer.set_bond_override("riko", 80)
        _scorer.mark_bond_complete("riko")

    # If Skip is toggled off, event shows dialogue instead of choices.
    # Re-enable skip so events auto-advance to the choice screen.
    # Check if Skip is toggled off by sampling the button color.
    # Skip ON = green button (avg G > 180, G > R). Skip OFF = grey/white.
    import numpy as np
    skip_crop = img.crop((280, 1855, 440, 1890))
    skip_avg = np.array(skip_crop)[:, :, :3].mean(axis=(0, 1))
    skip_is_green = skip_avg[1] > 180 and skip_avg[1] > skip_avg[0]
    if not skip_is_green:
        log(f"Skip is OFF (button color R={skip_avg[0]:.0f} G={skip_avg[1]:.0f} B={skip_avg[2]:.0f}) — tapping to re-enable")
        tap(380, 1876)
        time.sleep(1)
        return "event"

    # Build event choices by finding choice text positions from OCR.
    # Choice bubbles are typically at y=700-1300, with text length > 10 chars.
    # Exclude short UI labels (Skip, Quick, Log, Effects, Details, etc.)
    _SKIP_WORDS = {"skip", "quick", "log", "effects", "details", "career",
                   "energy", "result", "goal", "turns", "pts", "close"}
    choice_candidates = []
    if 'all_text' in dir():
        for t, c, y in all_text:
            text = t.strip()
            if c < 0.3 or len(text) < 10:
                continue
            if 700 < y < 1350 and text.lower() not in _SKIP_WORDS:
                choice_candidates.append((text, y))
    # Sort by y position and take up to 3 as choices
    choice_candidates.sort(key=lambda x: x[1])
    # Deduplicate candidates that are very close in y (within 50px)
    deduped = []
    for text, y in choice_candidates:
        if not deduped or abs(y - deduped[-1][1]) > 50:
            deduped.append((text, y))
    choice_candidates = deduped[:3]

    if len(choice_candidates) >= 2:
        choices = [
            EventChoice(index=i, text=text, tap_coords=(540, int(y)))
            for i, (text, y) in enumerate(choice_candidates)
        ]
        log(f"  Event choices detected at y={[int(y) for _, y in choice_candidates]}")
    elif len(choice_candidates) == 0:
        # No choices found — could be an event result page or a dialogue cutscene.
        # If Skip/Quick/Log are visible, this is a dialogue — tap dialogue area to advance.
        has_skip = 'all_text' in dir() and any(
            "skip" in t.lower() and "off" not in t.lower()
            for t, c, _ in all_text if c > 0.3)
        if has_skip:
            log("  No event choices — dialogue event, tapping dialogue area to advance")
            tap(540, 1500)
            return "event"
        log("  No event choices found — tapping to dismiss event result")
        tap(540, 960)
        return "event"
    else:
        # Fallback to hardcoded positions
        choices = [
            EventChoice(index=0, text="choice 1", tap_coords=(540, 1120)),
            EventChoice(index=1, text="choice 2", tap_coords=(540, 1250)),
        ]

    # Build GameState for EventHandler
    energy = get_energy_level(img)
    state = build_game_state(img, "event", energy=energy)
    state.event_text = full_text
    state.event_choices = choices

    # Use EventHandler (Tier 0 overrides → KB → fallback to choice 1)
    action = _event_handler.decide(state)
    log(f"EventHandler: {action.reason} → choice {action.target}")

    if action.tap_coords != (0, 0):
        tap(action.tap_coords[0], action.tap_coords[1])
    else:
        tap(540, 1120)
    return "event"


# Skill purchase priority for End Closer builds.
# Higher number = buy first. Skills not listed get priority 1 (low).
# Hint-discounted skills get +3 bonus.
# Format: partial skill name (case-insensitive) → priority
def _get_skill_priority(name):
    """Look up skill priority from strategy overrides (fuzzy match)."""
    strategy = _overrides.get_strategy()
    if strategy.is_blacklisted(name):
        return 0
    sp = strategy.is_priority_skill(name)
    if sp is not None:
        return sp.priority
    return 0


def _ocr_skill_list(img):
    """OCR the skill shop screen and return list of buyable SkillOption objects.

    Parses the skill list by looking for skill names (above descriptions)
    with costs (standalone 2-3 digit numbers). Skips "Obtained" skills.
    """
    results = ocr_full_screen(img)
    sorted_results = sorted(results, key=lambda r: r[2])  # Sort by y

    # First pass: find skill names, costs, "Obtained" markers, and description text
    # Skills appear as: [Name] at some y, [cost number] nearby, [description lines] below
    # "Obtained" appears right next to already-bought skills
    skills = []
    obtained_ys = set()
    cost_entries = []  # (y, cost)
    hint_ys = set()
    desc_lines = []  # (y, text_lower) — collect ALL description text for unique skill detection
    skip_words = {
        "confirm", "reset", "back", "close", "learn", "skill points",
        "full", "stats", "obtained", "10% off!", "hint lvl 1", "hint lvi 1",
    }
    # Unique/inherited skill markers in description text
    unique_markers = ["proportion", "career wins", "number of career"]

    for text, conf, y_pos in sorted_results:
        if conf < 0.3:
            continue
        t = text.strip()
        tl = t.lower()

        # Collect all description-like text for unique skill detection
        if len(t) > 15 or t[0:1].islower():
            desc_lines.append((int(y_pos), tl))

        if t == "Obtained":
            obtained_ys.add(int(y_pos))
            continue

        if "Hint" in t:
            # Parse hint level from "Hint Lvl N" text
            hlvl = 1
            for word in t.split():
                if word.isdigit():
                    hlvl = int(word)
                    break
            hint_ys.add((int(y_pos), hlvl))
            continue

        # Cost: standalone number 50-500
        if t.isdigit() and 50 <= int(t) <= 500:
            cost_entries.append((int(y_pos), int(t)))
            continue

        # Skip known non-skill-name text
        if tl in skip_words or len(t) <= 2:
            continue
        # Skip description lines (start lowercase, or very long)
        if len(t) > 50:
            continue
        if t[0].islower():
            continue
        # Skip stat numbers
        if t.isdigit():
            continue

    # Second pass: identify skill names by looking for capitalized text
    # that's NOT near an "Obtained" marker
    for text, conf, y_pos in sorted_results:
        if conf < 0.3:
            continue
        t = text.strip()
        tl = t.lower()
        y = int(y_pos)

        if tl in skip_words or len(t) <= 3 or t.isdigit():
            continue
        if t[0].islower() or "OFF" in t or "Hint" in t:
            continue
        # Skip description lines — only match multi-word description phrases
        # to avoid falsely catching skill names like "Corner Adept"
        desc_phrases = [
            "increase velocity", "increase acceleration", "increase performance",
            "decrease performance", "increase passing", "narrow the field",
            "improve running", "control breathing", "kick forward",
            "begin to advance", "positioned around", "slightly increase",
            "slightly decrease", "slightly improve", "slightly narrow",
            "moderately increase", "moderately decrease", "moderately narrow",
            "very slightly", "on the heels", "on a corner",
        ]
        if any(p in tl for p in desc_phrases):
            continue
        # Skip long lines (descriptions tend to be > 30 chars)
        if len(t) > 30:
            continue
        if y < 600:  # Header area
            continue

        # Check if this is near an "Obtained" marker (within 80px)
        is_obtained = any(abs(y - oy) < 80 for oy in obtained_ys)
        if is_obtained:
            continue

        # Check if this is a unique/inherited skill (description has scaling markers)
        is_unique = False
        for dy, dtl in desc_lines:
            if 0 < dy - y < 150:  # Description is below the name
                if any(m in dtl for m in unique_markers):
                    is_unique = True
                    break
        if is_unique:
            continue

        # Find the cost closest to this skill (within 100px below)
        cost = 0
        is_hint = False
        for cy, cv in cost_entries:
            if 0 < cy - y < 120:
                cost = cv
                break
        hlvl = 0
        for hy, hl in hint_ys:
            if abs(hy - y) < 40:
                hlvl = hl
                break

        if cost > 0:
            base_prio = _get_skill_priority(t)
            # Hint multiplier: lvl 1→1.1x, 2→1.2x, 3→1.3x, 4→1.3x, 5→1.4x
            hint_mult = {0: 1.0, 1: 1.1, 2: 1.2, 3: 1.3, 4: 1.35, 5: 1.4}.get(hlvl, 1.0)
            skill = SkillOption(
                name=t,
                cost=cost,
                is_hint_skill=hlvl > 0,
                hint_level=hlvl,
                tap_coords=(960, y + 70),  # + button is to the right, slightly below name
                priority=base_prio * hint_mult,
            )
            skills.append(skill)

    return skills


def _read_skill_pts(img):
    """Read current skill points from skill shop header."""
    try:
        results = ocr_full_screen(img)
        for text, conf, y_pos in results:
            if conf < 0.3:
                continue
            t = text.strip()
            # "Skill Points" label is at y~616, the number is near it
            if t.isdigit() and 500 < y_pos < 700:
                val = int(t)
                if val >= 50:
                    return val
    except Exception:
        pass
    return _skill_pts


_skill_shop_done = False


def _scan_all_skills():
    """Scroll through entire skill shop and collect all buyable skills.

    Returns (all_skills, sp) where all_skills is deduplicated by name.
    """
    # Scroll to top first (short swipes, fast settle)
    log("Skill shop — scrolling to top")
    for _ in range(8):
        scroll_up(settle=1.0)
    time.sleep(1)

    all_skills = {}  # name → SkillOption (dedup by name)
    sp = 0

    for page in range(15):  # more pages since we use shorter swipes
        img = screenshot(f"skill_scan_{page}_{int(time.time())}")
        skills = _ocr_skill_list(img)
        page_sp = _read_skill_pts(img)
        if page_sp > 0:
            sp = page_sp

        new_count = 0
        for s in skills:
            if s.name not in all_skills:
                all_skills[s.name] = s
                new_count += 1

        log(f"  Page {page}: {len(skills)} visible, {new_count} new (total: {len(all_skills)})")

        if not skills and page > 0:
            break  # No more skills to find
        if new_count == 0 and page > 0:
            break  # All duplicates — reached end

        scroll_down("short", settle=1.0)

    return list(all_skills.values()), sp


def _normalize_skill_name(name: str) -> str:
    """Lower-cased, alnum-only skill name for fuzzy match across packet/OCR."""
    return "".join(c for c in name.lower() if c.isalnum())


def _buy_skills_from_targets(targets, max_pages: int = 6):
    """Localise a known list of target skills via OCR and tap their + buttons.

    Used by the packet-driven fast path in :func:`handle_skill_shop`. The
    scrolling logic is much shorter than :func:`_scan_all_skills` because we
    already know exactly which skills we're looking for — we stop the moment
    every target is matched, or when two consecutive pages produce no OCR
    results (end of list).

    Returns ``(matched, unmatched)`` lists of the original target objects.
    """
    if not targets:
        return [], []

    # Build a normalised lookup for fuzzy matching against OCR'd names.
    # Each entry maps norm_name -> original BuyableSkill target.
    pending: dict[str, object] = {}
    for t in targets:
        key = _normalize_skill_name(getattr(t, "name", ""))
        if key:
            pending[key] = t

    matched: list = []

    # Scroll to top — packet path's target list is small so we don't need 8.
    log("[skill-shop] scrolling to top for packet-driven buy")
    for _ in range(4):
        scroll_up(settle=0.6)
    time.sleep(0.5)

    empty_streak = 0
    for page in range(max_pages):
        if not pending:
            break
        img = screenshot(f"skill_buy_packet_{page}_{int(time.time())}")
        visible = _ocr_skill_list(img)
        if not visible:
            empty_streak += 1
            if empty_streak >= 2:
                log("[skill-shop] two empty pages in a row — stopping scan")
                break
        else:
            empty_streak = 0

        page_hits = 0
        for skill in visible:
            ocr_norm = _normalize_skill_name(skill.name)
            if not ocr_norm:
                continue
            # Match if either name contains the other (after normalisation),
            # i.e. case-insensitive substring both ways. Mirrors how
            # OverridesLoader.is_priority_skill matches names.
            hit_key = None
            for key in pending:
                if key == ocr_norm or key in ocr_norm or ocr_norm in key:
                    hit_key = key
                    break
            if hit_key is None:
                continue
            target = pending.pop(hit_key)
            log(
                f"[skill-shop] tapping + for '{getattr(target, 'name', '')}' "
                f"at {skill.tap_coords}"
            )
            tap(skill.tap_coords[0], skill.tap_coords[1])
            time.sleep(0.5)
            matched.append(target)
            page_hits += 1
            if not pending:
                break

        log(
            f"[skill-shop] page {page}: visible={len(visible)} matched={page_hits} "
            f"remaining={len(pending)}"
        )
        if not pending:
            break
        scroll_down("short", settle=1.0)

    unmatched = list(pending.values())
    return matched, unmatched


def _confirm_skill_purchase():
    """Tap Confirm + handle the 'Learn the above skills?' dialog.

    Shared between the OCR and packet-driven branches of
    :func:`handle_skill_shop`. Caller is responsible for having tapped the
    individual + buttons first.
    """
    time.sleep(0.5)
    fresh_img = screenshot(f"skill_confirm_{int(time.time())}")
    confirm_btn = find_green_button(fresh_img, (1570, 1640), (100, 500))
    if confirm_btn:
        log(f"[skill-shop] found Confirm at {confirm_btn}")
        tap(confirm_btn[0], confirm_btn[1])
    else:
        log("[skill-shop] Confirm button not found — tapping default coords")
        tap(270, 1600)

    time.sleep(1.5)
    learn_img = screenshot(f"skill_learn_{int(time.time())}")
    learn_screen = detect_screen(learn_img)
    if learn_screen == "skill_confirm_dialog":
        log("[skill-shop] tapping Learn on confirmation dialog")
        tap(810, 1830, delay=2.0)
        for _ in range(5):
            time.sleep(1.5)
            sl_img = screenshot(f"skill_learned_{int(time.time())}")
            sl_screen = detect_screen(sl_img)
            if sl_screen == "skills_learned":
                log("[skill-shop] Skills Learned popup — tapping Close")
                tap(540, 1200)
                break
            elif sl_screen == "skill_confirm_dialog":
                tap(810, 1830)
            else:
                tap(540, 960)


def handle_skill_shop(img, force_recovery=False):
    """Buy skills from the skill shop screen.

    Strategy: scan all pages, sort by priority, buy highest-priority
    skills until we hit SP reserve. At Complete Career, spend everything.
    If force_recovery=True, lower SP reserve and prioritize recovery skills.
    """
    global _skill_shop_done, _recovery_skills_bought

    # Packet-driven fast path: when SessionTailer has a fresh capture and the
    # adapter populated buyable_skills, we already know which skills the game
    # offers — skip the slow 8-swipe + 15-page OCR scan and just localise our
    # planned targets. force_recovery still falls through to the OCR path
    # because it needs visibility into the full SP/cost picture.
    if (
        not force_recovery
        and _session_tailer.is_fresh()
        and getattr(_game_state, "buyable_skills", None)
    ):
        targets = _skill_buyer.decide_from_packet(_game_state)
        log(
            f"[skill-shop] packet plan: {len(targets)} targets, "
            f"budget={getattr(_game_state, 'skill_pts', 0)}"
        )
        for t in targets:
            log(
                f"[skill-shop]   → {getattr(t, 'name', '?')} "
                f"(cost={getattr(t, 'effective_cost', getattr(t, 'base_cost', 0))})"
            )
        if targets:
            matched, unmatched = _buy_skills_from_targets(targets)
            log(
                f"[skill-shop] matched {len(matched)}/{len(targets)}; "
                f"unmatched={[getattr(s, 'name', '?') for s in unmatched]}"
            )
            if matched:
                _confirm_skill_purchase()
                for t in matched:
                    name = getattr(t, "name", "")
                    if name and name.lower() in _RECOVERY_SKILL_NAMES:
                        _recovery_skills_bought += 1
                        log(
                            f"[skill-shop] recovery skill bought: {name} "
                            f"(total: {_recovery_skills_bought})"
                        )
                return "skill_shop"
            log("[skill-shop] no targets matched on screen — falling back to OCR scan")
        else:
            log("[skill-shop] packet plan empty — exiting without OCR scan")
            _skill_shop_done = True
            tap(40, 1830)
            return "skill_back"

    # Phase 1: Scan all pages
    all_skills, sp = _scan_all_skills()

    if not all_skills:
        log(f"Skill shop — no buyable skills found ({sp} SP remaining), exiting")
        _skill_shop_done = True
        tap(40, 1830)
        return "skill_back"

    # Phase 2: Sort by priority (highest first), then by cost (cheapest first for ties)
    # In force_recovery mode, ONLY consider recovery skills — nothing else.
    if force_recovery:
        before = len(all_skills)
        all_skills = [s for s in all_skills if s.name.lower() in _RECOVERY_SKILL_NAMES]
        log(f"  force_recovery: filtered {before} → {len(all_skills)} skills (recovery only)")
        for s in all_skills:
            s.priority = max(s.priority, 20)
    all_skills.sort(key=lambda s: (-s.priority, s.cost))

    is_end_game = _last_result in ("complete_career",)
    strategy = _overrides.get_strategy()
    sp_reserve = 0 if is_end_game else strategy.raw.get("skill_pts_reserve", 800)
    if force_recovery:
        sp_reserve = min(sp_reserve, 400)  # Lower reserve to ensure recovery skills get bought
        log(f"Skill shop — force_recovery mode, SP reserve lowered to {sp_reserve}")

    # Build prereq map and skill lookup from strategy
    prereqs = strategy.raw.get("skill_prereqs", {})
    skill_by_name = {}
    for s in all_skills:
        skill_by_name[s.name] = s
        # Also index by lowercase for fuzzy prereq matching
        skill_by_name[s.name.lower()] = s

    # Decide which skills to buy
    to_buy = []
    to_buy_names = set()
    remaining = sp
    for skill in all_skills:
        if skill.cost <= 0:
            continue
        if skill.priority <= 0:
            continue
        if skill.name in to_buy_names:
            continue
        # Check if this skill has a prereq we also need to buy
        prereq_skill = None
        for target_name, prereq_name in prereqs.items():
            if target_name.lower() in skill.name.lower() or skill.name.lower() in target_name.lower():
                # Find the prereq in available skills
                for s in all_skills:
                    if prereq_name.lower() in s.name.lower() or s.name.lower() in prereq_name.lower():
                        if s.name not in to_buy_names and s.cost > 0:
                            prereq_skill = s
                            break
                break
        total_cost = skill.cost + (prereq_skill.cost if prereq_skill else 0)
        if remaining - total_cost < sp_reserve:
            continue
        if prereq_skill and prereq_skill.name not in to_buy_names:
            to_buy.append(prereq_skill)
            to_buy_names.add(prereq_skill.name)
            remaining -= prereq_skill.cost
        to_buy.append(skill)
        to_buy_names.add(skill.name)
        remaining -= skill.cost

    if not to_buy:
        log(f"Skill shop — no skills worth buying (SP={sp}, reserve={sp_reserve}), exiting")
        for s in all_skills[:5]:
            log(f"  Available: {s.name} (cost={s.cost}, prio={s.priority})")
        _skill_shop_done = True
        tap(40, 1830)
        return "skill_back"

    log(f"Skill shop — buying {len(to_buy)} skills ({sp} SP, reserve {sp_reserve}):")
    for s in to_buy:
        log(f"  → {s.name} (cost={s.cost}, prio={s.priority}, hint={s.is_hint_skill})")

    # Phase 3: Scroll to top, then scroll through with short swipes to tap skills
    for _ in range(8):
        scroll_up(settle=1.0)
    time.sleep(1)

    buy_names = {s.name for s in to_buy}
    bought = set()

    for page in range(20):
        img = screenshot(f"skill_buy_{page}_{int(time.time())}")
        visible = _ocr_skill_list(img)

        for skill in visible:
            if skill.name in buy_names and skill.name not in bought:
                log(f"  Tapping + for: {skill.name} at {skill.tap_coords}")
                tap(skill.tap_coords[0], skill.tap_coords[1])
                time.sleep(0.5)
                bought.add(skill.name)

        if bought == buy_names:
            break  # All found

        scroll_down("short")

    if not bought:
        log("Skill shop — failed to tap any skills, exiting")
        _skill_shop_done = True
        tap(40, 1830)
        return "skill_back"

    # Phase 4+5: Confirm purchase + handle "Learn the above skills?" dialog
    log(f"Confirming {len(bought)} skill purchases")
    _confirm_skill_purchase()

    # Track recovery skills bought
    for name in bought:
        if name.lower() in _RECOVERY_SKILL_NAMES:
            _recovery_skills_bought += 1
            log(f"  Recovery skill bought: {name} (total: {_recovery_skills_bought})")

    return "skill_shop"


# --- Shop handling ---

_SHOP_TURN_FILE = Path("data/last_shop_turn.txt")
try:
    _last_shop_turn = int(_SHOP_TURN_FILE.read_text().strip())
except Exception:
    _last_shop_turn = -1

# Persisted across process restarts so that a race win → shop visit transition
# survives interruptions (e.g. the game's Data Update popup).
_NEEDS_SHOP_FILE = Path("data/needs_shop.flag")
_needs_shop_visit = _NEEDS_SHOP_FILE.exists()
_inventory_checked = False  # Read Training Items on first career_home


def read_inventory_from_training_items():
    """Open Training Items screen, OCR item names and counts, update inventory.

    Packet path: when a fresh capture is available and the response carries
    Trackblazer ``free_data_set.user_item_info_array``, sync inventory from
    that and skip the Training Items screen navigation entirely.
    """
    global _inventory_checked
    from uma_trainer.decision.shop_manager import ITEM_CATALOGUE
    from rapidfuzz import fuzz, process

    overlay = _packet_overlay_state("career_home")
    if overlay is not None and _shop_manager.apply_packet_state(overlay):
        log(f"Inventory from packet: {dict(_shop_manager.inventory)}")
        _shop_manager.save_inventory()
        _inventory_checked = True
        return

    log("Reading inventory from Training Items screen...")
    from scripts.ocr_util import ocr_image as ocr_full

    tap(*BTN_TRAINING_ITEMS, delay=2.5)
    img = screenshot(f"training_items_{int(time.time())}")

    # Verify we're on Training Items screen
    img.save("/tmp/training_items.png")
    results = ocr_full("/tmp/training_items.png")
    texts = [text for text, conf, bbox in results if conf > 0.3]
    if "Training Items" not in " ".join(texts):
        log("Not on Training Items screen — trying race-screen position")
        tap(*BTN_TRAINING_ITEMS_RACE, delay=2.5)
        img = screenshot(f"training_items_retry_{int(time.time())}")
        img.save("/tmp/training_items.png")
        results = ocr_full("/tmp/training_items.png")
        texts = [text for text, conf, bbox in results if conf > 0.3]
        if "Training Items" not in " ".join(texts):
            log("Not on Training Items screen — aborting inventory read")
            _inventory_checked = True  # Don't retry every turn
            press_back()
            return

    # Build name matcher — include stat-prefixed variants
    _STAT_PREFIXES = ("Speed", "Stamina", "Power", "Guts", "Wit")
    _STAT_VARIANT_KEYS = {"notepad", "manual", "scroll"}
    name_to_key = {}
    for key, item in ITEM_CATALOGUE.items():
        name_to_key[item.name] = key
        if key in _STAT_VARIANT_KEYS:
            for prefix in _STAT_PREFIXES:
                name_to_key[f"{prefix} {item.name}"] = key
    catalogue_names = list(name_to_key.keys())

    # Parse items: match only item name lines (skip effect descriptions, UI labels)
    skip_words = {"held", "effect", "training items", "close", "confirm use",
                  "choose how", "training stat", "race stat", "cures", "cure",
                  "shuffles", "increase", "energy", "acquires", "grants"}
    inventory = {}
    use_now = {}

    all_found_keys = set()

    # Effect text → item key fallback (for items whose name scrolled off-screen)
    effect_to_key = {
        "shuffles character appearances": "reset_whistle",
        "rearrange support cards": "reset_whistle",
        "training stat gain +40%": "motivating_mega",
        "training stat gain +60%": "empowering_mega",
        "training stat gain +20%": "coaching_mega",
        "sets training failure rate to 0%": "good_luck_charm",
        "race stat gain +20%": "artisan_hammer",
        "race stat gain +35%": "master_hammer",
        "energy +100": "royal_kale",
        "energy +20": "vita_20",
        "energy +40": "vita_40",
        "energy +65": "vita_65",
        "max energy +4": "energy_drink_max",
        "max energy +8": "energy_drink_max_ex",
        "mood +1": "plain_cupcake",
        "mood +2": "berry_cupcake",
        "cures night owl": "fluffy_pillow",
        "cures skin outbreak": "rich_hand_cream",
        "cures slow metabolism": "smart_scale",
        "cures all bad conditions": "miracle_cure",
        "cures migraine": "aroma_diffuser",
        "cures practice poor": "practice_dvd",
        "cures slacker": "pocket_planner",
        "all bond +5": "grilled_carrots",
    }

    def _scan_page(ocr_results):
        """Extract items from one page of OCR results. Skips already-found items."""
        import re
        page_keys = set()

        # Build list of all OCR entries with pixel y positions
        entries = []
        for text, conf, bbox in ocr_results:
            bx, by, bw, bh = bbox
            # Apple Vision bbox: (x, y_from_bottom, w, h) normalized
            pixel_y = (1.0 - by - bh / 2) * 1920
            entries.append((text, conf, pixel_y))

        # First pass: find item names and their y positions
        matched_items = []  # (pixel_y, item_key, item)
        matched_y_ranges = set()  # track which y-ranges have a name match
        for text, conf, pixel_y in entries:
            if conf < 0.8:
                continue
            lower = text.strip().lower()
            if any(lower.startswith(w) for w in skip_words):
                continue
            if len(lower) < 4:
                continue
            result = process.extractOne(text, catalogue_names, scorer=fuzz.token_sort_ratio, score_cutoff=80)
            if result:
                matched_name, score, _idx = result
                item_key = name_to_key[matched_name]
                item = ITEM_CATALOGUE[item_key]
                if item_key in all_found_keys and not item.use_immediately:
                    continue
                matched_items.append((pixel_y, item_key, item))
                matched_y_ranges.add(int(pixel_y // 200))

        # Fallback: match items by effect text (for names scrolled off-screen)
        # This catches items whose name is above the visible area after scrolling
        for text, conf, pixel_y in entries:
            if conf < 0.8:
                continue
            lower = text.strip().lower()
            for effect_phrase, item_key in effect_to_key.items():
                if effect_phrase in lower:
                    item = ITEM_CATALOGUE[item_key]
                    if item_key in all_found_keys and not item.use_immediately:
                        break
                    # Only suppress if a name-matched item is ABOVE this effect
                    # (i.e. the effect belongs to an already-matched item)
                    already_covered = any(my <= pixel_y and abs(pixel_y - my) < 120
                                          for my, _, _ in matched_items)
                    if already_covered:
                        break
                    matched_items.append((pixel_y, item_key, item))
                    break

        # Second pass: for each matched item, find nearby held count
        for name_y, item_key, item in matched_items:
            held_count = 1  # default
            count_source = "default"
            count_raw = ""
            for text, conf, py in entries:
                # Look for count near the item name/effect (within 80px)
                if abs(py - name_y) > 80:
                    continue
                # "N > N" — OCR misreads ">" as \, $, •, etc.
                m = re.search(r'(\d+)\s*[>»\\$•·|/~:×x]\s*(\d+)', text)
                if m:
                    held_count = int(m.group(1))
                    count_source = "N>N"
                    count_raw = text.strip()
                    break
                # "N >" (OCR split the second number into a separate entry)
                m2 = re.search(r'^(\d+)\s*[>»\\$•·|/~:×x]', text.strip())
                if m2 and int(m2.group(1)) > 0:
                    held_count = int(m2.group(1))
                    count_source = "N>"
                    count_raw = text.strip()
                    break
                # "• N" pattern
                m3 = re.match(r'[•·]\s*(\d+)', text.strip())
                if m3:
                    held_count = int(m3.group(1))
                    count_source = "dot-N"
                    count_raw = text.strip()
                    break

            log(f"  {item_key}: held={held_count} (src={count_source}, raw='{count_raw}')")
            page_keys.add(item_key)
            if item.use_immediately:
                use_now[item_key] = use_now.get(item_key, 0) + held_count
            else:
                inventory[item_key] = inventory.get(item_key, 0) + held_count
        return page_keys

    # Scan first page
    page_keys = _scan_page(results)
    all_found_keys |= page_keys

    # Scroll down and scan additional pages until no new items found
    # Use short scrolls so item names don't scroll off the top of the viewport
    for page in range(6):
        scroll_down("short")
        img = screenshot(f"training_items_p{page+2}_{int(time.time())}")
        img.save("/tmp/training_items.png")
        page_results = ocr_full("/tmp/training_items.png")
        new_keys = _scan_page(page_results)
        if not new_keys:
            break
        all_found_keys |= new_keys

    # Reset and set inventory (include use_immediately items so they can be consumed).
    # SAFETY: if OCR found nothing, the read likely failed (wrong screen, OCR glitch).
    # Don't wipe the existing inventory in that case — trust prior state instead.
    if not inventory and not use_now:
        log(f"Inventory OCR returned empty — keeping existing inventory: {dict(_shop_manager.inventory)}")
    else:
        _shop_manager._inventory.clear()
        for key, count in inventory.items():
            _shop_manager._inventory[key] = count
        for key, count in use_now.items():
            _shop_manager._inventory[key] = _shop_manager._inventory.get(key, 0) + count
        _shop_manager.save_inventory()
        log(f"Inventory from Training Items: {dict(_shop_manager.inventory)}")

    _inventory_checked = True

    # Use any use-immediately items (scrolls, manuals) sitting in inventory.
    # Carrots: only use now if Team Sirius bond < 60 or turn >= 36.
    if "grilled_carrots" in use_now:
        sirius_bond = _card_tracker.get_bond("team_sirius") if _card_tracker.is_tracked("team_sirius") else -1
        bond_met = sirius_bond >= 60 or _sirius_bond_unlocked
        if bond_met and _current_turn < 36:
            log(f"Saving carrots for summer camp (Sirius bond={sirius_bond}%, unlocked={_sirius_bond_unlocked}, turn={_current_turn})")
            del use_now["grilled_carrots"]

    if use_now:
        log(f"Use-immediately items found: {use_now}")
        use_keys = []
        for key, count in use_now.items():
            use_keys.extend([key] * count)
        tap(302, 1772, delay=1.5)  # Close Training Items first
        _use_training_items(use_keys)
    else:
        tap(302, 1772, delay=1.5)  # Tap Close (left button)


def _build_shop_plan():
    """Compute shop tier overrides, ankle stock limits, and want list.

    Returns (tier_overrides, ankle_stock_overrides, want_list) where want_list is
    a list of (tier, cost, key) tuples sorted by priority (best first). Pure — no
    taps, no screen interaction.
    """
    from uma_trainer.decision.shop_manager import ITEM_CATALOGUE, ItemTier

    inventory = _shop_manager.inventory
    tier_order = {ItemTier.SS: 0, ItemTier.S: 1, ItemTier.A: 2, ItemTier.B: 3}

    # Dynamic tier overrides based on game state
    tier_overrides = {}
    friendship_deadline = 36
    all_maxed = _card_tracker.all_bonds_maxed() if _card_tracker.card_count > 0 else False
    if _current_turn < friendship_deadline:
        tier_overrides["grilled_carrots"] = ItemTier.SS
    elif not all_maxed:
        tier_overrides["grilled_carrots"] = ItemTier.B  # Still useful for unbonded cards
    else:
        tier_overrides["grilled_carrots"] = ItemTier.NEVER  # All bonds maxed, no point

    # Runspec shop_item_tiers: build build-specific priorities. Runspec tiers
    # override catalogue defaults but are themselves overridden by any dynamic
    # turn-based tier_overrides already set above.
    if _runspec and _runspec.shop_item_tiers:
        _TIER_MAP = {
            "SS": ItemTier.SS, "S": ItemTier.S, "A": ItemTier.A,
            "B": ItemTier.B, "NEVER": ItemTier.NEVER,
        }
        for key, tier_str in _runspec.shop_item_tiers.items():
            if key in tier_overrides:
                continue
            tier = _TIER_MAP.get(tier_str.upper())
            if tier is not None:
                tier_overrides[key] = tier

    # Dynamic ankle weight max_stock based on deck composition from runspec.
    ANKLE_BUDGET = 4
    ankle_stock_overrides = {}
    deck = _runspec.deck if _runspec else {}
    if deck:
        ANKLE_STATS = ("speed", "stamina", "power", "guts")
        ankle_cards = {s: deck.get(s, 0) for s in ANKLE_STATS}
        total_ankle_cards = sum(ankle_cards.values())
        for stat in ANKLE_STATS:
            ankle_key = f"{stat}_ankle_weights"
            n_cards = ankle_cards[stat]
            if n_cards == 0:
                ankle_stock_overrides[ankle_key] = 1
            else:
                share = max(1, round(ANKLE_BUDGET * n_cards / total_ankle_cards))
                ankle_stock_overrides[ankle_key] = share

    # Late-career megaphone cap based on remaining training turns to cover.
    # Megaphone-worthy training turns after senior summer: flex pairs (66-67),
    # flex turn 71, and TS Climax training turns (~4-5 of 72-78).
    # motivating = 3 turn duration, empowering = 2 turn duration.
    mega_cap = {}
    max_turn = 72
    if _current_turn >= 61:
        remaining_training_turns = max(0, max_turn - _current_turn)
        # Count existing coverage from items already owned
        owned_motiv = inventory.get("motivating_mega", 0)
        owned_empow = inventory.get("empowering_mega", 0)
        coverage = owned_motiv * 3 + owned_empow * 2
        turns_uncovered = max(0, remaining_training_turns - coverage)
        # Only buy more if we have uncovered turns
        mega_cap["motivating_mega"] = owned_motiv + (turns_uncovered // 3)
        mega_cap["empowering_mega"] = owned_empow + (turns_uncovered // 2)

    buyable = []
    for key, item in ITEM_CATALOGUE.items():
        tier = tier_overrides.get(key, item.tier)
        if tier == ItemTier.NEVER:
            continue
        max_stock = mega_cap.get(key, ankle_stock_overrides.get(key, item.max_stock))
        owned = inventory.get(key, 0)
        if owned >= max_stock:
            continue
        if key == "pretty_mirror" and "charming" in _positive_statuses:
            continue
        buyable.append((tier, item.cost, key))
    buyable.sort(key=lambda t: (tier_order.get(t[0], 9), t[1]))
    return tier_overrides, ankle_stock_overrides, buyable


def handle_shop(img, dry=False):
    """Buy priority items from the shop screen, then exit.

    If dry=True, scan the shop but do NOT tap checkboxes or confirm purchases.
    Returns (available_items, would_buy) where available_items is a list of
    dicts {key, name, purchased, cost, tier} and would_buy is the list of
    item keys that the bot would actually purchase within the coin budget.
    """
    from uma_trainer.decision.shop_manager import ITEM_CATALOGUE, ItemTier
    from rapidfuzz import fuzz, process

    # Read coin balance
    coins_text = ocr_region(img, 780, 575, 1060, 665, save_path="/tmp/shop_coins.png")
    coins = None
    for text, conf in coins_text:
        import re
        m = re.search(r"(\d+)", text)
        if m:
            coins = int(m.group(1))
            break
    if coins is None:
        coins = 999  # Assume we have coins if OCR fails
        log("Shop coins: OCR failed, assuming enough to shop")
    else:
        log(f"Shop coins: {coins}")

    if coins < 15:
        log("Not enough coins — exiting shop")
        press_back()
        return "shop_back"

    tier_overrides, ankle_stock_overrides, buyable = _build_shop_plan()
    tier_order = {ItemTier.SS: 0, ItemTier.S: 1, ItemTier.A: 2, ItemTier.B: 3}
    inventory = _shop_manager.inventory
    deck = _runspec.deck if _runspec else {}
    if deck:
        log(f"Deck: {deck} → ankle stock: {ankle_stock_overrides}")
    want_keys = [key for _, _, key in buyable]

    if not want_keys:
        log("Nothing to buy — exiting shop")
        press_back()
        return "shop_back"

    log(f"Want list: {want_keys}")

    # Coins to hold back for unbought SS items that might appear later in the
    # shelf scan. Prevents buying lower-tier items that starve out an SS
    # purchase (e.g. Pretty Mirror @ 150 coins for Charming).
    ss_want = [(cost, key) for tier, cost, key in buyable if tier == ItemTier.SS]

    # Build name matcher — include stat-prefixed variants for items like
    # "Stamina Scroll", "Speed Manual", "Guts Ankle Weights" etc.
    _STAT_PREFIXES = ("Speed", "Stamina", "Power", "Guts", "Wit")
    _STAT_VARIANT_KEYS = {"notepad", "manual", "scroll"}
    name_to_key = {}
    for key, item in ITEM_CATALOGUE.items():
        name_to_key[item.name] = key
        if key in _STAT_VARIANT_KEYS:
            for prefix in _STAT_PREFIXES:
                name_to_key[f"{prefix} {item.name}"] = key
    catalogue_names = list(name_to_key.keys())

    # Scan and select items across scroll pages
    selected_keys = []
    spent = 0
    tapped_positions = []
    dry_available = []  # (dry mode) every matched item seen on shelf
    dry_seen_keys = set()

    for scroll in range(8):
        if scroll > 0:
            scroll_down("short")
            time.sleep(3.0)
            img = screenshot(f"shop_scroll_{scroll}_{int(time.time())}")

        # Scan visible items at y=700, 150px spacing
        y = 700
        while y < 1450:
            texts = ocr_region(img, 130, y, 700, y + 45, save_path="/tmp/shop_item.png")
            name_text = " ".join(t.strip() for t, c in texts if c > 0.3).strip()
            if not name_text or len(name_text) < 3:
                y += 30
                continue

            lower = name_text.lower()
            if any(lower.startswith(w) for w in ("cost", "effect", "choose", "x1", "xl")):
                y += 30
                continue

            result = process.extractOne(name_text, catalogue_names, scorer=fuzz.token_sort_ratio, score_cutoff=65)
            if result is None:
                y += 30
                continue

            matched_name, score, _idx = result
            item_key = name_to_key[matched_name]

            # Check if already purchased (OCR badge detection)
            right_texts = ocr_region(img, 700, y, 1080, y + 120, save_path="/tmp/shop_right.png")
            right_text = " ".join(t.strip().lower() for t, c in right_texts if c > 0.3)
            is_purchased = "purchased" in right_text or "purch" in right_text

            # In dry mode, record every item seen on shelf (even NEVER-tier/purchased)
            if dry and item_key not in dry_seen_keys:
                dry_seen_keys.add(item_key)
                dry_item = ITEM_CATALOGUE[item_key]
                dry_tier = tier_overrides.get(item_key, dry_item.tier)
                dry_available.append({
                    "key": item_key,
                    "name": dry_item.name,
                    "cost": dry_item.cost,
                    "tier": dry_tier.name,
                    "purchased": is_purchased,
                })

            # Skip if matched item is NEVER-tier (after overrides)
            effective_item_tier = tier_overrides.get(item_key, ITEM_CATALOGUE[item_key].tier)
            if effective_item_tier == ItemTier.NEVER:
                y += 150
                continue

            if is_purchased or item_key not in want_keys:
                y += 150
                continue

            # Check stock limit (use dynamic ankle weight limits if available)
            item = ITEM_CATALOGUE[item_key]
            max_stock = ankle_stock_overrides.get(item_key, item.max_stock)
            owned = inventory.get(item_key, 0)
            already_selected = sum(1 for k in selected_keys if k == item_key)
            if owned + already_selected >= max_stock:
                y += 150
                continue

            # Check affordability — reserve coins for higher-tier items
            effective_tier = tier_overrides.get(item_key, item.tier)
            if _current_turn >= 48 or effective_tier in (ItemTier.SS, ItemTier.S):
                coin_reserve = 0
            elif effective_tier == ItemTier.A:
                coin_reserve = 50
            else:
                coin_reserve = 0  # B-tier: no reserve
            # Hold back coins for up to 2 cheapest unbought SS-tier items.
            # (Don't reserve for ALL ss_want — most aren't on this shelf.)
            if effective_tier != ItemTier.SS:
                ss_remaining = sorted(
                    c for c, k in ss_want
                    if k != item_key and k not in selected_keys
                )
                ss_reserve = sum(ss_remaining[:2])
                coin_reserve = max(coin_reserve, ss_reserve)
            if coins is not None and (spent + item.cost + coin_reserve) > coins:
                log(f"  Skip {item.name} ({item.cost}c): spent={spent} + cost={item.cost} + reserve={coin_reserve} > coins={coins}")
                y += 150
                continue

            # Deduplicate (don't tap same position twice across scrolls).
            # Threshold must be < row spacing (150px) so adjacent rows with the
            # same item_key (e.g. Wit Manual + Guts Manual) are both buyable.
            abs_y = y + scroll * 350
            if any(abs(abs_y - py) < 100 and pk == item_key for pk, py in tapped_positions):
                y += 150
                continue

            if dry:
                log(f"  Dry: would select: {item.name} ({item.cost} coins)")
                tapped_positions.append((item_key, abs_y))
                selected_keys.append(item_key)
                spent += item.cost
                y += 150
                continue

            # Select item — checkbox is ~130px below name text, centered at x=915
            log(f"  Selecting: {item.name} ({item.cost} coins) at y={y}")
            tap(915, y + 130, delay=0.5)
            tapped_positions.append((item_key, abs_y))
            selected_keys.append(item_key)
            spent += item.cost
            y += 150

    if dry:
        log(f"Dry shop scan: {len(dry_available)} items on shelf, would buy {len(selected_keys)} ({spent} coins)")
        # Exit without confirming
        for attempt in range(3):
            press_back()
            time.sleep(1.5)
            img2 = screenshot(f"shop_dry_exit_{int(time.time())}")
            if detect_screen(img2) != "shop":
                break
        return dry_available, selected_keys

    if selected_keys:
        log(f"Confirming purchase of {len(selected_keys)} items ({spent} coins): {selected_keys}")
        # Tap Confirm button
        tap(540, 1640, delay=2.0)
        # Tap Exchange button (opens Exchange Complete "Use Now" dialog)
        tap(810, 1780, delay=2.5)

        # Decide which just-purchased items to use on the Exchange Complete screen.
        # - use_immediately items (manual, scroll, carrots, pretty_mirror): always, unless saving carrots
        # - cure items matching active conditions: use immediately
        use_now_keys = [k for k in selected_keys if ITEM_CATALOGUE[k].use_immediately]
        # Carrots before summer camp with bond met: save for later
        save_carrots = False
        if "grilled_carrots" in use_now_keys:
            sirius_bond = _card_tracker.get_bond("team_sirius") if _card_tracker.is_tracked("team_sirius") else -1
            bond_met = sirius_bond >= 60 or _sirius_bond_unlocked
            if bond_met and _current_turn < 36:
                log(f"Saving purchased carrots for summer camp")
                use_now_keys = [k for k in use_now_keys if k != "grilled_carrots"]
                save_carrots = True
        # Cure items matching active conditions
        for cond in list(_active_conditions):
            cure_key = CONDITION_CURES.get(cond)
            if cure_key and cure_key in selected_keys and cure_key not in use_now_keys:
                log(f"Will cure '{cond}' with freshly-bought {cure_key}")
                use_now_keys.append(cure_key)

        # Drive the Exchange Complete "Use Now" screen
        used = _use_shop_exchange_items(use_now_keys)

        # Update inventory: use_immediately items never go in; cure items do unless
        # we just consumed them at Exchange Complete.
        consumed_this_turn = set(use_now_keys) if used else set()
        for key in selected_keys:
            item = ITEM_CATALOGUE[key]
            if item.use_immediately:
                if key == "grilled_carrots" and save_carrots:
                    _shop_manager.add_item(key)
                else:
                    log(f"  {item.name} — used immediately, not added to inventory")
            else:
                if key in consumed_this_turn:
                    log(f"  {item.name} — consumed at Exchange Complete, not added to inventory")
                else:
                    _shop_manager.add_item(key)
        _shop_manager.save_inventory()
        log(f"Inventory updated: {dict(_shop_manager.inventory)}")

        # Clear conditions we just cured via Exchange Complete
        if used:
            cured = set()
            for cond in list(_active_conditions):
                cure_key = CONDITION_CURES.get(cond)
                if cure_key and cure_key in consumed_this_turn:
                    cured.add(cond)
            for c in cured:
                _active_conditions.remove(c)
                log(f"Cleared condition '{c}' (cured at Exchange Complete)")
    else:
        log("No items selected for purchase")

    # Exit shop
    for attempt in range(3):
        press_back()
        time.sleep(2.0)
        img2 = screenshot(f"shop_exit_{int(time.time())}")
        screen2 = detect_screen(img2)
        if screen2 != "shop":
            log(f"Exited shop (attempt {attempt + 1})")
            break
    else:
        log("WARNING: Could not exit shop after 3 attempts")
        return "shop_stuck"

    return "shop_done"


def _use_shop_exchange_items(use_now_keys):
    """Use items on the shop Exchange Complete screen.

    Assumes the Exchange Complete dialog is already open (tap Exchange before
    calling). OCRs visible item rows, taps '+' for each requested item, then
    taps Confirm Use and handles the final confirmation popup.

    Returns True if items were used (Confirm Use tapped), False if we tapped
    Close instead (nothing to use or nothing matched).
    """
    from scripts.ocr_util import ocr_image as ocr_full

    # Close button (left, white) and Confirm Use (right, green, grays out when all 0)
    CLOSE_BTN = (270, 1780)
    CONFIRM_USE_BTN = (810, 1780)

    if not use_now_keys:
        log("Exchange Complete: nothing to use — tapping Close")
        tap(*CLOSE_BTN, delay=2.0)
        return False

    # Matches any just-purchased item that could be use-now from the shop
    keyword_to_key = {
        "manual": "manual",
        "scroll": "scroll",
        "carrots": "grilled_carrots",
        "grilled": "grilled_carrots",
        "pretty mirror": "pretty_mirror",
        "mirror": "pretty_mirror",
        "hand cream": "rich_hand_cream",
        "fluffy": "fluffy_pillow",
        "pillow": "fluffy_pillow",
        "aroma": "aroma_diffuser",
        "pocket planner": "pocket_planner",
        "practice drills": "practice_dvd",
        "smart scale": "smart_scale",
        "miracle": "miracle_cure",
    }

    use_counts = {}
    for k in use_now_keys:
        use_counts[k] = use_counts.get(k, 0) + 1
    log(f"Exchange Complete: want to use {use_counts}")

    img = screenshot(f"shop_exchange_{int(time.time())}")
    img.save("/tmp/shop_exchange.png")
    results = ocr_full("/tmp/shop_exchange.png")

    # Find green "+" button y-positions by scanning for green pixels in the +
    # button column (~x=950-1010). Filter out Confirm Use (y>=1700).
    h = img.size[1]
    green_ys = []
    for y in range(160, 1700, 5):
        green_count = 0
        for x in range(950, 1010, 5):
            r, g, b = img.getpixel((x, y))[:3]
            if g > 150 and g > r + 30 and g > b + 30:
                green_count += 1
        if green_count >= 3:
            green_ys.append(y)
    plus_positions = []
    if green_ys:
        clusters = [[green_ys[0]]]
        for i in range(1, len(green_ys)):
            if green_ys[i] - green_ys[i - 1] > 50:
                clusters.append([])
            clusters[-1].append(green_ys[i])
        plus_positions = [sum(c) // len(c) for c in clusters]
    log(f"  Exchange Complete: {len(plus_positions)} + buttons at y={plus_positions}")

    # Find wanted item names via OCR, convert bbox to pixel y
    all_item_names = []
    for text, conf, bbox in results:
        if conf < 0.7:
            continue
        lower = text.strip().lower()
        for keyword, key in keyword_to_key.items():
            if keyword in lower:
                bx, by, bw, bh = bbox
                name_y = (1.0 - by - bh) * h
                all_item_names.append((name_y, key, text.strip()))
                break
    all_item_names.sort(key=lambda x: x[0])
    deduped = []
    for item in all_item_names:
        if not deduped or abs(item[0] - deduped[-1][0]) > 80:
            deduped.append(item)
    all_item_names = deduped

    # Assign each wanted item to its nearest unused + button
    items_on_screen = []
    used_btns = set()
    for name_y, key, name in all_item_names:
        if key not in use_counts:
            continue
        best_btn = None
        best_dist = 999
        for py in plus_positions:
            if py in used_btns:
                continue
            dist = abs(py - name_y)
            if dist < best_dist:
                best_dist = dist
                best_btn = py
        if best_btn is not None:
            used_btns.add(best_btn)
            items_on_screen.append((best_btn, key, name))

    if not items_on_screen:
        all_texts = [(t.strip(), round(c, 2)) for t, c, b in results if c > 0.5 and len(t.strip()) > 2]
        log(f"  Exchange Complete: no matching items. OCR saw: {all_texts[:15]}")
        tap(*CLOSE_BTN, delay=2.0)
        return False

    log(f"  Matched items: {[(n, k, int(y)) for y, k, n in items_on_screen]}")

    # Tap + for each matched item the required number of times
    used_any = False
    for idx, (btn_y, item_key, display_name) in enumerate(items_on_screen):
        if item_key not in use_counts:
            continue
        remaining = use_counts[item_key]
        future_rows = sum(1 for _, k, _ in items_on_screen[idx + 1:] if k == item_key)
        taps = max(1, remaining - future_rows)
        log(f"  {display_name}: tapping + {taps}x at (975, {btn_y})")
        for _ in range(taps):
            tap(975, btn_y, delay=0.3)
        used_any = True
        use_counts[item_key] -= taps
        if use_counts[item_key] <= 0:
            del use_counts[item_key]

    if not used_any:
        log("  Nothing tapped — tapping Close")
        tap(*CLOSE_BTN, delay=2.0)
        return False

    # Confirm Use (green) → final confirmation popup (Use button, same position
    # as Training Items) → result Close. Re-screenshot first so we can verify
    # the button is actually active — disabled buttons keep the same shape.
    pre_confirm = screenshot(f"shop_exchange_pre_confirm_{int(time.time())}")
    if not is_button_active(pre_confirm, *CONFIRM_USE_BTN):
        log("  Confirm Use is grayed out — nothing landed on the dialog. Tapping Close")
        tap(*CLOSE_BTN, delay=2.0)
        return False
    log("  Tapping Confirm Use")
    tap(*CONFIRM_USE_BTN, delay=2.5)
    # Final confirmation popup mirrors Training Items layout
    tap(*BTN_ITEMS_CONFIRM, delay=3.0)
    # Result screen Close
    tap(*BTN_ITEMS_CLOSE, delay=2.0)
    return True


def _use_training_items(item_keys):
    """Open Training Items, tap '+' for each item in item_keys, then Confirm Use."""
    from scripts.ocr_util import ocr_image as ocr_full

    # Keyword-based matching: game prefixes stat name (e.g. "Guts Manual", "Power Scroll")
    keyword_to_key = {
        "manual": "manual",
        "scroll": "scroll",
        "carrots": "grilled_carrots",
        "grilled": "grilled_carrots",
        "fluffy": "fluffy_pillow",
        "pillow": "fluffy_pillow",
        "hand cream": "rich_hand_cream",
        "miracle": "miracle_cure",
        "practice drills": "practice_dvd",
        "pocket planner": "pocket_planner",
        "smart scale": "smart_scale",
        "aroma": "aroma_diffuser",
        "empowering": "empowering_mega",
        "motivating": "motivating_mega",
        "coaching": "coaching_mega",
        "royal kale": "royal_kale",
        "vita 20": "vita_20",
        "vita 40": "vita_40",
        "vita 65": "vita_65",
        "energy drink max": "energy_drink_max",
        "speed ankle weights": "speed_ankle_weights",
        "stamina ankle weights": "stamina_ankle_weights",
        "power ankle weights": "power_ankle_weights",
        "guts ankle weights": "guts_ankle_weights",
        "artisan": "artisan_hammer",
        "master": "master_hammer",
        "good-luck": "good_luck_charm",
        "good luck": "good_luck_charm",
        "reset whistle": "reset_whistle",
        "plain cupcake": "plain_cupcake",
        "berry cupcake": "berry_cupcake",
    }

    # Count how many of each item to use
    use_counts = {}
    for k in item_keys:
        use_counts[k] = use_counts.get(k, 0) + 1

    log(f"Using training items: {use_counts}")
    tap(*BTN_TRAINING_ITEMS, delay=3.0)  # Open Training Items (extra delay for load)

    # Verify we actually opened the Training Items screen
    verify_img = screenshot(f"use_items_verify_{int(time.time())}")
    verify_img.save("/tmp/use_items_verify.png")
    verify_results = ocr_full("/tmp/use_items_verify.png")
    verify_texts = " ".join(t for t, c, b in verify_results if c > 0.5)
    if "Training Items" not in verify_texts:
        log("Training Items screen did not open — trying race-screen position")
        tap(*BTN_TRAINING_ITEMS_RACE, delay=3.0)
        verify_img = screenshot(f"use_items_verify2_{int(time.time())}")
        verify_img.save("/tmp/use_items_verify.png")
        verify_results = ocr_full("/tmp/use_items_verify.png")
        verify_texts = " ".join(t for t, c, b in verify_results if c > 0.5)
        if "Training Items" not in verify_texts:
            log("Training Items screen still did not open — aborting item use")
            return False

    # Use verify screenshot as first page (already loaded)
    first_page_img = verify_img
    first_page_results = verify_results

    used_any = False
    prev_page_items = None
    for scroll_page in range(4):
        if not use_counts:
            break
        if scroll_page > 0:
            # Training Items list needs a big swipe to scroll past rows.
            # Each item row is ~200px. Use a longer swipe than normal scroll_down.
            swipe(540, 1400, 540, 500, settle=2.0)
            time.sleep(1.0)

        if scroll_page == 0:
            img = first_page_img
            results = first_page_results
            prev_page_items = " ".join(sorted(t.strip() for t, c, _ in results if c > 0.5 and len(t.strip()) > 5))
        else:
            img = screenshot(f"use_items_{int(time.time())}")
            img.save("/tmp/use_items.png")
            results = ocr_full("/tmp/use_items.png")
            # Detect stuck scroll: if OCR text matches previous page, stop
            page_text = " ".join(sorted(t.strip() for t, c, _ in results if c > 0.5 and len(t.strip()) > 5))
            if page_text == prev_page_items:
                log(f"  Page {scroll_page}: same as previous page — scroll not working, stopping")
                break
            prev_page_items = page_text

        # Find green "+" button positions by scanning for green pixels
        h = img.size[1]
        plus_positions = []
        green_ys = []
        for y in range(160, 1600, 5):
            green_count = 0
            for x in range(950, 1010, 5):
                r, g, b = img.getpixel((x, y))[:3]
                if g > 150 and g > r + 30 and g > b + 30:
                    green_count += 1
            if green_count >= 3:
                green_ys.append(y)
        if green_ys:
            clusters = [[green_ys[0]]]
            for i in range(1, len(green_ys)):
                if green_ys[i] - green_ys[i - 1] > 50:
                    clusters.append([])
                clusters[-1].append(green_ys[i])
            plus_positions = [sum(c) // len(c) for c in clusters]

        log(f"  Page {scroll_page}: {len(plus_positions)} green + buttons at y={plus_positions}")

        # Match item names to green + buttons by row order.
        # OCR bbox y-positions are unreliable after scrolling, but the ORDER
        # of items in OCR results matches the visual order on screen. Since
        # green + buttons are also in visual order, the Nth recognized item
        # name corresponds to the Nth green + button.
        #
        # Strategy:
        # 1. Find ALL item names in OCR (not just wanted ones), sorted by y
        # 2. Each item with a green + button maps to buttons in order
        # 3. Items with dimmed buttons (Held = max) have no green button
        #    and must be skipped in the button index
        all_item_names = []
        for text, conf, bbox in results:
            if conf < 0.8:
                continue
            lower = text.strip().lower()
            for keyword, key in keyword_to_key.items():
                if keyword in lower:
                    bx, by, bw, bh = bbox
                    name_y = (1.0 - by - bh) * h
                    all_item_names.append((name_y, key, text.strip()))
                    break
        all_item_names.sort(key=lambda x: x[0])
        # Deduplicate items close in y (within 80px)
        deduped = []
        for item in all_item_names:
            if not deduped or abs(item[0] - deduped[-1][0]) > 80:
                deduped.append(item)
        all_item_names = deduped

        # Now assign buttons to items. Both lists are in top-to-bottom order.
        # Each button corresponds to an item that has an active (green) + button.
        # We walk through items in order and assign the next available button
        # to items that likely have a green button (i.e., are usable).
        #
        # Heuristic: if #items == #buttons, 1:1 mapping. If #items > #buttons,
        # some items have dimmed buttons — we can't know which without more info,
        # so we match wanted items to the nearest button by position.
        items_on_screen = []
        if len(all_item_names) == len(plus_positions):
            # Perfect 1:1 mapping
            for (name_y, key, name), btn_y in zip(all_item_names, plus_positions):
                if key in use_counts:
                    items_on_screen.append((btn_y, key, name))
        else:
            # Imperfect match — use nearest-button for wanted items only
            used_btns = set()
            for name_y, key, name in all_item_names:
                if key not in use_counts:
                    continue
                best_btn = None
                best_dist = 999
                for py in plus_positions:
                    if py in used_btns:
                        continue
                    dist = abs(py - name_y)
                    if dist < best_dist:
                        best_dist = dist
                        best_btn = py
                if best_btn is not None:
                    used_btns.add(best_btn)
                    items_on_screen.append((best_btn, key, name))

        if items_on_screen:
            log(f"  Page {scroll_page}: matched items: {[(n, k, int(y)) for y, k, n in items_on_screen]}")
        else:
            all_texts = [(t.strip(), round(c, 2)) for t, c, b in results if c > 0.5 and len(t.strip()) > 2]
            log(f"  Page {scroll_page}: no matching items. OCR saw: {all_texts[:15]}")

        # Each item in items_on_screen has (button_y, key, name).
        for best_btn, item_key, display_name in items_on_screen:
            if item_key not in use_counts:
                continue
            # Tap "+" for this row — once for most items, multiple for stacked items.
            # For items sharing a key across rows (e.g. 4 manuals), tap once per row.
            # For items stacked in one row (e.g. 2x Grilled Carrots), tap all remaining.
            remaining = use_counts[item_key]
            idx = items_on_screen.index((best_btn, item_key, display_name))
            future_rows = sum(1 for _, k, _ in items_on_screen[idx+1:] if k == item_key)
            taps = max(1, remaining - future_rows)
            log(f"  {display_name}: tapping + {taps}x at (975, {best_btn})")
            for _ in range(taps):
                tap(975, best_btn, delay=0.3)
            used_any = True
            use_counts[item_key] -= taps
            if use_counts[item_key] <= 0:
                del use_counts[item_key]

        if not use_counts:
            break

    if used_any:
        # Confirm Use grays out when no quantity is staged. If our taps
        # never landed (wrong row, dimmed +, scroll race) the count stays
        # at zero — bail to Close instead of pumping a dead button.
        pre_confirm = screenshot(f"use_items_pre_confirm_{int(time.time())}")
        if not is_button_active(pre_confirm, *BTN_ITEMS_CONFIRM):
            log("Confirm Use is grayed out — taps did not register. Tapping Close")
            tap(*BTN_ITEMS_CLOSE, delay=1.5)
            return False
        log("Tapping Confirm Use")
        tap(*BTN_ITEMS_CONFIRM, delay=2.0)
        # Confirmation popup: "Use Training Items" — same position
        tap(*BTN_ITEMS_CONFIRM, delay=3.0)
        # Result screen with "Close"
        tap(*BTN_ITEMS_CLOSE, delay=2.0)
    else:
        log("No items found to use — tapping Close")
        tap(*BTN_ITEMS_CLOSE, delay=1.5)
    return used_any


# Training tile tap positions (x, y) for each stat
TRAINING_TILES = {
    "Speed":   (158, 1520),
    "Stamina": (350, 1580),
    "Power":   (541, 1580),
    "Guts":    (731, 1580),
    "Wit":     (921, 1580),
}



def _ocr_training_gains(img):
    """OCR the stat gain preview numbers from a training screen.

    Returns dict of stat_name -> gain_value for each visible "+N" indicator.
    The gains are large stylized numbers overlaid on the stat panels at the bottom.

    Layout (2 rows x 3 columns):
      Top:    Speed (x<360)  | Stamina (360-720) | Power (x>720)
      Bottom: Guts  (x<360)  | Wit     (360-720) | Skill Pts (x>720)
    """
    from scripts.ocr_util import ocr_image
    from PIL import ImageOps
    # Crop the stat area — gains appear as +N text above stat labels
    # Layout (6 columns): Speed | Stamina | Power | Guts | Wit | Skill Pts
    # Gains (+N) at y~1200, stat labels at y~1244, values at y~1284
    crop = img.crop((0, 1180, 1080, 1280))
    # Invert colors — dramatically improves "+" recognition (1.00 vs 0.50)
    inverted = ImageOps.invert(crop.convert("RGB"))
    inverted.save("/tmp/stat_gains_crop.png")
    raw = ocr_image("/tmp/stat_gains_crop.png")

    # 6-column mapping by x position (normalized 0-1)
    stat_cols = [
        (0.0, 0.20, "Speed"),
        (0.20, 0.37, "Stamina"),
        (0.37, 0.53, "Power"),
        (0.53, 0.68, "Guts"),
        (0.68, 0.83, "Wit"),
        (0.83, 1.0, "Skill Pts"),
    ]

    gains = {}
    for text, conf, bbox in raw:
        if conf < 0.2:
            continue
        t = text.strip()
        # Gains appear in the upper half of the crop (cy_top < 0.60)
        # Stat labels are at cy_top ~0.80+, so 0.60 cleanly separates
        center_y = 1.0 - (bbox[1] + bbox[3] / 2)
        if center_y > 0.60:
            continue
        # Strip "+" prefix and common OCR artifacts
        has_space = " " in t.strip()
        clean = t.replace("+", "").replace("$", "").replace(",", "").replace(" ", "").strip()
        # Extract the leading run of digits — OCR sometimes appends trailing
        # punctuation ("+30-", "+30.") which breaks int() parsing.
        import re as _re
        m = _re.match(r"(\d+)", clean)
        if not m:
            continue
        clean = m.group(1)
        # Low-confidence "4N" is likely "+N" (inversion doesn't always fix it)
        if conf < 0.6 and len(clean) >= 2 and clean[0] == "4":
            clean = clean[1:]
        try:
            val = int(clean)
        except ValueError:
            continue
        # Low-confidence readings with a space (e.g. "+ 70") are usually a
        # single digit where OCR hallucinated an extra character. The second
        # character is almost always "0" in these cases — strip it.
        if conf < 0.6 and has_space and len(clean) >= 2 and clean.endswith("0"):
            val = int(clean[:-1])
        if val > 80 or val < 1:
            continue
        center_x = bbox[0] + bbox[2] / 2
        for x_min, x_max, stat in stat_cols:
            if x_min <= center_x < x_max:
                gains[stat] = val
                break
    return gains



def _ocr_failure_rate(img):
    """Read the failure rate percentage from the training screen.

    The 'Failure N%' bubble floats above the active training tile.
    Returns the integer percentage (0-100), or None if OCR fails to
    find the failure text at all.
    """
    results = ocr_region(img, 50, 1330, 1030, 1480)
    for text, _conf in results:
        t = text.strip().replace(" ", "")
        if "%" in t:
            # Extract just the digits immediately before the % sign
            import re
            match = re.search(r'(\d{1,3})%', t)
            if match:
                val = int(match.group(1))
                if val > 100:
                    val = val % 100  # e.g. 190% → 90%, likely OCR gluing "1" from nearby text
                return val
    return None


# Minimum score that justifies spending an energy drink.
# Below this, the best tile isn't worth the 50+ coin drink.
DRINK_WORTH_IT_SCORE = 25

# Minimum score to accept a summer camp / TS Climax tile without whistling.
# Calibrated with megaphone active: +22 power with 2 cards scores ~53.
WHISTLE_THRESHOLD = 50


def _try_drink_and_retry_training(best_score, scored_tiles):
    """Decide whether to spend an energy drink to make training viable.

    Only drinks if:
      - Best tile's score >= DRINK_WORTH_IT_SCORE (tile is worth training)
      - An energy drink is available in inventory

    If both conditions are met: back out to career_home, use the drink,
    re-enter training, and return True. Otherwise return False.
    """
    global _train_drink_used
    if best_score < DRINK_WORTH_IT_SCORE:
        log(f"Drink skipped — best score {best_score:.1f} < {DRINK_WORTH_IT_SCORE} (not worth it)")
        return False
    # Pick smallest drink that covers the gap (we've already fallen below the
    # failure threshold, so any refill is useful — prefer cheapest vita).
    inventory = _shop_manager.inventory
    candidates = [("vita_20", 20), ("vita_40", 40), ("royal_kale", 50), ("vita_65", 65)]
    chosen = None
    for key, gain in candidates:
        if inventory.get(key, 0) > 0:
            chosen = (key, gain)
            break
    if not chosen:
        log("Drink skipped — no energy drinks in inventory")
        return False
    key, gain = chosen
    top_stat = scored_tiles[0][0].stat_type.value if scored_tiles else "?"
    log(f"Drink worth it — best={top_stat} score={best_score:.1f}, using {key} (+{gain})")
    tap(80, 1855)  # Back to career home
    time.sleep(2)
    if _use_training_items([key]):
        if _shop_manager._inventory.get(key, 0) > 0:
            _shop_manager._inventory[key] -= 1
            if _shop_manager._inventory[key] <= 0:
                del _shop_manager._inventory[key]
        _shop_manager.save_inventory()
        _train_drink_used = True  # prevent re-entry loop
        log(f"  {key} used, re-entering training")
        return True
    log(f"  Failed to use {key}")
    return False


def handle_training():
    """Preview all 5 training tiles and pick the best using uma_trainer scorer."""
    global _pending_training_stat, _pending_training_turn, _summer_whistle_used, _train_drink_used
    global _playbook_force_train

    # Post-ankle re-entry fast path: if we already decided a stat earlier this
    # turn (e.g. scored → backed out → applied ankle weight → came back), skip
    # the preview/score loop entirely and commit the already-chosen tile. This
    # avoids a second wasteful round of tile-preview taps, and makes the on-
    # screen behavior read as "score → apply ankle → train" instead of looking
    # like the bot previews twice with no commit.
    if _pending_training_stat and _pending_training_turn == _current_turn:
        stat = _pending_training_stat
        _pending_training_stat = None
        _pending_training_turn = -1
        tile_key = stat.capitalize()
        if tile_key in TRAINING_TILES:
            tx, ty = TRAINING_TILES[tile_key]
            log(f"Post-item re-entry — committing {stat} directly at ({tx}, {ty})")
            # Verify we're on training screen
            img_check = screenshot(f"train_reenter_{int(time.time())}")
            if detect_screen(img_check) != "training":
                log(f"  Not on training screen — falling through to normal flow")
            else:
                _scenario.on_non_race_action()
                # Tap to raise the tile (first tap selects it)
                tap(tx, ty, delay=1)
                # Read failure rate from the selected tile before confirming
                sel_img = screenshot(f"train_reenter_selected_{int(time.time())}")
                failure_rate = _ocr_failure_rate(sel_img)
                if failure_rate is None:
                    sel_screen = detect_screen(sel_img)
                    if sel_screen != "training":
                        log(f"  Training already confirmed ({sel_screen})")
                        return sel_screen
                    log(f"  Could not read failure rate on re-entry — committing anyway")
                    failure_rate = 0
                if failure_rate > 0:
                    log(f"  Failure rate: {failure_rate}%")
                # Tap again to confirm the training
                tap(tx, ty, delay=1)
                return "training"
        else:
            log(f"Post-item re-entry: unknown stat '{stat}' — falling through to normal flow")

    log("Training — previewing all tiles")

    # Packet-driven preview short-circuit: the home_info.command_info_array
    # carries gains, failure_rate, partners, and bonds for every tile, so
    # we can skip the per-tile preview tap+OCR loop entirely. Opt-in via
    # UMA_PACKET_TRAINING while the path bakes.
    packet_tiles = _build_packet_training_tiles()
    if packet_tiles is not None:
        log(f"  Packet preview: {len(packet_tiles)} tiles (skipped OCR loop)")

    # Check which tile is pre-raised by reading gains before tapping
    # A pre-raised tile will show gains; tapping it again would CONFIRM training
    img_initial = screenshot(f"train_initial_{int(time.time())}")
    if detect_screen(img_initial) != "training":
        return detect_screen(img_initial)
    pre_gains = _ocr_training_gains(img_initial)
    # Identify which tile is pre-raised by checking which stat column has gains
    pre_raised_tile = None
    if pre_gains:
        # The training name banner shows which tile is selected (y~290-340)
        banner_text = ocr_region(img_initial, 0, 280, 540, 350, save_path="/tmp/train_banner.png")
        for t, c in banner_text:
            tl = t.strip().lower()
            for tn in TRAINING_TILES:
                if tn.lower() in tl:
                    pre_raised_tile = tn
                    break
            if pre_raised_tile:
                break
        if pre_raised_tile:
            log(f"  Pre-raised tile: {pre_raised_tile} (gains already visible)")
        else:
            log(f"  Gains visible but can't identify pre-raised tile")

    # Detect hint badges from tile buttons on the initial screenshot
    import numpy as np
    from uma_trainer.perception.pixel_analysis import read_bond_levels
    initial_rgb = np.array(img_initial.convert("RGB"))
    initial_bgr = initial_rgb[:, :, ::-1].copy()
    tile_hints = _detect_tile_hints(initial_bgr)

    last_previewed_tile = None
    img = img_initial
    if packet_tiles is not None:
        # Packet path: tiles already populated from home_info.command_info_array.
        # Overlay hint badges from the screenshot — they're not in the response.
        for tile in packet_tiles:
            tile_key = tile.stat_type.value.capitalize()
            if tile_hints.get(tile_key, False):
                tile.has_hint = True
        tiles = packet_tiles
        for tile in tiles:
            gains_str = ", ".join(f"{k}+{v}" for k, v in sorted(tile.stat_gains.items()))
            hint_str = " HINT" if tile.has_hint else ""
            bond_str = f" bonds={tile.bond_levels}" if tile.bond_levels else ""
            log(
                f"  {tile.stat_type.value:8s}: total={tile.total_stat_gain}, "
                f"cards={len(tile.support_cards)}{bond_str}{hint_str} "
                f"fail={int(tile.failure_rate*100)}% ({gains_str})"
            )
    else:
        # OCR path: preview each tile in turn and read gains.
        tiles = []
        for tile_name, (tx, ty) in TRAINING_TILES.items():
            if tile_name == pre_raised_tile:
                # Already raised — use the initial screenshot, don't tap
                img = img_initial
                gains = pre_gains
            else:
                tap(tx, ty, delay=1)
                last_previewed_tile = tile_name
                img = screenshot(f"train_preview_{tile_name.lower()}_{int(time.time())}")

                # Check if an event fired during preview (events overlay training)
                screen_check = detect_screen(img)
                if screen_check != "training":
                    log(f"  {tile_name}: interrupted by {screen_check} — aborting preview")
                    return screen_check

                gains = _ocr_training_gains(img)

            fail_rate = _ocr_failure_rate(img)
            # Count support card portraits and read bond levels
            n_cards = count_portraits(img)

            # Read bond gauge fill levels for each card on this tile
            frame_rgb = np.array(img.convert("RGB"))
            frame_bgr = frame_rgb[:, :, ::-1].copy()
            bond_levels = read_bond_levels(frame_bgr)
            # Pad/trim to match card count
            if len(bond_levels) < n_cards:
                bond_levels.extend([80] * (n_cards - len(bond_levels)))
            bond_levels = bond_levels[:n_cards]

            # Identify cards via portrait matching and update bond tracker
            card_ids = _card_tracker.identify_cards(frame_bgr, n_cards, bond_levels)

            # Use pre-detected hint from tile buttons
            has_hint = tile_hints.get(tile_name, False)

            stat_type = StatType(tile_name.lower())
            tile = TrainingTile(
                stat_type=stat_type,
                tap_coords=(tx, ty),
                stat_gains={k.lower(): v for k, v in gains.items()},
                support_cards=card_ids,
                bond_levels=bond_levels,
                has_hint=has_hint,
            )
            tiles.append(tile)

            hint_str = " HINT" if has_hint else ""
            bond_str = f" bonds={bond_levels}" if bond_levels else ""
            gains_str = ", ".join(f"{k}+{v}" for k, v in sorted(gains.items()))
            fail_str = f" fail={fail_rate}%" if fail_rate is not None else " fail=?"
            log(f"  {tile_name}: total={tile.total_stat_gain}, cards={n_cards}{bond_str}{hint_str}{fail_str} ({gains_str})")

    # Build GameState and let the scorer decide
    energy = get_energy_level(img)
    state = build_game_state(img, "training", energy=energy)
    state.training_tiles = tiles
    if packet_tiles is not None:
        # Packet path skipped per-tile sprite matching — derive all_bonds_maxed
        # from the bond_levels the adapter parsed off home_info.command_info_array.
        all_levels = [b for tile in tiles for b in tile.bond_levels]
        state.all_bonds_maxed = bool(all_levels) and all(b >= 80 for b in all_levels)
    else:
        state.all_bonds_maxed = _card_tracker.all_bonds_maxed()
        if _card_tracker.card_count > 0:
            log(f"Bond tracker: {_card_tracker.summary()}")

    # Pair commitment: if this is a pair-lead turn that needs tile preview to
    # decide between race+Riko vs train+train, evaluate now and either commit
    # to train (continue normal flow with _playbook_force_train) or back out
    # to career home so the new commitment routes us to the race branch.
    if _playbook_engine:
        pair_choice = _playbook_engine.commit_pair_after_tiles(state)
        if pair_choice == "race":
            log("Pair commitment: tile preview → race branch, backing out to career home")
            tap(80, 1855)  # Back to career home
            return "training_back_to_rest"
        elif pair_choice == "train":
            log(f"Pair commitment: tile preview → train branch (best tile ≥ {_playbook_engine.PAIR_TRAIN_TILE_THRESHOLD})")
            _playbook_force_train = True

    action = _scorer.best_action(state)
    scored_tiles = _scorer.score_tiles(state) if state.training_tiles else []
    best_score = scored_tiles[0][1] if scored_tiles else 0
    log(f"Scorer decision: {action.action_type.value} — {action.reason}")
    for st_tile, st_score in scored_tiles:
        log(f"  {st_tile.stat_type.value:8s}: score={st_score:5.1f}  cards={len(st_tile.support_cards)}  gains={dict(st_tile.stat_gains) if st_tile.stat_gains else {}}")

    # Summer camp / TS Climax: use reset whistle if best score is underwhelming.
    # Calibrated against megaphone-boosted gains: a "good" tile (+25 raw speed,
    # +10 raw power) scores ~80+ with empowering mega. 50 catches bad shuffles
    # while accepting decent tiles (e.g. +22 power with 2 cards scores ~53).
    summer_turns = set(range(37, 41)) | set(range(61, 65))
    is_whistle_turn = _current_turn in summer_turns or _current_turn >= 72
    if (is_whistle_turn
            and best_score < WHISTLE_THRESHOLD
            and not _summer_whistle_used
            and _shop_manager.inventory.get("reset_whistle", 0) > 0):
        phase = "TS CLIMAX" if _current_turn >= 72 else "SUMMER CAMP"
        log(f"{phase} — Best score {best_score:.1f} < {WHISTLE_THRESHOLD}, backing out to use Reset Whistle")
        _summer_whistle_used = True
        tap(80, 1855)  # Back to career home
        time.sleep(2)
        if _use_training_items(["reset_whistle"]):
            if _shop_manager._inventory.get("reset_whistle", 0) > 0:
                _shop_manager._inventory["reset_whistle"] -= 1
                if _shop_manager._inventory["reset_whistle"] <= 0:
                    del _shop_manager._inventory["reset_whistle"]
            _shop_manager.save_inventory()
            log(f"{phase} — Whistle used, re-entering training")
        else:
            log(f"{phase} — Failed to use Reset Whistle, removing from tracked inventory")
            if _shop_manager._inventory.get("reset_whistle", 0) > 0:
                _shop_manager._inventory["reset_whistle"] -= 1
                if _shop_manager._inventory["reset_whistle"] <= 0:
                    del _shop_manager._inventory["reset_whistle"]
            _shop_manager.save_inventory()
        return "training_back_to_rest"  # Re-enters training via summer/TS handler

    # Summer camp: use stat-matched ankle weights before training
    # Follows reset whistle pattern: back out → use item → re-enter training
    if is_whistle_turn and action.action_type != ActionType.REST:
        from uma_trainer.decision.shop_manager import ANKLE_WEIGHT_MAP
        stat_name = action.target.lower() if action.target else ""
        ankle_key = ANKLE_WEIGHT_MAP.get(stat_name)
        active_weights = [e for e in _shop_manager._active_effects if "ankle" in e.item_key]
        if ankle_key and not active_weights and _shop_manager.inventory.get(ankle_key, 0) > 0:
            phase = "TS CLIMAX" if _current_turn >= 72 else "SUMMER CAMP"
            log(f"{phase} — Using {ankle_key} for {stat_name} training (+50% gain)")
            tap(80, 1855)  # Back to career home
            time.sleep(2)
            if _use_training_items([ankle_key]):
                if _shop_manager._inventory.get(ankle_key, 0) > 0:
                    _shop_manager._inventory[ankle_key] -= 1
                    if _shop_manager._inventory[ankle_key] <= 0:
                        del _shop_manager._inventory[ankle_key]
                _shop_manager.save_inventory()
                _shop_manager.activate_item(ankle_key)
                # Remember the chosen stat so re-entry skips preview
                _pending_training_stat = stat_name
                _pending_training_turn = _current_turn
                log(f"{phase} — {ankle_key} active, re-entering to train {stat_name} directly")
            else:
                log(f"{phase} — Failed to use {ankle_key}, removing from tracked inventory")
                if _shop_manager._inventory.get(ankle_key, 0) > 0:
                    _shop_manager._inventory[ankle_key] -= 1
                    if _shop_manager._inventory[ankle_key] <= 0:
                        del _shop_manager._inventory[ankle_key]
                _shop_manager.save_inventory()
            return "training_back_to_rest"  # Re-enters training via summer/TS handler

    if action.action_type == ActionType.REST:
        # Playbook-forced TRAIN turns must not rest. If there's a worthwhile tile,
        # drink and retry; otherwise pick the lowest-failure tile (usually Wit).
        if _playbook_force_train and not _train_drink_used:
            if _try_drink_and_retry_training(best_score, scored_tiles):
                return "training_back_to_rest"
            # Drink attempt may have navigated away from the training screen
            # (Training Items bag, etc.) and failed to return. Verify we're
            # still on training before picking tiles; otherwise just rest.
            post_drink_img = screenshot(f"post_drink_check_{int(time.time())}")
            if detect_screen(post_drink_img) != "training":
                log("Playbook TRAIN: drink failed and training screen lost — resting")
                _scenario.on_non_race_action()
                tap(*BTN_REST)
                return "rest"
            # No drink available or no worthwhile tile — pick lowest-failure tile
            log("Playbook TRAIN: no drink worth it, picking lowest-failure tile")
            wit_tile = next((t for t in tiles if t.stat_type.value == "wit"), None)
            if wit_tile is not None:
                action = BotAction(
                    action_type=ActionType.TRAIN,
                    target="wit",
                    tap_coords=wit_tile.tap_coords,
                    reason="playbook TRAIN: lowest-failure fallback",
                )
            else:
                log("  No Wit tile found — falling back to best-gain tile")
                best = max(tiles, key=lambda t: t.total_stat_gain)
                action = BotAction(
                    action_type=ActionType.TRAIN,
                    target=best.stat_type.value,
                    tap_coords=best.tap_coords,
                    reason="playbook TRAIN: highest-gain fallback",
                )
        else:
            log("Scorer says rest — tapping Back to return to career home")
            _scenario.on_non_race_action()
            tap(80, 1855)
            return "training_back_to_rest"

    # Find the tile the scorer chose and tap it
    _scenario.on_non_race_action()
    if action.tap_coords != (0, 0):
        tx, ty = action.tap_coords
    else:
        # Fallback: pick highest total gain
        best = max(tiles, key=lambda t: t.total_stat_gain)
        tx, ty = best.tap_coords

    # Check if the chosen tile is currently raised (i.e., it was the last tile
    # tapped during preview). If so, tapping it would CONFIRM training — skip the
    # selection tap.  Note: the ORIGINAL pre-raised tile is no longer raised after
    # previewing other tiles, so we check against last_previewed_tile, not pre_raised_tile.
    chosen_tile_name = None
    for t in tiles:
        if t.tap_coords == (tx, ty):
            chosen_tile_name = t.stat_type.value.capitalize()
            break
    already_raised = (chosen_tile_name and chosen_tile_name == last_previewed_tile)
    if already_raised:
        log(f"  Chosen tile {chosen_tile_name} is already raised — reading failure rate without tapping")
    else:
        tap(tx, ty, delay=1)

    # Read failure rate from the SELECTED tile before confirming
    sel_img = screenshot(f"train_selected_{int(time.time())}")
    failure_rate = _ocr_failure_rate(sel_img)
    if failure_rate is None:
        # Verify we're still on the training screen — if not, training already executed
        sel_screen = detect_screen(sel_img)
        if sel_screen != "training":
            log(f"Training already confirmed (screen: {sel_screen}) — skipping failure check")
            return sel_screen
        debug_path = f"/tmp/failure_ocr_fail_{int(time.time())}.png"
        sel_img.save(debug_path)
        log(f"ERROR: Could not read failure rate — screenshot saved to {debug_path}")
        raise RuntimeError(f"Failed to OCR failure rate from training screen (saved {debug_path})")

    if failure_rate > 0:
        log(f"Failure rate: {failure_rate}%")

    summer_camp = _scenario.get_event_turns("summer_camp")
    in_summer = _current_turn in summer_camp
    chosen_stat = chosen_tile_name.lower() if chosen_tile_name else ""
    if in_summer:
        max_failure = 5
    elif chosen_stat == "wit":
        max_failure = 10
    else:
        max_failure = 5
    if failure_rate > max_failure:
        # Summer camp and playbook TRAIN turns: always try drink before resting.
        # These turns are too valuable to waste on rest.
        should_try_drink = (in_summer or _playbook_force_train) and not _train_drink_used
        if should_try_drink:
            log(f"Failure rate {failure_rate}% > {max_failure}% — trying energy drink first")
            if _try_drink_and_retry_training(best_score, scored_tiles):
                return "training_back_to_rest"
            if in_summer:
                log(f"SUMMER CAMP — No drinks left, failure {failure_rate}% too high — backing out to rest")
            else:
                log(f"Playbook TRAIN: no drink — failure {failure_rate}% too high, backing out to rest")
        else:
            log(f"Failure rate {failure_rate}% > {max_failure}% — backing out to rest")
        tap(80, 1855)  # Back to career home
        time.sleep(1)
        tap(*BTN_REST)
        return "rest"

    tap(tx, ty)
    return "training"


def _wait_for_career_home(tag="", accept=("career_home",)):
    """Screenshot + detect in a loop until we're back on career_home. Max 5 attempts.

    `accept` is the set of screens that count as "home" — defaults to just
    career_home, but summer paths pass ("career_home", "career_home_summer").
    """
    for attempt in range(5):
        img = screenshot(f"career_home_wait_{tag}_{int(time.time())}")
        s = detect_screen(img)
        if s in accept:
            return img
        # Try dismissing popups/dialogs
        if s == "warning_popup":
            ok = find_green_button(img, (1150, 1350))
            if ok:
                tap(ok[0], ok[1])
        elif s == "rest_confirm":
            tap(270, 1250)  # Cancel — never confirm unintended rest
        elif s == "recreation_confirm":
            tap(270, 1260)  # Cancel
        elif s == "unknown":
            btn = find_green_button(img, (1780, 1900))
            if btn:
                tap(btn[0], btn[1])
            else:
                tap(540, 960)
        else:
            tap(540, 960)
        time.sleep(1)
    return None


def _do_fast_path_shop(from_summer=False):
    """Visit shop on fast-path turns (race/recreation) so coins don't pile up.

    When `from_summer=True`, we're on career_home_summer and should return there.
    """
    global _needs_shop_visit, _last_shop_turn
    is_pre_debut = _current_turn < 6
    if is_pre_debut:
        return
    should_shop = _needs_shop_visit or _last_shop_turn != _current_turn
    if not should_shop:
        return
    reason = "flagged" if _needs_shop_visit else f"per-turn ({_current_turn})"
    if from_summer:
        reason = "summer-forced"
    log(f"Visiting shop — {reason}")
    _last_shop_turn = _current_turn
    _SHOP_TURN_FILE.write_text(str(_current_turn))
    _needs_shop_visit = False
    try:
        _NEEDS_SHOP_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    accept = ("career_home", "career_home_summer") if from_summer else ("career_home",)
    tap(*BTN_SHOP, delay=2.5)
    img3 = screenshot(f"shop_visit_{int(time.time())}")
    screen3 = detect_screen(img3)
    if screen3 == "shop":
        handle_shop(img3)
        for _ in range(15):
            time.sleep(2)
            img3 = screenshot(f"shop_exit_{int(time.time())}")
            s3 = detect_screen(img3)
            if s3 in accept:
                break
            elif s3 == "shop":
                handle_shop(img3)
            elif s3 in ("warning_popup", "unknown"):
                btn = find_green_button(img3, (1780, 1900))
                if btn:
                    tap(btn[0], btn[1])
                else:
                    tap(540, 960)
        _wait_for_career_home("post_shop_fast", accept=accept)


def _handle_career_home(img):
    """Full career_home handler: gather state → housekeeping → decide → act."""
    global _game_state, _skill_shop_done, _needs_shop_visit, _last_shop_turn
    global _inventory_checked, _summer_whistle_used
    global _playbook_force_train, _train_drink_used
    global _pending_training_stat, _pending_training_turn
    global _last_race_was_g1, _g1_retried_this_race, _backed_out_to_home_this_turn
    global _race_attempted_turn

    # Check Skip button — re-enable if toggled off
    import numpy as np
    skip_crop = img.crop((280, 1855, 440, 1890))
    skip_avg = np.array(skip_crop)[:, :, :3].mean(axis=(0, 1))
    if not (skip_avg[1] > 180 and skip_avg[1] > skip_avg[0]):
        log(f"Skip is OFF on career home (R={skip_avg[0]:.0f} G={skip_avg[1]:.0f} B={skip_avg[2]:.0f}) — tapping to re-enable")
        tap(90, 1876)
        time.sleep(1)
        img = screenshot(f"career_home_skip_fix_{int(time.time())}")

    # =====================================================================
    # PHASE 1: Gather state (stats, aptitudes, conditions, inventory)
    # =====================================================================
    _skill_shop_done = False
    _summer_whistle_used = False
    _playbook_force_train = False
    _train_drink_used = False
    _backed_out_to_home_this_turn = False
    # Clear pending training stat only on a NEW turn — within the same turn
    # we preserve it so the post-ankle re-entry to handle_training finds it.
    if _pending_training_turn != _current_turn:
        _pending_training_stat = None
        _pending_training_turn = -1
    energy = get_energy_level(img)
    build_game_state(img, "career_home", energy=energy)

    is_pre_debut = _current_turn < 12  # Pre-Debut: no shop, no inventory, no effects

    _packet_fresh = _session_tailer is not None and _session_tailer.is_fresh()
    if not is_pre_debut:
        if not _packet_fresh:
            _read_game_log()
            img = _wait_for_career_home("post_log")
            if img is None:
                return "recovering"

            _detect_active_effects()
            img = _wait_for_career_home("post_effects")
            if img is None:
                return "recovering"
            energy = get_energy_level(img)
        else:
            log("[packet-state] Skipping game log + effects OCR (packet fresh)")
    log(f"Energy: ~{energy}% | Turn: {_current_turn} | Consecutive races: {_scenario._consecutive_races}")

    # Read Full Stats (aptitudes + conditions). Packet path populates these
    # globals during build_game_state(); only fall back to OCR when the
    # session is stale or UMA_PACKET_STATE=0.
    if _should_call_fullstats():
        read_fullstats()
        time.sleep(1)
        img = _wait_for_career_home("post_stats")
        if img is None:
            return "recovering"
        energy = get_energy_level(img)
    else:
        log(f"[packet-state] Conditions: {_active_conditions}; Positive: {_positive_statuses}")

    # Build authoritative game state with aptitudes
    _game_state = build_game_state(img, "career_home", energy=energy)

    # Fast-path: if playbook says race or recreation and no conditions to cure,
    # skip slow housekeeping (inventory, shop) but still detect active effects
    # (needed for cleat hammer decisions on race turns).
    if _playbook_engine and not _active_conditions and not is_pre_debut:
        scheduled = _playbook_engine._get_scheduled_action(_current_turn)
        if scheduled and scheduled.action in ("race", "recreation"):
            if not _packet_fresh:
                _detect_active_effects()
                img = _wait_for_career_home("post_effects_fast")
                if img is None:
                    return "recovering"
                energy = get_energy_level(img)
            _game_state = build_game_state(img, "career_home", energy=energy)
            log(f"State gathered: Turn {_current_turn}, Energy {energy}%, "
                f"Stats Spd={_current_stats.speed} Sta={_current_stats.stamina} "
                f"Pow={_current_stats.power} Gut={_current_stats.guts} Wit={_current_stats.wit} "
                f"SP={_skill_pts}")
            skip_cards = {"team_sirius"} if _sirius_bond_unlocked else set()
            _playbook_engine.check_friendship_deadline(_current_turn, skip_cards=skip_cards)
            rec_remaining = _playbook_engine.rec_tracker.uses_remaining
            log(f"Playbook: Recreation remaining={rec_remaining}, total_used={_playbook_engine.rec_tracker.total_used}")
            pb_action = _playbook_engine.decide_turn(_game_state)
            if pb_action.action_type in (ActionType.GO_OUT, ActionType.RACE):
                # Shop visit before fast-path — don't skip buying just because
                # we're racing or recreating
                _do_fast_path_shop()
                if pb_action.action_type == ActionType.GO_OUT:
                    log(f"Playbook: Recreation — {pb_action.reason} (fast-path)")
                    _scenario.on_non_race_action()
                    tap(*BTN_RECREATION)
                    return "recreation"
                else:
                    log(f"Playbook: Racing — {pb_action.reason} (fast-path)")
                    # Force-buy recovery skills before Kikuka Sho even on race turns.
                    if (
                        _current_turn in (43, 44)
                        and _recovery_skills_bought < 2
                        and not _skill_shop_done
                        and _skill_pts >= 400
                    ):
                        log(f"Forcing skill shop for recovery skills before Kikuka Sho (have {_recovery_skills_bought}/2, SP={_skill_pts})")
                        tap(*BTN_HOME_SKILLS)
                        time.sleep(2.5)
                        img_sk = screenshot(f"force_skill_shop_{int(time.time())}")
                        if detect_screen(img_sk) == "skill_shop":
                            handle_skill_shop(img_sk, force_recovery=True)
                            _wait_for_career_home("post_force_skill_shop")
                    scheduled = _playbook_engine._get_scheduled_action(_current_turn)
                    target_name = ""
                    if scheduled:
                        target_name = scheduled.race or ""
                    if target_name:
                        _race_selector._target_race_name = target_name
                        log(f"  Target race: {target_name}")
                    _g1_retried_this_race = False
                    tap(*BTN_HOME_RACES)
                    return "going_to_races"

    # Packet inventory sync every turn (free); OCR fallback every 6 turns
    _packet_fresh = _session_tailer is not None and _session_tailer.is_fresh()
    if not is_pre_debut and (not _inventory_checked or _current_turn % 6 == 0 or _packet_fresh):
        read_inventory_from_training_items()
        time.sleep(1)
        img = _wait_for_career_home("post_inv")
        if img is None:
            return "recovering"
        energy = get_energy_level(img)

    log(f"State gathered: Turn {_current_turn}, Energy {energy}%, "
        f"Stats Spd={_current_stats.speed} Sta={_current_stats.stamina} "
        f"Pow={_current_stats.power} Gut={_current_stats.guts} Wit={_current_stats.wit} "
        f"SP={_skill_pts}")

    # Playbook: check friendship deadlines (skip team_sirius if bond event already fired)
    if _playbook_engine:
        skip_cards = {"team_sirius"} if _sirius_bond_unlocked else set()
        deadline_result = _playbook_engine.check_friendship_deadline(_current_turn, skip_cards=skip_cards)
        if deadline_result == "restart":
            log(f"PLAYBOOK: Friendship deadline missed at turn {_current_turn} — restart recommended")
        elif deadline_result == "warn":
            log(f"PLAYBOOK WARNING: Friendship deadline missed at turn {_current_turn}")
        rec_remaining = _playbook_engine.rec_tracker.uses_remaining
        log(f"Playbook: Recreation remaining={rec_remaining}, total_used={_playbook_engine.rec_tracker.total_used}")

    # =====================================================================
    # PHASE 2: Housekeeping (cure, shop, consumables, mood)
    # =====================================================================

    # Cure conditions (first pass — use items already in inventory)
    if _active_conditions:
        cure_conditions_from_inventory()
        time.sleep(1)
        img = _wait_for_career_home("post_cure")
        if img is None:
            return "recovering"

    # Shop visit — every turn post-debut, at most once per turn
    # Happens BEFORE the post-shop cure pass so newly-purchased cure items can
    # be used on the same turn.
    should_shop = _needs_shop_visit or (
        _current_turn >= 6 and _last_shop_turn != _current_turn
    )
    if should_shop:
        reason = "flagged" if _needs_shop_visit else f"per-turn ({_current_turn})"
        log(f"Visiting shop — {reason}")
        _last_shop_turn = _current_turn
        _SHOP_TURN_FILE.write_text(str(_current_turn))
        _needs_shop_visit = False
        try:
            _NEEDS_SHOP_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        tap(*BTN_SHOP, delay=2.5)
        img3 = screenshot(f"shop_visit_{int(time.time())}")
        screen3 = detect_screen(img3)
        if screen3 == "shop":
            shop_result = handle_shop(img3)
            # Wait for shop to finish and return to career_home
            for _ in range(15):
                time.sleep(2)
                img3 = screenshot(f"shop_exit_{int(time.time())}")
                s3 = detect_screen(img3)
                if s3 == "career_home":
                    break
                elif s3 == "shop":
                    handle_shop(img3)
                elif s3 in ("warning_popup", "unknown"):
                    btn = find_green_button(img3, (1780, 1900))
                    if btn:
                        tap(btn[0], btn[1])
                    else:
                        tap(540, 960)
        img = _wait_for_career_home("post_shop")
        if img is None:
            return "recovering"
        energy = get_energy_level(img)

        # Second cure pass — if the shop visit bought cure items, use them now
        if _active_conditions:
            log(f"Post-shop cure pass for still-active conditions: {_active_conditions}")
            cure_conditions_from_inventory()
            time.sleep(1)
            img = _wait_for_career_home("post_cure2")
            if img is None:
                return "recovering"
            energy = get_energy_level(img)

    # Use consumables (manuals, scrolls, and conditionally carrots)
    # Carrots: use immediately if Team Sirius bond < 60 or unknown.
    # Otherwise save for Classic summer camp (turn 36+) to maximize mirror/Charming chance.
    use_carrots = False
    carrot_count = _shop_manager.inventory.get("grilled_carrots", 0)
    if carrot_count > 0:
        sirius_bond = _card_tracker.get_bond("team_sirius") if _card_tracker.is_tracked("team_sirius") else -1
        bond_met = sirius_bond >= 60 or _sirius_bond_unlocked
        if not bond_met:
            use_carrots = True
        elif _current_turn >= 36:
            use_carrots = True
        else:
            log(f"Saving carrots for summer camp (Sirius bond={sirius_bond}%, turn={_current_turn})")

    use_now_inv = {k: v for k, v in _shop_manager.inventory.items()
                   if k in ("manual", "scroll") and v > 0}
    if use_carrots and carrot_count > 0:
        use_now_inv["grilled_carrots"] = carrot_count
    if use_now_inv:
        log(f"Using consumables from inventory: {use_now_inv}")
        use_keys = []
        for k, count in use_now_inv.items():
            use_keys.extend([k] * count)
        _use_training_items(use_keys)
        for k in use_now_inv:
            _shop_manager._inventory.pop(k, None)
        _shop_manager.save_inventory()
        time.sleep(1)
        img = _wait_for_career_home("post_use")
        if img is None:
            return "recovering"

    # Mood management — use cupcakes during summer camp and TS Climax (Great mood
    # required for training stacking). Outside those phases, normal mood is fine.
    mood = detect_mood(img)
    summer_camp_turns = _scenario.get_event_turns("summer_camp")
    twinkle_star_turns = _scenario.get_event_turns("twinkle_star")
    in_summer_camp = _current_turn in summer_camp_turns
    in_ts_climax = _current_turn in twinkle_star_turns
    if (in_summer_camp or in_ts_climax) and mood in ("NORMAL", "BAD"):
        inventory = _shop_manager.inventory
        cupcake_key = None
        if mood == "BAD" and inventory.get("berry_cupcake", 0) > 0:
            cupcake_key = "berry_cupcake"
        elif inventory.get("plain_cupcake", 0) > 0:
            cupcake_key = "plain_cupcake"
        elif inventory.get("berry_cupcake", 0) > 0:
            cupcake_key = "berry_cupcake"
        if cupcake_key:
            log(f"Mood {mood} — using {cupcake_key} to boost")
            _use_training_items([cupcake_key])
            if _shop_manager._inventory.get(cupcake_key, 0) > 0:
                _shop_manager._inventory[cupcake_key] -= 1
                if _shop_manager._inventory[cupcake_key] <= 0:
                    del _shop_manager._inventory[cupcake_key]
            _shop_manager.save_inventory()
            time.sleep(1)
            img = _wait_for_career_home("post_cupcake")
            if img is None:
                return "recovering"

    # Skill shop — visit if SP exceeds threshold (configurable via strategy.yaml)
    # Playbook can defer skill buying (e.g., Sirius strategy waits until ~2500 SP)
    # Exception: force skill shop before Kikuka Sho (turn 44) to buy recovery skills
    sp_threshold = _overrides.get_strategy().raw.get("skill_shop_sp_threshold", 1200)
    force_recovery_shop = (
        _current_turn in (43, 44)
        and _recovery_skills_bought < 2
        and not _skill_shop_done
    )
    if not force_recovery_shop and _playbook_engine and _playbook_engine.should_defer_skills(_skill_pts, _current_turn):
        log(f"Playbook: deferring skill shop (SP={_skill_pts}, turn={_current_turn})")
    elif force_recovery_shop or (_skill_pts > sp_threshold and not _skill_shop_done):
        if force_recovery_shop:
            log(f"Forcing skill shop for recovery skills before Kikuka Sho (have {_recovery_skills_bought}/2)")
        log(f"SP {_skill_pts} > {sp_threshold} — visiting skill shop")
        tap(*BTN_HOME_SKILLS)
        time.sleep(2)
        for _ in range(20):
            img_sk = screenshot(f"skill_visit_{int(time.time())}")
            s_sk = detect_screen(img_sk)
            if s_sk == "skill_shop":
                result = handle_skill_shop(img_sk, force_recovery=force_recovery_shop)
                if result == "skill_back":
                    break
            elif s_sk == "career_home":
                break
            elif s_sk in ("warning_popup", "unknown"):
                btn = find_green_button(img_sk, (1100, 1350))
                if btn:
                    tap(btn[0], btn[1])
                else:
                    tap(540, 960)
            else:
                tap(540, 960)
            time.sleep(2)
        img = _wait_for_career_home("post_skills")
        if img is None:
            return "recovering"
        energy = get_energy_level(img)

    # =====================================================================
    # PHASE 3: Decide action (rest / race / train)
    # =====================================================================

    # Slacker is debilitating — go to Infirmary immediately if still active
    # (fires regardless of scheduled action — slacker kills all training value)
    if "slacker" in _active_conditions:
        log("SLACKER detected — going to Infirmary immediately")
        tap(*BTN_INFIRMARY)
        return "rest"  # Infirmary confirm handled same as rest confirm

    # Playbook-driven decision (if active) — consult turn schedule before dynamic logic
    if _playbook_engine and _last_result != "race_back":
        pb_action = _playbook_engine.decide_turn(_game_state)
        # Negative condition on flex/train/rest turns → divert to Infirmary.
        # Race and recreation turns are schedule-critical, so conditions don't override them.
        if (_active_conditions
                and pb_action.action_type in (ActionType.WAIT, ActionType.TRAIN, ActionType.REST)):
            log(f"Negative condition {_active_conditions} on non-critical turn — going to Infirmary")
            _scenario.on_non_race_action()
            tap(*BTN_INFIRMARY)
            return "rest"
        if pb_action.action_type == ActionType.GO_OUT:
            log(f"Playbook: Recreation — {pb_action.reason}")
            _scenario.on_non_race_action()
            tap(*BTN_RECREATION)
            return "recreation"
        elif pb_action.action_type == ActionType.RACE:
            log(f"Playbook: Racing — {pb_action.reason}")
            # Pass target race name to selector for forced matching.
            # Pair-tagged turns store the race name in `race` (clean); other
            # turns put it in `note` (with extra annotation text).
            scheduled = _playbook_engine._get_scheduled_action(_current_turn)
            target_name = ""
            if scheduled:
                target_name = scheduled.race or ""
            if target_name:
                _race_selector._target_race_name = target_name
                log(f"  Target race: {target_name}")
            tap(*BTN_HOME_RACES)
            return "going_to_races"
        elif pb_action.action_type == ActionType.REST:
            log(f"Playbook: Resting — {pb_action.reason}")
            _scenario.on_non_race_action()
            tap(*BTN_REST)
            return "rest"
        elif pb_action.action_type == ActionType.INFIRMARY:
            log(f"Playbook: Infirmary — {pb_action.reason}")
            _scenario.on_non_race_action()
            tap(*BTN_INFIRMARY)
            return "rest"  # infirmary_confirm handled alongside rest_confirm
        elif pb_action.action_type == ActionType.TRAIN:
            log(f"Playbook: Training — {pb_action.reason}")
            _scenario.on_non_race_action()
            _use_megaphone_if_needed()
            # The drink-or-not decision happens in handle_training after tiles
            # are previewed — we need to see the best tile's score vs. failure
            # rate to know whether a drink is worth spending.
            _playbook_force_train = True
            tap(*BTN_TRAINING)
            return "going_to_training"
        # WAIT = fall through to existing dynamic logic

    # If we just came back from race_list with no good races, train or rest
    if _last_result == "race_back":
        _scenario.on_non_race_action()
        summer_camp_rb = _scenario.get_event_turns("summer_camp")
        if energy < 50 and _current_turn not in summer_camp_rb:
            log(f"No good races, energy {energy}% — resting")
            tap(*BTN_REST)
            return "rest"
        log("No good races available — going to Training instead")
        tap(*BTN_TRAINING)
        return "going_to_training"

    # Energy budget lookahead — check if we need to conserve for an upcoming milestone
    mood = detect_mood(img)
    conserve, conserve_reason = should_conserve_energy(
        _current_turn, energy, _shop_manager.inventory, mood,
    )

    _game_state.energy = energy
    already_tried = (_race_attempted_turn == _current_turn)
    if already_tried:
        log(f"Already attempted race on turn {_current_turn} — skipping race decision")
    race_action = None if (is_pre_debut or already_tried) else _race_selector.should_race_this_turn(_game_state)

    if race_action:
        # Conservation overrides non-mandatory races (rhythm, low-energy races)
        # but NOT goal races or G1s — those are too important to skip
        is_mandatory = "Goal race" in race_action.reason or "G1 available" in race_action.reason
        if conserve and not is_mandatory:
            log(f"Lookahead: conserving energy — {conserve_reason}")
            log(f"  (would have raced: {race_action.reason})")
            _scenario.on_non_race_action()
        else:
            # Consecutive race gate: at 3+, require mood item + condition cure.
            # Exception: G1 streak (e.g. fall triple) — all races in chain are G1s.
            consec = _scenario._consecutive_races
            is_g1 = "G1 available" in race_action.reason
            in_g1_streak = is_g1 and _scenario._consecutive_g1s >= consec
            if consec >= 3 and not in_g1_streak:
                inv = _shop_manager.inventory
                has_mood = inv.get("plain_cupcake", 0) > 0 or inv.get("berry_cupcake", 0) > 0
                has_cure = (inv.get("rich_hand_cream", 0) > 0
                            or inv.get("miracle_cure", 0) > 0
                            or inv.get("smart_scale", 0) > 0)
                if has_mood and has_cure:
                    log(f"Racing (consecutive {consec}): prepared with mood+cure items")
                    log(f"Racing: {race_action.reason}")
                    _last_race_was_g1 = is_g1
                    _g1_retried_this_race = False
                    _race_attempted_turn = _current_turn
                    tap(*BTN_HOME_RACES)
                    return "going_to_races"
                else:
                    missing = []
                    if not has_mood:
                        missing.append("mood item")
                    if not has_cure:
                        missing.append("condition cure")
                    log(f"Consecutive {consec} races — blocking, missing: {', '.join(missing)}")
                    _scenario.on_non_race_action()
            else:
                log(f"Racing: {race_action.reason}")
                _last_race_was_g1 = is_g1
                _g1_retried_this_race = False
                _race_attempted_turn = _current_turn
                tap(*BTN_HOME_RACES)
                return "going_to_races"
    else:
        # Not racing — notify scenario to reset consecutive race counter
        _scenario.on_non_race_action()

    # Rest vs train — use lookahead budget instead of fixed threshold
    if conserve:
        log(f"Energy {energy}%, conserving for milestone — resting")
        tap(*BTN_REST)
        return "rest"

    # Low energy floor — don't waste time entering training just to back out.
    # The failure rate check on the training screen is the precise gate;
    # this is a fast pre-filter to avoid the 15s tile scan at very low energy.
    summer_camp = _scenario.get_event_turns("summer_camp")
    in_summer = _current_turn in summer_camp
    if energy < 30 and not in_summer:
        # For playbooks where training turns are rare (Sirius+Riko etc),
        # burn a drink rather than waste a training turn on rest.
        drink_before_rest = (
            _playbook_engine is not None
            and _playbook_engine.playbook.drink_before_rest
        )
        if drink_before_rest:
            inventory = _shop_manager.inventory
            drink_candidates = [("vita_20", 20), ("vita_40", 40), ("royal_kale", 50), ("vita_65", 65)]
            drink_key = next((k for k, _ in drink_candidates if inventory.get(k, 0) > 0), None)
            if drink_key:
                log(f"Energy {energy}% low — playbook drink_before_rest: using {drink_key}")
                if _use_training_items([drink_key]):
                    if _shop_manager._inventory.get(drink_key, 0) > 0:
                        _shop_manager._inventory[drink_key] -= 1
                        if _shop_manager._inventory[drink_key] <= 0:
                            del _shop_manager._inventory[drink_key]
                    _shop_manager.save_inventory()
                    time.sleep(1)
                    img = screenshot(f"post_drink_{int(time.time())}")
                    if detect_screen(img) == "career_home":
                        energy = get_energy_level(img)
                        log(f"Energy after {drink_key}: ~{energy}%")
                else:
                    log(f"Failed to use {drink_key} — falling back to rest")
                    tap(*BTN_REST)
                    return "rest"
            else:
                log(f"Energy {energy}% too low — resting (no drinks in inventory)")
                tap(*BTN_REST)
                return "rest"
        else:
            log(f"Energy {energy}% too low — resting")
            tap(*BTN_REST)
            return "rest"

    log(f"Training turn, energy {energy}%")
    _use_megaphone_if_needed()
    tap(*BTN_TRAINING)
    return "going_to_training"


def _desired_strategy(turn=None, distance=None):
    """Determine desired running strategy.

    Phase logic:
    - Turns 1-40 (Jr + early Classic through summer camp): run pace or front
      (whichever has better aptitude) to minimize losses and secure early wins.
    - Turns 41+ (post-Classic summer): switch to 'end' (End Closer) to farm
      End aptitude sparks for progeny inheritance.
    - Exception: mile races (1401-1800m) ALWAYS use pace/front regardless of
      turn — End Closer underperforms at mile distances.
    """
    if turn is None:
        turn = _current_turn
    if distance is None:
        distance = _last_race_distance or 0

    GRADE_ORDER = {"s": 6, "a": 5, "b": 4, "c": 3, "d": 2, "e": 1, "f": 0, "g": 0}
    front = GRADE_ORDER.get((_cached_aptitudes or {}).get("front", "").lower(), 0)
    pace = GRADE_ORDER.get((_cached_aptitudes or {}).get("pace", "").lower(), 0)

    def _pace_or_front():
        return "front" if front > pace else "pace"

    # Mile races always run pace/front
    if 1401 <= distance <= 1800:
        return _pace_or_front()

    # Post-Classic summer: switch to End Closer for sparks
    if turn > 40:
        return "end"

    return _pace_or_front()


def _detect_current_strategy(img):
    """Detect the currently selected strategy from the pre-race screen.

    The selected strategy circle has a near-white interior (~255,255,255)
    while unselected ones are gray (~149,155,157).  Sample pixel at the
    center-bottom of each circle (y≈1020) and check brightness.

    Returns one of "end", "late", "pace", "front", or None if detection fails.
    """
    import numpy as np
    arr = np.array(img)
    # Circle x-centers (from OCR) and sample y at top of circle (avoids number text)
    CIRCLES = {"end": 723, "late": 813, "pace": 903, "front": 993}
    SAMPLE_Y = 1005

    for name, cx in CIRCLES.items():
        # Sample a small 5x5 box at top of circle interior
        region = arr[SAMPLE_Y - 2:SAMPLE_Y + 3, cx - 2:cx + 3]
        avg = region.mean(axis=(0, 1))
        if min(avg[0], avg[1], avg[2]) > 240:
            return name
    return None


def _set_race_strategy(img):
    """Check and change running strategy on the pre-race screen if needed."""
    if _current_turn == 0 and _last_race_distance == 0:
        log("Race strategy: no turn/distance info (retry?) — keeping current strategy")
        return
    desired = _desired_strategy()
    current = _detect_current_strategy(img)
    log(f"Race strategy: desired={desired}, current={current}, turn={_current_turn}, dist={_last_race_distance}m")

    if current == desired:
        log(f"Strategy already {desired} — no change needed")
        return

    if current is None:
        log("Could not detect current strategy — opening Change dialog")

    # Tap Change button in Strategy section
    tap(720, 1103, delay=1.5)
    img2 = screenshot("strategy_popup")

    # Verify popup opened (should have "Cancel" and "Confirm")
    popup_text = " ".join(t for t, c, _ in ocr_full_screen(img2) if c > 0.3).lower()
    if "confirm" not in popup_text and "cancel" not in popup_text:
        log("Strategy popup did not open — skipping strategy change")
        return

    # Strategy buttons in popup: End, Late, Pace, Front (left to right)
    # Button y range: 1142-1188, center ~1165
    STRATEGY_X = {"end": 198, "late": 425, "pace": 652, "front": 880}
    STRATEGY_Y = 1165

    target_x = STRATEGY_X.get(desired, 652)
    log(f"Selecting {desired} strategy at ({target_x}, {STRATEGY_Y})")
    tap(target_x, STRATEGY_Y, delay=0.5)

    # Tap Confirm (y range: 1355-1395, center ~1375)
    tap(777, 1375)
    time.sleep(1)
    log(f"Strategy set to {desired}")


_INTERMEDIATE_RESULTS = {
    "going_to_races", "going_to_training", "going_to_training_summer", "going_to_training_ts_climax", "race_confirm", "pre_race",
    "race_enter", "result_pts", "standings_next", "tap_prompt",
    "cutscene_skip", "tutorial_slide", "goal_complete", "fan_class",
    "unlock_popup", "trophy_won", "race_lineup", "post_race_next",
    "shop_popup_enter", "unknown", "event", "event_choice", "skill_confirm", "skills_learned_close",
    "continue_career", "recovering", "placement_next", "ts_climax_racing", "race_day_racing",
    "ts_climax_standings", "ts_standings_next", "post_career_next",
    "post_career_confirm", "career_finishing", "warning_ok",
    "rest", "recreation", "recreation_cancel", "recreation_select", "recreation_member_select", "rest_confirm", "race_back",
    "training_back_to_rest", "race_live_skip", "career_home_summer",
    "photo_save_cancel", "race_photo_skip", "quick_mode_dismiss",
    "retry_race", "try_again_confirmed", "log_close",
    "shop_done",
}

def run_one_turn(stop_before=None, stop_on_turn_advance=True):
    """Execute one full game turn. Loops through intermediate screens.

    Args:
        stop_before: set of screen names. If detected, return immediately
                     WITHOUT acting (e.g. {"complete_career"} to stop at
                     end-of-career without opening skill shop).
        stop_on_turn_advance: if True (default), return as soon as _current_turn
                     advances past the turn we started on. Prevents one run_one
                     call from cascading across multiple turns (e.g. race →
                     forced rest → train). Set False for TS Climax/career end
                     where turn numbers don't mean the same thing.
    """
    global _last_result, _backed_out_to_home_this_turn
    _backed_out_to_home_this_turn = False
    prev_result = None
    repeat_count = 0
    start_turn = None  # Captured on first iteration where _current_turn > 0
    for _ in range(50):
        result = _run_one_turn_inner(stop_before=stop_before)
        _last_result = result
        log(f"Result: {result}")
        # Capture starting turn on first successful state build
        if stop_on_turn_advance and start_turn is None and _current_turn > 0:
            start_turn = _current_turn
            log(f"run_one_turn: tracking turn advance from turn {start_turn}")
        # Stop if the turn has advanced past where we started
        if stop_on_turn_advance and start_turn is not None and _current_turn > start_turn:
            log(f"run_one_turn: turn advanced {start_turn} → {_current_turn}, stopping")
            return f"turn_advanced:{result}"
        if result not in _INTERMEDIATE_RESULTS:
            return result
        # Stuck detection: if same result 15 times in a row, bail out
        if result == prev_result:
            repeat_count += 1
            if repeat_count >= 15:
                log(f"Stuck loop detected: '{result}' repeated {repeat_count} times — breaking out")
                return f"stuck_{result}"
        else:
            prev_result = result
            repeat_count = 1
        time.sleep(2.5)
    log("run_one_turn: hit 50 action limit")
    return result


def _run_one_turn_inner(stop_before=None):
    """Internal: execute one game action."""
    global _last_result, _needs_shop_visit, _last_shop_turn, _inventory_checked, _skill_shop_done, _summer_whistle_used, _backed_out_to_home_this_turn, _pending_training_stat, _pending_training_turn

    img = screenshot(f"auto_{int(time.time())}")
    screen = detect_screen(img)
    log(f"Detected: {screen}")

    if stop_before and screen in stop_before:
        log(f"Stop-before triggered: {screen}")
        return f"stopped:{screen}"

    if screen == "career_home_summer":
        # SUMMER CAMP: train as much as possible, never race

        # Read inventory on first encounter (same as career_home init)
        if not _inventory_checked:
            read_inventory_from_training_items()
            time.sleep(1)
            img = screenshot(f"summer_post_inv_{int(time.time())}")
            if detect_screen(img) != "career_home_summer":
                return "recovering"

        energy = get_energy_level(img)
        # Refresh _current_turn from period OCR — otherwise playbook lookups
        # stay stuck on the pre-summer turn number across intermediate loops.
        build_game_state(img, "career_home", energy=energy)
        _summer_packet_fresh = _session_tailer is not None and _session_tailer.is_fresh()
        if not _summer_packet_fresh:
            _detect_active_effects()
            img = screenshot(f"summer_post_effects_{int(time.time())}")
            if detect_screen(img) != "career_home_summer":
                return "recovering"
            energy = get_energy_level(img)
        mood = detect_mood(img)
        inventory = _shop_manager.inventory
        active_megas = [e for e in _shop_manager._active_effects if "mega" in e.item_key]
        mega_info = f"{active_megas[0].item_key} ({active_megas[0].turns_remaining} left)" if active_megas else "none"
        log(f"SUMMER CAMP — Energy: ~{energy}%, Mood: {mood}, Megaphone: {mega_info}")

        # Per-turn shop visit during summer — drinks and charms are critical here.
        if _last_shop_turn != _current_turn:
            _do_fast_path_shop(from_summer=True)
            img = screenshot(f"summer_post_shop_{int(time.time())}")
            if detect_screen(img) != "career_home_summer":
                return "recovering"
            energy = get_energy_level(img)
            mood = detect_mood(img)
            inventory = _shop_manager.inventory

        # 1. Mood check — AWFUL/BAD must be fixed first via Recreation
        #    Do NOT use megaphone — it would waste a turn of the buff
        if mood in ("AWFUL", "BAD"):
            log(f"SUMMER CAMP — Mood {mood}, doing Rest & Recreation")
            tap(210, 1460)
            return "recreation"

        # 1b. Boost GOOD/NORMAL to GREAT with a cupcake — summer stacks favor GREAT.
        if mood != "GREAT":
            cupcake_key = None
            if inventory.get("plain_cupcake", 0) > 0:
                cupcake_key = "plain_cupcake"
            elif inventory.get("berry_cupcake", 0) > 0:
                cupcake_key = "berry_cupcake"
            if cupcake_key:
                log(f"SUMMER CAMP — Mood {mood}, using {cupcake_key} to reach GREAT")
                if _use_training_items([cupcake_key]):
                    if _shop_manager._inventory.get(cupcake_key, 0) > 0:
                        _shop_manager._inventory[cupcake_key] -= 1
                        if _shop_manager._inventory[cupcake_key] <= 0:
                            del _shop_manager._inventory[cupcake_key]
                    _shop_manager.save_inventory()
                time.sleep(1)
                img = screenshot(f"summer_post_cupcake_{int(time.time())}")
                if detect_screen(img) != "career_home_summer":
                    return "recovering"
                mood = detect_mood(img)

        # 2. Energy + failure-insurance plan — delegated to SummerPlanner.
        summer_turns_all = _scenario.get_event_turns("summer_camp") or set()
        turns_remaining = sum(1 for t in summer_turns_all if t >= _current_turn) or 1
        plan = plan_summer_turn(
            energy=energy,
            turns_remaining=turns_remaining,
            inventory=inventory,
        )
        log(f"SUMMER CAMP — Plan: {plan.kind}"
            f"{' (' + plan.item_key + ')' if plan.item_key else ''}"
            f" — {plan.reason}")

        can_train = plan.kind != "none" or energy >= 50

        def _consume_and_screenshot(item_key: str, tag: str) -> tuple[bool, "Image"]:
            if not _use_training_items([item_key]):
                log(f"SUMMER CAMP — Failed to use {item_key}")
                return False, None
            if _shop_manager._inventory.get(item_key, 0) > 0:
                _shop_manager._inventory[item_key] -= 1
                if _shop_manager._inventory[item_key] <= 0:
                    del _shop_manager._inventory[item_key]
            _shop_manager.save_inventory()
            time.sleep(1)
            new_img = screenshot(f"summer_post_{tag}_{int(time.time())}")
            return True, new_img

        if plan.kind == "drink":
            ok, new_img = _consume_and_screenshot(plan.item_key, "drink")
            if ok:
                if detect_screen(new_img) != "career_home_summer":
                    return "recovering"
                energy = get_energy_level(new_img)
                log(f"SUMMER CAMP — Energy after drink: ~{energy}%")
        elif plan.kind in ("kale", "kale_cupcake"):
            ok, new_img = _consume_and_screenshot("royal_kale", "kale")
            if ok:
                if detect_screen(new_img) != "career_home_summer":
                    return "recovering"
                energy = get_energy_level(new_img)
                log(f"SUMMER CAMP — Energy after kale: ~{energy}%")
                if plan.kind == "kale_cupcake":
                    cupcake_key = ("plain_cupcake"
                                   if inventory.get("plain_cupcake", 0) > 0
                                   else "berry_cupcake")
                    log(f"SUMMER CAMP — Using {cupcake_key} to restore mood after kale")
                    ok2, new_img2 = _consume_and_screenshot(cupcake_key, "cupcake")
                    if ok2 and detect_screen(new_img2) != "career_home_summer":
                        return "recovering"
        elif plan.kind == "charm":
            ok, new_img = _consume_and_screenshot("good_luck_charm", "charm")
            if ok:
                _shop_manager.activate_item("good_luck_charm")
                if detect_screen(new_img) != "career_home_summer":
                    return "recovering"

        if not can_train:
            log(f"SUMMER CAMP — Energy ~{energy}%, no recovery items — resting")
            _scenario.on_non_race_action()
            tap(*BTN_REST)
            time.sleep(2)
            img2 = screenshot(f"rest_check_{int(time.time())}")
            s2 = detect_screen(img2)
            if "confirm" in s2 or "warning" in s2:
                ok = find_green_button(img2, (1150, 1350))
                if ok:
                    tap(ok[0], ok[1])
            return "rest"

        # 3. Ensure a megaphone buff is active
        # Use active_megas computed before energy checks (line ~2926) to avoid
        # tick_effects expiring the effect due to OCR turn variance in handle_training.
        has_mega = len(active_megas) > 0
        if not has_mega:
            # Use best available: Empowering (+60%, 2 turns) > Motivating (+40%, 3 turns)
            mega_key = None
            if inventory.get("empowering_mega", 0) > 0:
                mega_key = "empowering_mega"
            elif inventory.get("motivating_mega", 0) > 0:
                mega_key = "motivating_mega"
            if mega_key:
                log(f"SUMMER CAMP — No active megaphone, using {mega_key}")
                if _use_training_items([mega_key]):
                    if _shop_manager._inventory.get(mega_key, 0) > 0:
                        _shop_manager._inventory[mega_key] -= 1
                        if _shop_manager._inventory[mega_key] <= 0:
                            del _shop_manager._inventory[mega_key]
                    _shop_manager.save_inventory()
                    _shop_manager.activate_item(mega_key)
                else:
                    log(f"SUMMER CAMP — Failed to use {mega_key}, removing from tracked inventory")
                    if _shop_manager._inventory.get(mega_key, 0) > 0:
                        _shop_manager._inventory[mega_key] -= 1
                        if _shop_manager._inventory[mega_key] <= 0:
                            del _shop_manager._inventory[mega_key]
                    _shop_manager.save_inventory()
                time.sleep(1)
                img = screenshot(f"summer_post_mega_{int(time.time())}")
                if detect_screen(img) != "career_home_summer":
                    return "recovering"
        else:
            active_mega = next(e for e in _shop_manager._active_effects if "mega" in e.item_key)
            log(f"SUMMER CAMP — Megaphone active: {active_mega.item_key} ({active_mega.turns_remaining} turns left)")

        # (Charm usage is now handled in the energy/insurance plan above — see §2.)

        # 4. Pre-score tiles from packets to skip preview loop in handle_training.
        #    Whistle + ankle weights are handled here instead of inside handle_training.
        packet_tiles = _build_packet_training_tiles()
        if packet_tiles:
            state = build_game_state(img, "career_home_summer", energy=energy)
            state.training_tiles = packet_tiles
            all_levels = [b for tile in packet_tiles for b in tile.bond_levels]
            state.all_bonds_maxed = bool(all_levels) and all(b >= 80 for b in all_levels)
            action = _scorer.best_action(state)
            scored_tiles = _scorer.score_tiles(state)
            best_score = scored_tiles[0][1] if scored_tiles else 0
            log(f"SUMMER CAMP — Pre-scored from packets (best: {best_score:.1f})")
            for st_tile, st_score in scored_tiles:
                log(f"  {st_tile.stat_type.value:8s}: score={st_score:5.1f}  cards={len(st_tile.support_cards)}  gains={dict(st_tile.stat_gains)}")

            summer_turns = set(range(37, 41)) | set(range(61, 65))
            is_whistle_turn = _current_turn in summer_turns or _current_turn >= 72
            if (is_whistle_turn and best_score < WHISTLE_THRESHOLD
                    and not _summer_whistle_used
                    and inventory.get("reset_whistle", 0) > 0):
                log(f"SUMMER CAMP — Best score {best_score:.1f} < {WHISTLE_THRESHOLD}, using Reset Whistle from home")
                _summer_whistle_used = True
                if _use_training_items(["reset_whistle"]):
                    if _shop_manager._inventory.get("reset_whistle", 0) > 0:
                        _shop_manager._inventory["reset_whistle"] -= 1
                        if _shop_manager._inventory["reset_whistle"] <= 0:
                            del _shop_manager._inventory["reset_whistle"]
                    _shop_manager.save_inventory()
                    log("SUMMER CAMP — Whistle used, waiting for fresh packet then re-entering")
                    time.sleep(2)
                else:
                    log("SUMMER CAMP — Failed to use Reset Whistle")
                    if _shop_manager._inventory.get("reset_whistle", 0) > 0:
                        _shop_manager._inventory["reset_whistle"] -= 1
                        if _shop_manager._inventory["reset_whistle"] <= 0:
                            del _shop_manager._inventory["reset_whistle"]
                    _shop_manager.save_inventory()
                return "training_back_to_rest"

            if action.action_type != ActionType.REST:
                chosen_stat = action.target.lower() if action.target else ""
                from uma_trainer.decision.shop_manager import ANKLE_WEIGHT_MAP
                ankle_key = ANKLE_WEIGHT_MAP.get(chosen_stat)
                active_weights = [e for e in _shop_manager._active_effects if "ankle" in e.item_key]
                if (is_whistle_turn and ankle_key and not active_weights
                        and _shop_manager.inventory.get(ankle_key, 0) > 0):
                    log(f"SUMMER CAMP — Using {ankle_key} for {chosen_stat} training (+50% gain)")
                    if _use_training_items([ankle_key]):
                        if _shop_manager._inventory.get(ankle_key, 0) > 0:
                            _shop_manager._inventory[ankle_key] -= 1
                            if _shop_manager._inventory[ankle_key] <= 0:
                                del _shop_manager._inventory[ankle_key]
                        _shop_manager.save_inventory()
                        _shop_manager.activate_item(ankle_key)
                    else:
                        log(f"SUMMER CAMP — Failed to use {ankle_key}")
                        if _shop_manager._inventory.get(ankle_key, 0) > 0:
                            _shop_manager._inventory[ankle_key] -= 1
                            if _shop_manager._inventory[ankle_key] <= 0:
                                del _shop_manager._inventory[ankle_key]
                        _shop_manager.save_inventory()
                    time.sleep(1)
                    img = screenshot(f"summer_post_ankle_{int(time.time())}")
                    if detect_screen(img) != "career_home_summer":
                        return "recovering"
                _pending_training_stat = chosen_stat
                _pending_training_turn = _current_turn
                log(f"SUMMER CAMP — Pre-committed to {chosen_stat} (score {best_score:.1f}, skipping preview)")

        log(f"SUMMER CAMP — Energy ~{energy}% OK — going to Training")
        tap(*BTN_TRAINING)
        return "going_to_training_summer"

    if screen == "race_day":
        log("Race Day — tapping Race!")
        tap(620, 1680)
        return "race_day_racing"

    if screen == "ts_climax_race":
        # Dismiss any trainee dialogue overlay first
        tap(540, 500, delay=1.0)
        if not _inventory_checked:
            read_inventory_from_training_items()
            time.sleep(1)
            img = screenshot(f"ts_race_post_inv_{int(time.time())}")
            if detect_screen(img) != "ts_climax_race":
                return "recovering"
        log("TS CLIMAX Race Day — using Master Cleat and racing")
        _use_cleat_for_race(is_ts_climax=True)
        # Only one race in TS Climax — tap Race directly
        tap(620, 1680)
        return "ts_climax_racing"

    if screen == "ts_climax_home":
        # TS Climax with Training/Rest buttons visible = training turn
        # Race turns force you into race selection directly
        # Note: _summer_whistle_used is NOT reset here — it resets per career_home turn.
        # Resetting here caused infinite whistle-retry loops when _use_training_items failed.

        # Read inventory on first encounter
        if not _inventory_checked:
            read_inventory_from_training_items()
            time.sleep(1)
            img = screenshot(f"ts_post_inv_{int(time.time())}")
            if detect_screen(img) != "ts_climax_home":
                return "recovering"

        energy = get_energy_level(img)
        _ts_packet_fresh = _session_tailer is not None and _session_tailer.is_fresh()
        if not _ts_packet_fresh:
            _detect_active_effects()
            img = screenshot(f"ts_post_effects_{int(time.time())}")
            if detect_screen(img) != "ts_climax_home":
                return "recovering"
            energy = get_energy_level(img)
        else:
            active = _shop_manager._active_effects
            log(f"Active effects from packet: {[(e.item_key, e.turns_remaining) for e in active] if active else 'none'}")
        log(f"TS CLIMAX training turn — Energy: ~{energy}%")

        # 1. Cupcake — bring mood to Great before training (stacking multiplier)
        mood = detect_mood(img)
        if mood != "GREAT":
            inventory = _shop_manager.inventory
            cupcake_key = None
            if mood == "BAD" and inventory.get("berry_cupcake", 0) > 0:
                cupcake_key = "berry_cupcake"
            elif inventory.get("plain_cupcake", 0) > 0:
                cupcake_key = "plain_cupcake"
            elif inventory.get("berry_cupcake", 0) > 0:
                cupcake_key = "berry_cupcake"
            if cupcake_key:
                log(f"TS CLIMAX mood {mood} — using {cupcake_key}")
                _use_training_items([cupcake_key])
                if _shop_manager._inventory.get(cupcake_key, 0) > 0:
                    _shop_manager._inventory[cupcake_key] -= 1
                    if _shop_manager._inventory[cupcake_key] <= 0:
                        del _shop_manager._inventory[cupcake_key]
                _shop_manager.save_inventory()
                time.sleep(1)
                img = screenshot(f"ts_post_cupcake_{int(time.time())}")
                if detect_screen(img) != "ts_climax_home":
                    return "recovering"
                energy = get_energy_level(img)

        # 2. Megaphone — maximise every training turn
        _use_megaphone_if_needed(is_ts_climax=True)

        # 2. Energy drink to top up without overcapping
        inventory = _shop_manager.inventory
        for key, gain in [("vita_65", 65), ("vita_40", 40), ("vita_20", 20)]:
            if inventory.get(key, 0) > 0 and energy + gain <= 100:
                log(f"TS CLIMAX — using {key} (+{gain}) at energy {energy}%")
                _use_training_items([key])
                if _shop_manager._inventory.get(key, 0) > 0:
                    _shop_manager._inventory[key] -= 1
                    if _shop_manager._inventory[key] <= 0:
                        del _shop_manager._inventory[key]
                _shop_manager.save_inventory()
                time.sleep(2)
                img = screenshot(f"ts_post_vita_{int(time.time())}")
                if detect_screen(img) == "ts_climax_home":
                    energy = get_energy_level(img)
                    log(f"TS CLIMAX — energy after vita: ~{energy}%")
                break

        # 2.5. Good luck charm — 0% failure for 1 turn. TS Climax uses heavy stacking
        # with 60%+ mood bonus, but failure rate can still be 5-15%. Always burn a
        # charm if held (max_stock=4, cheap insurance on the biggest training gains).
        inventory = _shop_manager.inventory
        if inventory.get("good_luck_charm", 0) > 0:
            log(f"TS CLIMAX — using good_luck_charm (0% failure this turn)")
            if _use_training_items(["good_luck_charm"]):
                if _shop_manager._inventory.get("good_luck_charm", 0) > 0:
                    _shop_manager._inventory["good_luck_charm"] -= 1
                    if _shop_manager._inventory["good_luck_charm"] <= 0:
                        del _shop_manager._inventory["good_luck_charm"]
                _shop_manager.save_inventory()
                _shop_manager.activate_item("good_luck_charm")
            time.sleep(1)
            img = screenshot(f"ts_post_charm_{int(time.time())}")
            if detect_screen(img) != "ts_climax_home":
                return "recovering"

        # 3. Train
        tap(540, 1496)
        return "going_to_training_ts_climax"

    if screen == "career_home":
        return _handle_career_home(img)

    if screen == "tutorial_slide":
        # Check for Next vs Close button
        next_btn = find_green_button(img, (1780, 1900))
        if next_btn:
            log("Tutorial slide — tapping Next")
            tap(next_btn[0], next_btn[1])
        else:
            log("Tutorial/info slide — tapping Close")
            tap(180, 1853)
        return "tutorial_slide"

    elif screen == "continue_career":
        log("Continue Career dialog — tapping Resume")
        resume_btn = find_green_button(img, (1300, 1500))
        if resume_btn:
            tap(resume_btn[0], resume_btn[1])
        else:
            tap(270, 1410)
        return "continue_career"

    elif screen == "goal_complete":
        log("Goal complete / goals screen — tapping Next")
        next_btn = find_green_button(img, (1600, 1800))
        if next_btn:
            tap(next_btn[0], next_btn[1])
        else:
            tap(540, 1680)
        return "goal_complete"

    elif screen == "insufficient_pts":
        log("Insufficient Result Pts — tapping Race to earn points")
        race_btn = find_green_button(img, (1150, 1350))
        if race_btn:
            tap(race_btn[0], race_btn[1])
        else:
            tap(760, 1250)
        return "insufficient_pts_race"

    elif screen == "race_photo":
        log("Race photo screen — pressing Back to skip")
        press_back()
        # Confirm "Return to previous screen?" dialog — OK is the green button
        # in the lower-middle area of the dialog (~y=1250 on 1080x1920).
        ok = find_green_button(screenshot("race_photo_confirm"), (1100, 1350), x_range=(500, 1000))
        if ok:
            tap(ok[0], ok[1])
        else:
            tap(777, 1250)
        return "race_photo_skip"

    elif screen == "photo_save_popup":
        log("Photo save popup — tapping Cancel (storage full or unwanted)")
        tap(270, 1480)
        return "photo_save_cancel"

    elif screen == "quick_mode_settings":
        log("Quick Mode Settings dialog — tapping Confirm at (777, 1380)")
        tap(777, 1380)
        return "quick_mode_dismiss"

    elif screen == "warning_popup":
        if _last_result in ("going_to_races", "race_enter"):
            log(f"Race warning popup — tapping OK (consecutive: {_scenario._consecutive_races})")
        else:
            log("Warning popup — tapping OK")
        ok = find_green_button(img, (1150, 1350))
        if ok:
            tap(ok[0], ok[1])
        else:
            tap(760, 1250)
        return "warning_ok"

    elif screen == "race_list":
        return handle_race_list(img)

    elif screen == "race_confirm":
        _scenario.on_race_completed()
        log(f"Race confirm — tapping Race button (consecutive: {_scenario._consecutive_races})")
        race_btn = find_green_button(img, (1250, 1450))
        if race_btn:
            tap(race_btn[0], race_btn[1])
        else:
            tap(730, 1360)
        return "race_confirm"

    elif screen == "recreation_select":
        # Recreation card selection screen — pick the right card based on playbook
        if _playbook_engine and _playbook_engine.wants_recreation(_current_turn):
            source = _get_recreation_source()
            log(f"Recreation select — looking for '{source}'")
            tap_y = _find_recreation_card(img, source)
            if tap_y:
                log(f"  Found at y={tap_y}, tapping")
                tap(540, tap_y)
                return "recreation_select"  # Will loop back to detect recreation_confirm
            else:
                # Turn 18 = first scheduled Sirius recreation. If Sirius isn't on
                # the recreation screen, unlock RNG failed → switch to fallback.
                if (source == "team_sirius" and _current_turn == 18
                        and _strategy_name == "sirius_riko_v1"):
                    riko_y = _find_recreation_card(img, "riko")
                    if riko_y is None:
                        log("  ABORT: Neither Sirius nor Riko available at turn 18 — run is ruined")
                        tap(540, 1450)
                        raise SystemExit("Run ruined: no recreation source available at turn 18")
                    log("  Sirius not unlocked at turn 18 — activating fallback schedule (restart required)")
                    _FALLBACK_FLAG.write_text(f"activated turn {_current_turn}\n")
                    # Tap Riko for this turn, then exit so next process uses fallback
                    log(f"  Tapping Riko at y={riko_y} for this turn")
                    tap(540, riko_y)
                    return "recreation_select"
                # Sirius still missing later in fallback mode — substitute Riko
                # for this turn so we don't loop. Schedule stays the same;
                # Sirius rec_tracker entry remains 0 so future Sirius turns will
                # also substitute cleanly.
                if source == "team_sirius":
                    riko_y = _find_recreation_card(img, "riko")
                    if riko_y is not None:
                        log(f"  Sirius missing — substituting Riko at y={riko_y}")
                        _playbook_engine.rec_tracker.uses_remaining["team_sirius"] = 0
                        tap(540, riko_y)
                        return "recreation_select"
                log(f"  WARNING: Could not find '{source}' on recreation screen — marking exhausted, cancelling")
                # Mark this source as exhausted so playbook stops sending us here
                if _playbook_engine:
                    _playbook_engine.rec_tracker.uses_remaining[source] = 0
                    # Skip recreation for this turn so we don't loop
                    _playbook_engine.skipped_recreation_turns.add(_current_turn)
                tap(540, 1450)
                return "recreation_cancel"
        else:
            log("Recreation select — no playbook recreation, cancelling")
            tap(540, 1450)
            return "recreation_cancel"

    elif screen == "recreation_member_select":
        member = _get_recreation_member()
        log(f"Recreation member select — looking for '{member}'")
        if _last_result == "recreation_member_select":
            log(f"  Member select stuck — cancelling recreation via back button")
            if _playbook_engine:
                _playbook_engine.skipped_recreation_turns.add(_current_turn)
            press_back()
            return "recreation_cancel"
        tap_y = _find_member_on_screen(img, member)
        if tap_y:
            log(f"  Found {member} at y={tap_y}, tapping")
            tap(540, tap_y)
        else:
            log(f"  WARNING: Could not find '{member}' — tapping first available")
            tap(540, 300)
        return "recreation_member_select"

    elif screen == "recreation_confirm":
        # Confirm if: playbook wants recreation this turn, OR we just came from
        # recreation_select/member_select (means we intentionally chose recreation)
        intentional = (_last_result in ("recreation", "recreation_select", "recreation_member_select"))
        playbook_wants = _playbook_engine and _playbook_engine.wants_recreation(_current_turn)
        if playbook_wants or intentional:
            log("Recreation confirm — confirming (playbook scheduled)")
            ok = find_green_button(img, (1150, 1350))
            if ok:
                tap(ok[0], ok[1])
            else:
                tap(730, 1260)
            if _playbook_engine:
                # Pass the actual source from the schedule note so the
                # tracker decrements the right counter (otherwise it always
                # falls back to the priority head, e.g. team_sirius).
                source = _get_recreation_source()
                _playbook_engine.on_recreation_completed(source=source, turn=_current_turn)
            return "recreation"
        log("Recreation confirm detected — tapping Cancel (no playbook recreation)")
        tap(270, 1260)
        return "recreation_cancel"

    elif screen == "infirmary_confirm":
        log("Infirmary confirm — tapping OK")
        ok = find_green_button(img, (1150, 1350))
        if ok:
            tap(ok[0], ok[1])
        else:
            tap(730, 1250)
        return "rest_confirm"

    elif screen == "rest_confirm":
        if _last_result == "rest":
            log("Rest confirm — tapping OK (intentional rest)")
            ok = find_green_button(img, (1150, 1350))
            if ok:
                tap(ok[0], ok[1])
            else:
                tap(730, 1250)
        else:
            log(f"Rest confirm — unexpected (last_result={_last_result}), tapping Cancel")
            tap(270, 1250)
        return "rest_confirm"

    elif screen == "pre_race":
        # --- Running strategy selection ---
        _set_race_strategy(img)

        # Check if View Results is locked (gray button = RGB ~150,145,153)
        # vs unlocked (white/bright button). Sample the button center.
        vr_r, vr_g, vr_b = px(img, 300, 1790)
        view_results_locked = (vr_r < 170 and vr_g < 170 and vr_b < 170)
        if view_results_locked:
            log("Pre-race — View Results LOCKED, tapping Race (must watch)")
            race_btn = find_green_button(img, (1750, 1850), (500, 900))
            if race_btn:
                tap(race_btn[0], race_btn[1])
            else:
                tap(690, 1790)
        else:
            log("Pre-race — tapping View Results (skip animation)")
            tap(300, 1790)
        return "pre_race"

    elif screen == "tap_prompt":
        log("TAP prompt — tapping center")
        tap(540, 960)
        return "tap_prompt"

    elif screen == "result_pts_popup":
        log("Result Pts popup — tapping background to dismiss")
        tap(540, 400)
        return "result_pts"

    elif screen == "log_close":
        log("Log screen — tapping Close")
        tap(540, 1780)
        return "log_close"

    elif screen == "post_race_standings":
        # Check placement via OCR — read the hero placement at top of screen first,
        # then fall back to full-screen scan. The hero area (top 700px) renders our
        # placement as a large stylized number; full-screen picks up list entries first.
        import re
        placement = 99
        is_climax = False
        is_g1 = False
        try:
            hero_results = ocr_region(img, 0, 0, 1080, 700)
            for text, conf in hero_results:
                t = text.strip().lower()
                m = re.match(r"(\d+)(st|nd|rd|th)", t)
                if m and placement == 99:
                    placement = int(m.group(1))
            standings_results = ocr_full_screen(img)
            is_make_debut = False
            for text, conf, y_pos in standings_results:
                t = text.strip().lower()
                if "climax" in t:
                    is_climax = True
                if t == "g1":
                    is_g1 = True
                if "make debut" in t:
                    is_make_debut = True
                if placement == 99:
                    m = re.match(r"(\d+)(st|nd|rd|th)", t)
                    if m:
                        placement = int(m.group(1))
        except Exception:
            pass
        global _last_race_placement
        _last_race_placement = placement
        # TS Climax: retry if placed worse than 3rd (max 3 retries)
        global _ts_climax_retries
        if is_climax and placement > 3 and _ts_climax_retries < 3:
            _ts_climax_retries += 1
            log(f"TS Climax race — placed {placement}th, tapping Try Again (retry {_ts_climax_retries}/3)")
            tap(270, 1780)
            return "retry_race"
        if is_climax and placement <= 3:
            _ts_climax_retries = 0  # Reset on success
        # G1 / Make Debut race retry: use alarm clock if we didn't win
        # (max 1 retry per race, 5 clocks per career)
        global _g1_retries, _g1_retried_this_race
        retry_eligible = is_g1 or is_make_debut
        race_label = "G1" if is_g1 else "Make Debut"
        if not is_climax and retry_eligible and placement > 1 and not _g1_retried_this_race and _g1_retries < 5:
            _g1_retries += 1
            _g1_retried_this_race = True
            log(f"{race_label} race — placed {placement}, using Alarm Clock to Try Again ({_g1_retries}/5 used)")
            tap(270, 1780)
            return "retry_race"
        elif not is_climax and retry_eligible and placement > 1 and _g1_retried_this_race:
            log(f"{race_label} race — placed {placement} on retry, accepting result (1 retry per race)")
            _g1_retried_this_race = False
        if placement == 1:
            _needs_shop_visit = True
            try:
                _NEEDS_SHOP_FILE.write_text("1")
            except Exception:
                pass
            log(f"Won race! Will visit shop next career_home")
        log(f"Standings — placed {placement}, tapping Next")
        next_btn = find_green_button(img, (1700, 1850), (500, 1000))
        if next_btn:
            tap(next_btn[0], next_btn[1])
        else:
            tap(750, 1780)
        return "standings_next"

    elif screen == "try_again_confirm":
        log("Try Again confirmation — tapping Try Again to confirm retry")
        tap(810, 1810)  # Green "Try Again" button
        return "try_again_confirmed"

    elif screen == "ts_climax_standings":
        log("TS Climax standings — tapping Next")
        next_btn = find_green_button(img, (1600, 1750), (300, 800))
        if next_btn:
            tap(next_btn[0], next_btn[1])
        else:
            tap(540, 1680)
        return "ts_standings_next"

    elif screen == "post_race_placement":
        log("Post-race placement — tapping Next")
        tap(540, 1792)
        return "placement_next"

    elif screen == "fan_class":
        log("Fan class — tapping Next")
        next_btn = find_green_button(img, (1750, 1870), (600, 1000))
        if next_btn:
            tap(next_btn[0], next_btn[1])
        else:
            tap(810, 1810)
        return "fan_next"

    elif screen == "unlock_popup":
        log("Unlock Requirements popup — tapping Close")
        tap(540, 1400)
        return "unlock_close"

    elif screen == "trophy_won":
        log("Trophy won! — tapping Close")
        tap(540, 1400)
        return "trophy_close"

    elif screen == "race_lineup":
        log("Race lineup — tapping Race! to start")
        race_btn = find_green_button(img, (1690, 1790))
        if race_btn:
            tap(race_btn[0], race_btn[1])
        else:
            tap(540, 1735)
        return "race_start"

    elif screen == "post_race_result":
        log("Post-race result — tapping to continue")
        tap(540, 960)
        return "post_race_result"

    elif screen == "inspiration":
        log("Inspiration screen — tapping GO!")
        tap(540, 1530)
        return "inspiration"

    elif screen == "race_live":
        log("Live race — tapping Skip to fast-forward")
        tap(970, 1862, delay=3.0)
        return "race_live_skip"

    elif screen == "concert_confirm":
        log("Concert playback prompt — pressing Back to dismiss")
        subprocess.run(["adb", "-s", DEVICE, "shell", "input", "keyevent", "KEYCODE_BACK"])
        return "concert_cancel"

    elif screen == "concert":
        log("Victory concert — opening menu and skipping")
        tap(1040, 1880, delay=1.5)
        tap(1000, 1585, delay=2.0)
        # Confirmation dialog may appear — tap OK
        img3 = screenshot(f"concert_skip_{int(time.time())}")
        screen3 = detect_screen(img3)
        if screen3 == "warning_popup" or any("skip" in t.lower() for t, c, y in ocr_full_screen(img3) if c > 0.3):
            ok_btn = find_green_button(img3, (1150, 1350))
            if ok_btn:
                tap(ok_btn[0], ok_btn[1])
            else:
                tap(730, 1250)
        return "concert_skip"

    elif screen == "cutscene":
        log("Cutscene — tapping Skip")
        tap(135, 1853)
        return "cutscene_skip"

    elif screen == "shop_popup":
        log("Shop refresh popup — tapping Shop to go buy items")
        tap(810, 1360)
        return "shop_popup_enter"

    elif screen == "shop":
        return handle_shop(img)

    elif screen == "event":
        return handle_event(img)

    elif screen == "training":
        # Check playbook — if the scheduled action is NOT train/flex, back out
        # to career home so the playbook can route to recreation/race/etc.
        # But skip this check if:
        # - the scheduled action already happened this turn (recreation completed
        #   → game drops us on training screen)
        # - we already backed out once this turn (prevents infinite loop when
        #   recreation is done but turn hasn't advanced yet)
        # If we came from summer camp home, the game doesn't allow racing/rec —
        # don't back out even if the schedule says race.
        came_from_summer = _last_result == "going_to_training_summer"
        came_from_ts_climax = _last_result == "going_to_training_ts_climax"
        if _playbook_engine and not came_from_summer and not came_from_ts_climax and _last_result not in ("recreation", "recreation_select",
                                                      "recreation_member_select",
                                                      "recreation_confirm",
                                                      "training_back_to_home") \
                            and not _backed_out_to_home_this_turn:
            scheduled = _playbook_engine._get_scheduled_action(_current_turn)
            # Don't back out if this turn's recreation already failed — decide_turn
            # has already been redirected to TRAIN and we're intentionally here.
            rec_skipped = (
                scheduled is not None
                and scheduled.action == "recreation"
                and _current_turn in _playbook_engine.skipped_recreation_turns
            )
            if scheduled and scheduled.action in ("recreation", "race", "rest") and not rec_skipped:
                log(f"On training screen but playbook says '{scheduled.action}' for turn {_current_turn} — backing out")
                _backed_out_to_home_this_turn = True
                tap(80, 1855)  # Back button
                return "training_back_to_home"
        result = handle_training()
        # If training was interrupted by an event, process it now
        if result in ("event", "goal_complete", "tap_prompt", "cutscene"):
            log(f"Training interrupted by {result} — handling it")
            img2 = screenshot(f"train_interrupt_{int(time.time())}")
            screen2 = detect_screen(img2)
            if screen2 == "event":
                return handle_event(img2)
            elif screen2 == "goal_complete":
                next_btn = find_green_button(img2, (1600, 1800))
                if next_btn:
                    tap(next_btn[0], next_btn[1])
                else:
                    tap(540, 1680)
                return "goal_complete"
            elif screen2 == "tap_prompt":
                tap(540, 960)
                return "tap_prompt"
            elif screen2 == "cutscene":
                tap(135, 1853)
                return "cutscene_skip"
        return result

    elif screen == "complete_career":
        if _skill_shop_done:
            log("Complete Career — skills done, tapping Complete Career")
            tap(810, 1565)  # Complete Career button (right side)
            return "career_complete_final"
        log("Complete Career — opening skill shop to spend remaining pts")
        tap(270, 1565)  # Skills button
        return "complete_career"

    elif screen == "complete_career_finish":
        log("Complete Career finish dialog — tapping Finish")
        finish_btn = find_green_button(img, (1320, 1400), (550, 1000))
        if finish_btn:
            tap(finish_btn[0], finish_btn[1])
        else:
            tap(777, 1356)
        return "career_finishing"

    elif screen == "post_career_next":
        log("Post-career screen — tapping Next")
        tap(540, 1800)
        return "post_career_next"

    elif screen == "post_career_confirm":
        log("Epithet screen — tapping Confirm")
        tap(540, 1750)
        return "post_career_confirm"

    elif screen == "post_career_details":
        log("Umamusume Details — tapping Close")
        tap(540, 1750)
        return "post_career_close"

    elif screen == "career_complete_done":
        log("Career Complete — tapping To Home")
        tap(270, 1250)
        return "career_done"

    elif screen == "skill_confirm_dialog":
        log("Skill purchase confirmation — tapping Learn")
        tap(810, 1830)
        return "skill_confirm"

    elif screen == "skills_learned":
        log("Skills Learned popup — tapping Close")
        tap(540, 1200)
        return "skills_learned_close"

    elif screen == "skill_shop":
        return handle_skill_shop(img)

    elif screen in ("training_items_idle", "exchange_complete_idle"):
        # We landed on the items dialog without an active use flow driving
        # it (probable causes: a prior _use_training_items hit Confirm with
        # nothing selected, or the dialog reopened on its own). Tap Close
        # to dismiss — never blindly retry Confirm Use.
        active = is_button_active(img, *BTN_ITEMS_CONFIRM)
        log(f"Items dialog idle ({screen}); Confirm Use active={active} — tapping Close")
        tap(*BTN_ITEMS_CLOSE, delay=2.0)
        return "items_dialog_close"

    else:
        # Check if this is actually a pre_race screen that OCR missed
        ocr_texts = [t.lower() for t, c, _ in ocr_full_screen(img) if c > 0.3]
        ocr_joined = " ".join(ocr_texts)
        if "view results" in ocr_joined or "view result" in ocr_joined:
            log("Unknown screen — detected 'View Results', treating as pre_race")
            vr_r, vr_g, vr_b = px(img, 300, 1790)
            view_results_locked = (vr_r < 170 and vr_g < 170 and vr_b < 170)
            if view_results_locked:
                log("  View Results LOCKED, tapping Race")
                race_btn = find_green_button(img, (1750, 1850), (500, 900))
                if race_btn:
                    tap(race_btn[0], race_btn[1])
                else:
                    tap(690, 1790)
            else:
                log("  Tapping View Results")
                tap(300, 1790)
            return "pre_race"

        # Try to find a green button (Next, OK, etc.) before blindly tapping
        # x starts at 460 to skip the Skip/Quick buttons at x≈300-450
        # y capped at 1800 to avoid Skip/Quick/Log buttons at y≈1870
        green_btn = find_green_button(img, (1600, 1800))
        if green_btn:
            log(f"Unknown screen — found green button at {green_btn}, tapping")
            tap(green_btn[0], green_btn[1])
        else:
            log(f"Unknown screen — tapping center to advance")
            tap(540, 960)
        return "unknown"


def main():
    log("\n" + "=" * 50)
    log("Auto-turn session starting")
    log("=" * 50)

    num_turns = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    log(f"Running {num_turns} actions")

    global _last_result
    for i in range(num_turns):
        log(f"\n--- Action {i+1}/{num_turns} ---")
        result = run_one_turn()
        _last_result = result
        log(f"Result: {result}")
        time.sleep(2.5)

    log("\nSession complete")


if __name__ == "__main__":
    main()
