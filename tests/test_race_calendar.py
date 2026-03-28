"""Tests for calendar-driven race pre-selection."""

import pytest
from uma_trainer.decision.race_selector import RaceSelector, GRADE_SORT_ORDER


class TestTurnToMonthHalf:
    def test_junior_jan_early(self):
        year, month, half = RaceSelector.turn_to_month_half(0)
        assert (year, month, half) == (1, 1, "early")

    def test_junior_jan_late(self):
        year, month, half = RaceSelector.turn_to_month_half(1)
        assert (year, month, half) == (1, 1, "late")

    def test_junior_dec_late(self):
        year, month, half = RaceSelector.turn_to_month_half(23)
        assert (year, month, half) == (1, 12, "late")

    def test_classic_jan_early(self):
        year, month, half = RaceSelector.turn_to_month_half(24)
        assert (year, month, half) == (2, 1, "early")

    def test_senior_aug_late(self):
        # Turn 63 = senior year, aug late
        year, month, half = RaceSelector.turn_to_month_half(63)
        assert (year, month, half) == (3, 8, "late")

    def test_senior_dec_late(self):
        year, month, half = RaceSelector.turn_to_month_half(71)
        assert (year, month, half) == (3, 12, "late")


class TestGradeSortOrder:
    def test_g1_before_g2(self):
        assert GRADE_SORT_ORDER["G1"] < GRADE_SORT_ORDER["G2"]

    def test_op_after_g3(self):
        assert GRADE_SORT_ORDER["OP"] > GRADE_SORT_ORDER["G3"]
