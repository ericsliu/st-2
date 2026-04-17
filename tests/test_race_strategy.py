"""Tests for running strategy selection logic."""

import pytest
import sys
import os

# auto_turn.py is a script, not a package — import the function directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from auto_turn import _desired_strategy


class TestDesiredStrategy:
    """Test _desired_strategy(turn, distance) logic."""

    # --- Junior Year: always Pace Chaser ---

    def test_junior_mile_race(self):
        assert _desired_strategy(turn=5, distance=1600) == "pace"

    def test_junior_medium_race(self):
        assert _desired_strategy(turn=10, distance=2000) == "pace"

    def test_junior_long_race(self):
        assert _desired_strategy(turn=20, distance=2500) == "pace"

    def test_junior_last_turn(self):
        assert _desired_strategy(turn=24, distance=2400) == "pace"

    def test_junior_unknown_distance(self):
        assert _desired_strategy(turn=15, distance=0) == "pace"

    # --- Classic/Senior Mile: Pace Chaser ---

    def test_classic_mile_1600(self):
        assert _desired_strategy(turn=30, distance=1600) == "pace"

    def test_classic_mile_1800(self):
        assert _desired_strategy(turn=35, distance=1800) == "pace"

    def test_senior_mile(self):
        assert _desired_strategy(turn=55, distance=1600) == "pace"

    # --- Classic pre-summer (turns 25-40): still Pace/Front ---

    def test_classic_medium_2000(self):
        assert _desired_strategy(turn=30, distance=2000) == "pace"

    def test_classic_medium_2400(self):
        assert _desired_strategy(turn=40, distance=2400) == "pace"

    def test_classic_long(self):
        assert _desired_strategy(turn=35, distance=2500) == "pace"

    # --- Post-Classic summer (turns 41+): End Closer for medium+ ---

    def test_senior_medium(self):
        assert _desired_strategy(turn=55, distance=2000) == "end"

    def test_senior_long(self):
        assert _desired_strategy(turn=60, distance=3200) == "end"

    def test_post_summer_medium(self):
        """Turn 41 is first post-summer turn — medium race should be End Closer."""
        assert _desired_strategy(turn=41, distance=2000) == "end"

    # --- Edge cases ---

    def test_classic_first_turn(self):
        """Turn 25 is early Classic — still pace."""
        assert _desired_strategy(turn=25, distance=2000) == "pace"

    def test_classic_sprint(self):
        """Sprint (<=1400m) should be Pace Chaser."""
        assert _desired_strategy(turn=30, distance=1200) == "pace"

    def test_unknown_distance_classic(self):
        """Unknown distance in early Classic defaults to pace."""
        assert _desired_strategy(turn=30, distance=0) == "pace"

    def test_unknown_distance_senior(self):
        assert _desired_strategy(turn=50, distance=0) == "end"
