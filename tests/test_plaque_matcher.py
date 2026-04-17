"""Tests for ``uma_trainer.perception.plaque_matcher.PlaqueMatcher``.

These tests rely on the baseline race-list screenshot committed at
``screenshots/run_log/race_confirm_1775810631.png`` and the 302 plaque
templates in ``data/race_plaques/``. Both are required fixtures for the
Trackblazer automation and should always be present.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from uma_trainer.perception.plaque_matcher import (
    PlaqueMatcher,
    ResolvedRace,
    _parse_venue,
)

REPO = Path(__file__).resolve().parent.parent
SCREENSHOT = REPO / "screenshots/run_log/race_confirm_1775810631.png"
RACES_JSON = REPO / "data/gametora_races.json"

# Card regions match those in ``auto_turn._ocr_race_list``.
CARD_ASAHI = {"y_range": (1080, 1245), "tap_y": 1165}
CARD_HANSHIN = {"y_range": (1245, 1460), "tap_y": 1355}

# Track description strings that would be OCR'd off the screenshot.
TRACK_DESC_ASAHI = "Hanshin Turf 1600m (Mile) Right / Outer"
TRACK_DESC_HANSHIN = "Hanshin Turf 1600m (Mile) Right / Outer"


@pytest.fixture(scope="module")
def matcher() -> PlaqueMatcher:
    return PlaqueMatcher()


@pytest.fixture(scope="module")
def screenshot_img():
    if not SCREENSHOT.exists():
        pytest.skip(f"Baseline screenshot missing: {SCREENSHOT}")
    return Image.open(SCREENSHOT)


def test_matcher_loads_all_templates(matcher: PlaqueMatcher):
    # There are 302 plaque PNGs shipped with the repo.
    assert matcher.template_count == 302


def test_matcher_finds_asahi_hai(matcher: PlaqueMatcher, screenshot_img):
    result = matcher.match_card(screenshot_img, CARD_ASAHI)
    assert result is not None
    assert result.banner_id == 1022
    assert result.race_names == ["Asahi Hai Futurity Stakes"]
    # Confident match — runner-up well below.
    assert result.confidence >= 0.75
    assert result.confidence - result.runner_up_confidence >= 0.2


def test_matcher_finds_hanshin_juvenile_fillies(matcher: PlaqueMatcher, screenshot_img):
    result = matcher.match_card(screenshot_img, CARD_HANSHIN)
    assert result is not None
    assert result.banner_id == 1021
    assert result.race_names == ["Hanshin Juvenile Fillies"]
    # Hanshin's crop is noisier (portrait overlap) so the absolute score is
    # a bit lower; still want a clear win over the runner-up.
    assert result.confidence >= 0.55
    assert result.confidence - result.runner_up_confidence >= 0.2


def test_match_returns_none_on_empty_region(matcher: PlaqueMatcher, screenshot_img):
    # Degenerate crop (0x0) should return None, not crash.
    assert matcher.match(screenshot_img, 10, 10, 10, 10) is None


# ------------------------------------------------------------ resolve_race
def test_parse_venue_picks_known_token():
    assert _parse_venue("Hanshin Turf 1600m (Mile) Right / Outer") == "Hanshin"
    assert _parse_venue("Tokyo Dirt 2100m Left") == "Tokyo"
    assert _parse_venue("") == ""
    assert _parse_venue("unknown course") == ""


def test_resolve_race_asahi_hai(matcher: PlaqueMatcher, screenshot_img):
    """Asahi Hai Futurity Stakes — plaque match is already strong; the
    feature score should push combined confidence well past 0.85."""
    resolved = matcher.resolve_race(
        screenshot_img,
        CARD_ASAHI,
        distance=1600,
        surface="turf",
        track_desc=TRACK_DESC_ASAHI,
    )
    assert resolved is not None
    assert resolved.race_name == "Asahi Hai Futurity Stakes"
    assert resolved.banner_id == 1022
    assert resolved.combined_confidence > 0.85
    # Feature score should be a full match on this card.
    assert resolved.feature_score == pytest.approx(1.0, abs=1e-6)
    assert resolved.venue == "Hanshin"


def test_resolve_race_hanshin_juvenile_fillies(matcher: PlaqueMatcher, screenshot_img):
    """Hanshin JF — noisier plaque crop; features must boost the combined
    confidence comfortably past 0.65."""
    resolved = matcher.resolve_race(
        screenshot_img,
        CARD_HANSHIN,
        distance=1600,
        surface="turf",
        track_desc=TRACK_DESC_HANSHIN,
    )
    assert resolved is not None
    assert resolved.race_name == "Hanshin Juvenile Fillies"
    assert resolved.banner_id == 1021
    assert resolved.combined_confidence > 0.65
    assert resolved.feature_score == pytest.approx(1.0, abs=1e-6)


def test_resolve_race_hard_reject_distance(matcher: PlaqueMatcher, screenshot_img):
    """If OCR claims 2000m but the plaque matches a 1600m race, the
    matcher must refuse that variant. The next plaque candidate also
    fails its distance check, so the whole resolver returns None."""
    resolved = matcher.resolve_race(
        screenshot_img,
        CARD_ASAHI,
        distance=2000,  # wrong on purpose
        surface="turf",
        track_desc=TRACK_DESC_ASAHI,
    )
    # Either no candidate is accepted (None) or, if one is, it must not
    # be the 1600m Asahi Hai variant.
    if resolved is not None:
        assert resolved.distance == 2000
        assert resolved.banner_id != 1022


def test_resolve_race_hard_reject_surface(matcher: PlaqueMatcher, screenshot_img):
    """Same idea for surface: the Asahi Hai is turf, so if OCR reports
    dirt the resolver must not pick the turf variant."""
    resolved = matcher.resolve_race(
        screenshot_img,
        CARD_ASAHI,
        distance=1600,
        surface="dirt",  # wrong on purpose
        track_desc=TRACK_DESC_ASAHI,
    )
    if resolved is not None:
        assert resolved.surface == "dirt"
        assert resolved.banner_id != 1022


# ------------------------------------------------------- synthetic variants
def _find_multi_variant_banner() -> tuple[int, list[dict]]:
    """Find a banner_id in gametora_races.json with multiple distances.

    Returns the banner_id and its list of race variant dicts. Used by
    tests that want to exercise feature-based disambiguation without a
    screenshot."""
    data = json.loads(RACES_JSON.read_text())
    by_banner: dict[int, list[dict]] = {}
    for entry in data:
        bid = entry.get("banner_id")
        if bid is None:
            continue
        by_banner.setdefault(int(bid), []).append(entry)
    for bid, variants in sorted(by_banner.items()):
        distances = {int(r.get("distance") or 0) for r in variants}
        if len(distances) >= 2:
            return bid, variants
    raise RuntimeError("No multi-distance banner found in gametora_races.json")


def test_multi_variant_banner_disambiguation_by_distance(matcher: PlaqueMatcher):
    """Given a banner with multiple distance variants, ``_score_variant``
    must pick the one that matches the OCR'd distance."""
    banner_id, variants = _find_multi_variant_banner()
    variant_distances = {int(v["distance"]): v for v in variants}
    assert len(variant_distances) >= 2  # sanity

    # Score every variant with synthetic (perfect) plaque score, using the
    # smallest distance in the banner. The matching variant must win.
    target_distance = min(variant_distances.keys())
    target_variant = variant_distances[target_distance]

    best: ResolvedRace | None = None
    for variant in variants:
        resolved = matcher._score_variant(
            race=variant,
            banner_id=banner_id,
            plaque_score=0.90,
            plaque_rank=0,
            distance=target_distance,
            surface="turf",  # all known multi-variant banners are turf only
            direction="",
            venue="",
        )
        if resolved is None:
            continue
        if best is None or resolved.combined_confidence > best.combined_confidence:
            best = resolved

    assert best is not None
    assert best.race_id == int(target_variant["id"])
    assert best.distance == target_distance


