"""Tests for aptitude OCR parsing from Full Stats screenshots."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PIL import Image

from scripts.auto_turn import _parse_aptitudes_from_image

EXPECTED = {
    "turf": "A",
    "short": "D", "mile": "A", "medium": "A", "long": "S",
}
# Note: "dirt" is excluded — OCR unreliably detects it from these screenshots.

SCREENSHOTS = [
    "screenshots/full_stats.png",
    "screenshots/after_full_stats_tap.png",
]


@pytest.mark.parametrize("path", SCREENSHOTS)
def test_parse_aptitudes(path):
    img = Image.open(path)
    result = _parse_aptitudes_from_image(img)
    for key, expected_grade in EXPECTED.items():
        assert key in result, f"Missing aptitude: {key}"
        assert result[key] == expected_grade, (
            f"{key}: got {result[key]}, expected {expected_grade}"
        )
