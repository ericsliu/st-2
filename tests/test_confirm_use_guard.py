"""Smoke tests for the Confirm Use grayed-out guard + idle-screen classifier."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _make_solid_image(rgb: tuple[int, int, int], size=(1080, 1920)) -> Image.Image:
    return Image.new("RGB", size, rgb)


def _patch_button_region(img: Image.Image, cx: int, cy: int, rgb: tuple[int, int, int]) -> Image.Image:
    out = img.copy()
    px = out.load()
    for dy in range(-30, 31):
        for dx in range(-80, 81):
            x = cx + dx
            y = cy + dy
            if 0 <= x < out.width and 0 <= y < out.height:
                px[x, y] = rgb
    return out


def test_is_button_active_detects_bright_green():
    from scripts.auto_turn import BTN_ITEMS_CONFIRM, is_button_active

    base = _make_solid_image((40, 40, 40))
    img = _patch_button_region(base, *BTN_ITEMS_CONFIRM, rgb=(60, 220, 90))
    assert is_button_active(img, *BTN_ITEMS_CONFIRM) is True


def test_is_button_active_rejects_grayed_button():
    from scripts.auto_turn import BTN_ITEMS_CONFIRM, is_button_active

    base = _make_solid_image((40, 40, 40))
    # Grayed-out CTA: green channel barely above red, fails (g - r) > 30
    img = _patch_button_region(base, *BTN_ITEMS_CONFIRM, rgb=(140, 155, 140))
    assert is_button_active(img, *BTN_ITEMS_CONFIRM) is False


def test_detect_screen_classifies_training_items_idle():
    from scripts import auto_turn

    fake_results = [
        ("Training Items", 0.99, 100),
        ("Confirm Use", 0.99, 1772),
        ("Close", 0.99, 1772),
        ("Vita 20", 0.99, 400),
    ]
    with patch.object(auto_turn, "ocr_full_screen", return_value=fake_results):
        screen = auto_turn.detect_screen(_make_solid_image((30, 30, 30)))
    assert screen == "training_items_idle"


def test_detect_screen_classifies_exchange_complete_idle():
    from scripts import auto_turn

    fake_results = [
        ("Exchange Complete", 0.99, 100),
        ("Choose how many to use.", 0.99, 200),
        ("Confirm Use", 0.99, 1772),
        ("Close", 0.99, 1772),
        ("Vita 20", 0.99, 400),
    ]
    with patch.object(auto_turn, "ocr_full_screen", return_value=fake_results):
        screen = auto_turn.detect_screen(_make_solid_image((30, 30, 30)))
    assert screen == "exchange_complete_idle"


def test_detect_screen_does_not_classify_other_screens_as_items():
    from scripts import auto_turn

    fake_results = [
        ("Career Golshi Mode", 0.99, 60),
        ("Training", 0.99, 1500),
        ("Races", 0.99, 1500),
        ("Rest", 0.99, 1500),
    ]
    with patch.object(auto_turn, "ocr_full_screen", return_value=fake_results):
        screen = auto_turn.detect_screen(_make_solid_image((30, 30, 30)))
    assert screen != "training_items_idle"
    assert screen != "exchange_complete_idle"
