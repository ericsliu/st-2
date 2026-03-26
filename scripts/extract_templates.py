#!/usr/bin/env python3
"""Extract screen-identification templates from a screenshot.

Crops distinctive UI elements (icons, buttons, labels) from a screenshot
and saves them as template images for cv2.matchTemplate()-based screen
identification.

Usage:
    # Capture from ADB and extract all templates for a screen:
    python scripts/extract_templates.py main_menu

    # From an existing screenshot:
    python scripts/extract_templates.py main_menu --input /tmp/uma_screenshot.png

    # Interactive mode — click to define crop regions:
    python scripts/extract_templates.py main_menu --interactive

    # Extract a single region by pixel coords:
    python scripts/extract_templates.py main_menu --crop 430,1830,650,1910 --name home_tab

    # List all known template definitions:
    python scripts/extract_templates.py --list
"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image

from uma_trainer.types import ScreenState

TEMPLATE_DIR = Path("data/templates")

# Pre-defined crop regions for each screen type.
# Format: (name, x1, y1, x2, y2)
# These are the distinctive icons/buttons that identify each screen.
# Add or adjust entries as the UI is explored.
TEMPLATE_DEFS: dict[str, list[tuple[str, int, int, int, int]]] = {
    "main_menu": [
        ("home_tab",        430, 1830, 650, 1910),   # "Home" tab icon + text
        ("story_tab",       180, 1830, 350, 1910),   # "Story" book icon
        ("race_tab",        720, 1830, 890, 1910),   # "Race" gates icon
        ("menu_btn",        950, 30,  1060, 75),     # "Menu" button top-right
    ],
    "training": [
        ("rest_btn",        60,  1470, 310, 1580),   # Green "Rest" button
        ("training_btn",    370, 1470, 650, 1580),   # Blue "Training" button
        ("skills_btn",      700, 1470, 990, 1580),   # "Skills" button
        ("turn_counter",    15,  85,  155, 195),     # Turn number badge
    ],
    "stat_selection": [
        ("back_btn",        30,  1850, 200, 1900),   # "Back" button bottom-left
        ("turn_counter",    15,  85,  155, 195),     # Turn number badge
    ],
    "event": [
        # Events have a popup over a darkened background.
        # The choice buttons are distinctive.
        ("event_choice_1",  100, 1200, 980, 1300),   # First choice
    ],
    "race_entry": [
        ("back_btn",        30,  1850, 200, 1900),   # "Back" button
        ("header",          100, 30,   980, 80),      # "Race" header
        # Race list items will vary, but the header + back btn identify the screen
    ],
    "skill_shop": [
        # Needs a screenshot to define — placeholder
    ],
}


def capture_screenshot(device: str) -> np.ndarray:
    # Ensure ADB is connected
    subprocess.run(["adb", "connect", device], capture_output=True, timeout=5)

    result = subprocess.run(
        ["adb", "-s", device, "exec-out", "screencap", "-p"],
        capture_output=True, timeout=10,
    )
    if result.returncode != 0:
        print(f"ADB failed: {result.stderr.decode()[:200]}", file=sys.stderr)
        sys.exit(1)
    return np.array(Image.open(io.BytesIO(result.stdout)).convert("RGB"))


def extract_and_save(
    frame_rgb: np.ndarray,
    screen: str,
    regions: list[tuple[str, int, int, int, int]],
) -> None:
    out_dir = TEMPLATE_DIR / screen
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, x1, y1, x2, y2 in regions:
        crop = frame_rgb[y1:y2, x1:x2]
        if crop.size == 0:
            print(f"  SKIP {name}: empty crop ({x1},{y1},{x2},{y2})")
            continue
        path = out_dir / f"{name}.png"
        Image.fromarray(crop).save(path)
        print(f"  Saved {path} ({x2-x1}x{y2-y1}px)")


def list_templates() -> None:
    print("=== Template Definitions ===\n")
    for screen, regions in sorted(TEMPLATE_DEFS.items()):
        print(f"{screen}:")
        if not regions:
            print("  (none defined)")
        for name, x1, y1, x2, y2 in regions:
            existing = (TEMPLATE_DIR / screen / f"{name}.png").exists()
            status = "EXISTS" if existing else "missing"
            print(f"  {name:20s} ({x1},{y1})→({x2},{y2})  [{status}]")
        print()

    # Also show any templates on disk not in TEMPLATE_DEFS
    if TEMPLATE_DIR.exists():
        for screen_dir in sorted(TEMPLATE_DIR.iterdir()):
            if not screen_dir.is_dir():
                continue
            screen = screen_dir.name
            defined_names = {r[0] for r in TEMPLATE_DEFS.get(screen, [])}
            for img in sorted(screen_dir.glob("*.png")):
                if img.stem not in defined_names:
                    print(f"  {screen}/{img.name} (extra, not in TEMPLATE_DEFS)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract screen templates from screenshots")
    parser.add_argument("screen", nargs="?", help="Screen type to extract templates for")
    parser.add_argument("--input", "-i", help="Screenshot PNG path (default: capture from ADB)")
    parser.add_argument("--device", "-d", default="127.0.0.1:5555")
    parser.add_argument("--list", action="store_true", help="List all template definitions")
    parser.add_argument(
        "--crop", help="Manual crop region: x1,y1,x2,y2"
    )
    parser.add_argument("--name", help="Template name (with --crop)")
    args = parser.parse_args()

    if args.list:
        list_templates()
        return

    if not args.screen:
        print("Usage: extract_templates.py <screen> [--input FILE]\n")
        print("Available screens:")
        for name in sorted(TEMPLATE_DEFS):
            count = len(TEMPLATE_DEFS[name])
            print(f"  {name:20s} ({count} regions defined)")
        print(f"\nAll ScreenState values: {', '.join(s.value for s in ScreenState)}")
        print("\nExamples:")
        print("  python scripts/extract_templates.py main_menu")
        print("  python scripts/extract_templates.py training --input /tmp/uma_screenshot.png")
        print("  python scripts/extract_templates.py event --crop 100,300,980,700 --name event_text")
        print("\nUse --list to see which templates exist on disk.")
        sys.exit(0)

    # Load frame
    if args.input:
        frame = np.array(Image.open(args.input).convert("RGB"))
    else:
        frame = capture_screenshot(args.device)
        # Also save the raw screenshot
        raw_path = f"/tmp/uma_screenshot.png"
        Image.fromarray(frame).save(raw_path)
        print(f"Raw screenshot → {raw_path}")

    print(f"Frame: {frame.shape[1]}x{frame.shape[0]}\n")

    if args.crop:
        # Manual single-region extraction
        x1, y1, x2, y2 = (int(v) for v in args.crop.split(","))
        name = args.name or "custom"
        extract_and_save(frame, args.screen, [(name, x1, y1, x2, y2)])
    else:
        regions = TEMPLATE_DEFS.get(args.screen)
        if regions is None:
            print(f"No template definitions for '{args.screen}'.")
            print(f"Use --crop x1,y1,x2,y2 --name <name> to define one manually.")
            return
        if not regions:
            print(f"No regions defined yet for '{args.screen}'. Use --crop to add some.")
            return
        extract_and_save(frame, args.screen, regions)

    print(f"\nTemplates saved to {TEMPLATE_DIR / args.screen}/")


if __name__ == "__main__":
    main()
