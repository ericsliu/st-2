"""Tests for support card portrait tracking and bond state."""

import cv2
import numpy as np
import pytest

from uma_trainer.perception.card_tracker import CardTracker, _load_named_templates


def _make_portrait(seed: int, h: int = 128, w: int = 130) -> np.ndarray:
    """Create a deterministic pseudo-random portrait (BGR)."""
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, (h, w, 3), dtype=np.uint8)


def _make_rgba_portrait(seed: int, h: int = 128, w: int = 130) -> np.ndarray:
    """Create an RGBA portrait with transparent corners (named template format)."""
    raw = _make_portrait(seed, h, w)
    alpha = np.full((h, w), 255, dtype=np.uint8)
    # Make corners transparent (simulating background removal)
    cv2.circle(alpha, (w // 2, h // 2), min(w, h) // 2 - 2, 255, -1)
    b, g, r = cv2.split(raw)
    return cv2.merge([b, g, r, alpha])


def _make_frame(portraits: list[np.ndarray]) -> np.ndarray:
    """Build a 1920x1080 BGR frame with portraits placed at the expected slots."""
    from uma_trainer.perception.card_tracker import (
        BAR_Y_CENTERS, PORTRAIT_X1, PORTRAIT_X2, PORTRAIT_Y_OFFSET, PORTRAIT_Y_BOTTOM,
    )
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    for i, portrait in enumerate(portraits):
        bar_y = BAR_Y_CENTERS[i]
        y1 = bar_y - PORTRAIT_Y_OFFSET
        y2 = bar_y - PORTRAIT_Y_BOTTOM
        frame[y1:y2, PORTRAIT_X1:PORTRAIT_X2] = portrait
    return frame


class TestCardTracker:
    def test_registers_new_cards(self):
        tracker = CardTracker()
        p0 = _make_portrait(0)
        p1 = _make_portrait(1)
        frame = _make_frame([p0, p1])

        ids = tracker.identify_cards(frame, 2, [40, 60])
        assert len(ids) == 2
        assert ids[0] != ids[1]
        assert tracker.card_count == 2

    def test_recognizes_same_card_across_tiles(self):
        tracker = CardTracker()
        p0 = _make_portrait(0)
        p1 = _make_portrait(1)

        # First tile: cards 0 and 1
        frame1 = _make_frame([p0, p1])
        ids1 = tracker.identify_cards(frame1, 2, [40, 60])

        # Second tile: same card 0 appears alone
        frame2 = _make_frame([p0])
        ids2 = tracker.identify_cards(frame2, 1, [50])

        assert ids2[0] == ids1[0]
        assert tracker.card_count == 2  # No new registration

    def test_bond_updates_to_max(self):
        tracker = CardTracker()
        p0 = _make_portrait(0)

        frame = _make_frame([p0])
        tracker.identify_cards(frame, 1, [40])
        assert tracker.get_bond("card_0") == 40

        # Seen again with higher bond
        tracker.identify_cards(frame, 1, [60])
        assert tracker.get_bond("card_0") == 60

        # Seen with lower bond — should NOT decrease
        tracker.identify_cards(frame, 1, [20])
        assert tracker.get_bond("card_0") == 60

    def test_all_bonds_maxed_false_when_building(self):
        tracker = CardTracker()
        p0 = _make_portrait(0)
        p1 = _make_portrait(1)
        frame = _make_frame([p0, p1])

        tracker.identify_cards(frame, 2, [80, 40])
        assert not tracker.all_bonds_maxed()

    def test_all_bonds_maxed_true_when_all_hit_80(self):
        tracker = CardTracker()
        p0 = _make_portrait(0)
        p1 = _make_portrait(1)
        frame = _make_frame([p0, p1])

        tracker.identify_cards(frame, 2, [80, 80])
        assert tracker.all_bonds_maxed()

    def test_all_bonds_maxed_with_100(self):
        tracker = CardTracker()
        p0 = _make_portrait(0)
        frame = _make_frame([p0])

        tracker.identify_cards(frame, 1, [100])
        assert tracker.all_bonds_maxed()

    def test_all_bonds_maxed_false_when_empty(self):
        tracker = CardTracker()
        assert not tracker.all_bonds_maxed()

    def test_reset_clears_state(self):
        tracker = CardTracker()
        p0 = _make_portrait(0)
        frame = _make_frame([p0])

        tracker.identify_cards(frame, 1, [80])
        assert tracker.card_count == 1

        tracker.reset()
        assert tracker.card_count == 0
        assert not tracker.all_bonds_maxed()

    def test_summary_format(self):
        tracker = CardTracker()
        p0 = _make_portrait(0)
        p1 = _make_portrait(1)
        frame = _make_frame([p0, p1])

        tracker.identify_cards(frame, 2, [80, 40])
        summary = tracker.summary()
        assert "1/2 maxed" in summary
        assert "card_0=80%" in summary
        assert "card_1=40%" in summary


class TestNamedTemplates:
    """Test named card template matching (e.g., team_sirius, riko)."""

    def test_named_template_takes_priority(self, tmp_path, monkeypatch):
        """A portrait matching a named template gets that name, not card_N."""
        # Create a known portrait and save the circle-masked version as template
        portrait = _make_portrait(42)
        masked = _make_rgba_portrait(42)
        template_dir = tmp_path / "card_templates"
        template_dir.mkdir()
        cv2.imwrite(str(template_dir / "team_sirius.png"), masked)

        # Point the loader at our temp dir and clear cache
        import uma_trainer.perception.card_tracker as ct_mod
        monkeypatch.setattr(ct_mod, "_CARD_TEMPLATE_DIR", template_dir)
        if hasattr(_load_named_templates, "_cache"):
            delattr(_load_named_templates, "_cache")

        tracker = CardTracker()
        frame = _make_frame([portrait])
        ids = tracker.identify_cards(frame, 1, [60])

        assert ids[0] == "team_sirius"
        assert tracker.get_bond("team_sirius") == 60

    def test_named_and_unnamed_coexist(self, tmp_path, monkeypatch):
        """Named template matches coexist with runtime-registered cards."""
        sirius_portrait = _make_portrait(42)
        sirius_masked = _make_rgba_portrait(42)
        unknown_portrait = _make_portrait(99)

        template_dir = tmp_path / "card_templates"
        template_dir.mkdir()
        cv2.imwrite(str(template_dir / "team_sirius.png"), sirius_masked)

        import uma_trainer.perception.card_tracker as ct_mod
        monkeypatch.setattr(ct_mod, "_CARD_TEMPLATE_DIR", template_dir)
        if hasattr(_load_named_templates, "_cache"):
            delattr(_load_named_templates, "_cache")

        tracker = CardTracker()
        frame = _make_frame([sirius_portrait, unknown_portrait])
        ids = tracker.identify_cards(frame, 2, [60, 40])

        assert ids[0] == "team_sirius"
        assert ids[1] == "card_0"  # Runtime-registered
        assert tracker.get_bond("team_sirius") == 60
        assert tracker.get_bond("card_0") == 40

    def test_has_friendship(self, tmp_path, monkeypatch):
        """has_friendship returns True when named card bond >= 80."""
        portrait = _make_portrait(42)
        masked = _make_rgba_portrait(42)
        template_dir = tmp_path / "card_templates"
        template_dir.mkdir()
        cv2.imwrite(str(template_dir / "team_sirius.png"), masked)

        import uma_trainer.perception.card_tracker as ct_mod
        monkeypatch.setattr(ct_mod, "_CARD_TEMPLATE_DIR", template_dir)
        if hasattr(_load_named_templates, "_cache"):
            delattr(_load_named_templates, "_cache")

        tracker = CardTracker()
        frame = _make_frame([portrait])

        # Below friendship
        tracker.identify_cards(frame, 1, [60])
        assert not tracker.has_friendship("team_sirius")

        # At friendship
        tracker.identify_cards(frame, 1, [80])
        assert tracker.has_friendship("team_sirius")

    def test_is_tracked(self, tmp_path, monkeypatch):
        """is_tracked returns True only after the card has been seen."""
        portrait = _make_portrait(42)
        masked = _make_rgba_portrait(42)
        template_dir = tmp_path / "card_templates"
        template_dir.mkdir()
        cv2.imwrite(str(template_dir / "team_sirius.png"), masked)

        import uma_trainer.perception.card_tracker as ct_mod
        monkeypatch.setattr(ct_mod, "_CARD_TEMPLATE_DIR", template_dir)
        if hasattr(_load_named_templates, "_cache"):
            delattr(_load_named_templates, "_cache")

        tracker = CardTracker()
        assert not tracker.is_tracked("team_sirius")

        frame = _make_frame([portrait])
        tracker.identify_cards(frame, 1, [40])
        assert tracker.is_tracked("team_sirius")

    def test_no_named_templates_falls_through(self, tmp_path, monkeypatch):
        """With empty template dir, cards get card_N ids as before."""
        template_dir = tmp_path / "card_templates"
        template_dir.mkdir()  # Empty dir

        import uma_trainer.perception.card_tracker as ct_mod
        monkeypatch.setattr(ct_mod, "_CARD_TEMPLATE_DIR", template_dir)
        if hasattr(_load_named_templates, "_cache"):
            delattr(_load_named_templates, "_cache")

        tracker = CardTracker()
        portrait = _make_portrait(42)
        frame = _make_frame([portrait])
        ids = tracker.identify_cards(frame, 1, [50])

        assert ids[0] == "card_0"
