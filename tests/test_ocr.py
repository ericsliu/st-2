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
        """'+5' misread as '45' — most common misread."""
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

    def test_plain_digit_fallback(self, ocr):
        _mock_text(ocr, "7")
        assert ocr.read_gain_number(MagicMock()) == 7

    def test_empty_returns_none(self, ocr):
        _mock_text(ocr, "")
        assert ocr.read_gain_number(MagicMock()) is None

    def test_no_digits_returns_none(self, ocr):
        _mock_text(ocr, "abc")
        assert ocr.read_gain_number(MagicMock()) is None
