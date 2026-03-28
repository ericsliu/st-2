"""Tests for OCR gain number parsing."""

import pytest
from unittest.mock import patch, MagicMock
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.config import OCRConfig


@pytest.fixture
def ocr():
    engine = OCREngine(OCRConfig())
    engine._initialized = True
    engine._primary = MagicMock()
    return engine


def _mock_text(ocr, text):
    """Configure the OCR mock to return the given text."""
    ocr._primary.recognize.return_value = [(text, 0.9)]


class TestReadGainNumber:
    def test_correct_plus(self, ocr):
        _mock_text(ocr, "+8")
        assert ocr.read_gain_number(MagicMock()) == 8

    def test_correct_plus_two_digit(self, ocr):
        _mock_text(ocr, "+12")
        assert ocr.read_gain_number(MagicMock()) == 12

    def test_plus_misread_as_4(self, ocr):
        """'+5' misread as '45' — leading '4' is misread '+'."""
        _mock_text(ocr, "45")
        assert ocr.read_gain_number(MagicMock()) == 5

    def test_plus_misread_as_dollar(self, ocr):
        _mock_text(ocr, "$2")
        assert ocr.read_gain_number(MagicMock()) == 2

    def test_plus_misread_as_4_with_two_digit_gain(self, ocr):
        """'+12' misread as '412' — leading 4 stripped."""
        _mock_text(ocr, "412")
        assert ocr.read_gain_number(MagicMock()) == 12

    def test_fullwidth_plus(self, ocr):
        _mock_text(ocr, "＋9")
        assert ocr.read_gain_number(MagicMock()) == 9

    def test_bare_single_digit_rejected(self, ocr):
        """Bare single digit is rejected — too ambiguous (could be icon/decoration)."""
        _mock_text(ocr, "7")
        assert ocr.read_gain_number(MagicMock()) is None

    def test_bare_two_digit_accepted(self, ocr):
        """Bare two-digit number accepted — likely '+' was missed but value plausible."""
        _mock_text(ocr, "12")
        assert ocr.read_gain_number(MagicMock()) == 12

    def test_empty_returns_none(self, ocr):
        _mock_text(ocr, "")
        assert ocr.read_gain_number(MagicMock()) is None

    def test_no_digits_returns_none(self, ocr):
        _mock_text(ocr, "abc")
        assert ocr.read_gain_number(MagicMock()) is None

    def test_plus_misread_as_6(self, ocr):
        """'+9' misread as '69' — value > 50, strip leading digit."""
        _mock_text(ocr, "69")
        assert ocr.read_gain_number(MagicMock()) == 9

    def test_letter_digit_confusion(self, ocr):
        """'+10' misread as '+IC' — letter→digit substitution."""
        _mock_text(ocr, "+IC")
        assert ocr.read_gain_number(MagicMock()) == 10

    def test_plus_misread_as_4_large(self, ocr):
        """'+12' misread as '412' — value > 50, strip leading digit."""
        _mock_text(ocr, "412")
        assert ocr.read_gain_number(MagicMock()) == 12

    def test_trackblazer_icon_rejected(self, ocr):
        """Trackblazer 'T' icon misread as '1)' — single digit with noise, rejected."""
        _mock_text(ocr, "1)")
        assert ocr.read_gain_number(MagicMock()) is None


class TestBoostedGainParsing:
    """Item-boosted training shows two additive rows (+base +bonus)."""

    def test_two_plus_patterns_summed(self, ocr):
        """+13 +33 (Megaphone speed boost) → 46."""
        assert ocr._parse_gain_text("+13 +33") == 46

    def test_two_plus_patterns_small(self, ocr):
        """+9 +23 (Megaphone power boost) → 32."""
        assert ocr._parse_gain_text("+9 +23") == 32

    def test_single_plus_unchanged(self, ocr):
        """Single +N still works normally."""
        assert ocr._parse_gain_text("+17") == 17

    def test_fullwidth_plus_summed(self, ocr):
        """Fullwidth ＋ symbols also summed."""
        assert ocr._parse_gain_text("＋5 ＋12") == 17
