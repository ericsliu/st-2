"""Tests for support card portrait tracking and bond state."""

import numpy as np
import pytest

from uma_trainer.perception.card_tracker import CardTracker


def _make_portrait(seed: int, h: int = 120, w: int = 120) -> np.ndarray:
    """Create a deterministic pseudo-random portrait (BGR)."""
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, (h, w, 3), dtype=np.uint8)


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