def test_hard_reject_via_score_variant(matcher: PlaqueMatcher):
    """Directly exercise the hard-reject path: wrong distance -> None,
    wrong surface -> None, matching features -> ResolvedRace."""
    data = json.loads(RACES_JSON.read_text())
    # Pick any race with a known distance and terrain.
    race = next(r for r in data if r.get("distance") and r.get("terrain"))
    bid = int(race["banner_id"])
    good_distance = int(race["distance"])
    good_surface = {1: "turf", 2: "dirt"}[int(race["terrain"])]
    bad_surface = "dirt" if good_surface == "turf" else "turf"

    # Match -> ResolvedRace
    ok = matcher._score_variant(
        race=race,
        banner_id=bid,
        plaque_score=0.7,
        plaque_rank=0,
        distance=good_distance,
        surface=good_surface,
        direction="",
        venue="",
    )
    assert ok is not None
    assert ok.feature_score == pytest.approx(1.0, abs=1e-6)

    # Wrong distance -> None
    bad_dist = matcher._score_variant(
        race=race,
        banner_id=bid,
        plaque_score=0.7,
        plaque_rank=0,
        distance=good_distance + 400,
        surface=good_surface,
        direction="",
        venue="",
    )
    assert bad_dist is None

    # Wrong surface -> None
    bad_surf = matcher._score_variant(
        race=race,
        banner_id=bid,
        plaque_score=0.7,
        plaque_rank=0,
        distance=good_distance,
        surface=bad_surface,
        direction="",
        venue="",
    )
    assert bad_surf is None
