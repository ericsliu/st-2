#!/usr/bin/env python3
"""Calibration tool for the fixed-coordinate region map.

Captures a screenshot from the emulator (or loads one from disk),
overlays all defined regions as labelled rectangles, and optionally
runs OCR on each region to verify accuracy.

Usage:
    # Capture live from ADB, save raw screenshot, and annotate:
    python scripts/calibrate_regions.py

    # Load an existing screenshot:
    python scripts/calibrate_regions.py --input /tmp/uma_screenshot.png

    # Also run OCR on each region:
    python scripts/calibrate_regions.py --ocr

    # Sample pixel colours at specific points:
    python scripts/calibrate_regions.py --sample 540,960 100,1530

    # Sample anchor points to verify screen identification:
    python scripts/calibrate_regions.py --anchors
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Ensure project root is on sys.path so `uma_trainer` is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def capture_screenshot(device: str = "127.0.0.1:5555") -> np.ndarray:
    """Capture a screenshot via ADB. Auto-connects if needed."""
    # Ensure ADB is connected to the device
    subprocess.run(
        ["adb", "connect", device],
        capture_output=True,
        timeout=5,
    )

    result = subprocess.run(
        ["adb", "-s", device, "exec-out", "screencap", "-p"],
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"ADB capture failed: {result.stderr.decode()}", file=sys.stderr)
        sys.exit(1)

    img = Image.open(__import__("io").BytesIO(result.stdout))
    return np.array(img.convert("RGB"))


def draw_regions(
    img: Image.Image,
    regions: dict[str, tuple[int, int, int, int]],
    colour: str = "red",
    label_prefix: str = "",
) -> None:
    """Draw labelled rectangles on an image."""
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 14)
    except (OSError, IOError):
        font = ImageFont.load_default()

    for name, (x1, y1, x2, y2) in regions.items():
        draw.rectangle([x1, y1, x2, y2], outline=colour, width=2)
        label = f"{label_prefix}{name}"
        draw.text((x1 + 2, y1 - 16), label, fill=colour, font=font)


def draw_tiles(
    img: Image.Image,
    tiles: list,
    colour: str = "lime",
) -> None:
    """Draw training tile regions."""
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 14)
    except (OSError, IOError):
        font = ImageFont.load_default()

    from uma_trainer.perception.regions import TILE_INDEX_TO_STAT

    for i, tile in enumerate(tiles):
        stat = TILE_INDEX_TO_STAT[i].value
        for attr, col in [
            ("tap_target", "lime"),
            ("label", "cyan"),
            ("indicator", "magenta"),
            ("support_cards", "yellow"),
        ]:
            x1, y1, x2, y2 = getattr(tile, attr)
            draw.rectangle([x1, y1, x2, y2], outline=col, width=2)
            draw.text((x1 + 2, y1 - 14), f"{stat}.{attr}", fill=col, font=font)


def sample_pixels(
    frame: np.ndarray,
    points: list[tuple[int, int]],
) -> None:
    """Print RGB values at specified pixel coordinates."""
    print("\n=== Pixel Samples ===")
    for x, y in points:
        h, w = frame.shape[:2]
        if 0 <= x < w and 0 <= y < h:
            r, g, b = frame[y, x, :3]
            print(f"  ({x:4d}, {y:4d}) → R={r:3d}  G={g:3d}  B={b:3d}")
        else:
            print(f"  ({x:4d}, {y:4d}) → OUT OF BOUNDS ({w}×{h})")


def run_ocr_on_regions(
    frame_bgr: np.ndarray,
    regions: dict[str, tuple[int, int, int, int]],
    label: str = "",
) -> None:
    """Run OCR on each region and print results."""
    from uma_trainer.config import OCRConfig
    from uma_trainer.perception.ocr import OCREngine

    ocr = OCREngine(OCRConfig())
    print(f"\n=== OCR Results{' (' + label + ')' if label else ''} ===")
    for name, (x1, y1, x2, y2) in regions.items():
        text = ocr.read_region(frame_bgr, (x1, y1, x2, y2))
        if text.strip():
            print(f"  {name:25s} → {text!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate region map coordinates")
    parser.add_argument("--input", "-i", help="Path to screenshot PNG (skip ADB capture)")
    parser.add_argument("--device", "-d", default="127.0.0.1:5555", help="ADB device serial")
    parser.add_argument("--output", "-o", default="/tmp/uma_calibrated.png", help="Output annotated path")
    parser.add_argument("--raw", default="/tmp/uma_screenshot.png", help="Save raw screenshot to this path")
    parser.add_argument("--ocr", action="store_true", help="Run OCR on each region")
    parser.add_argument("--sample", nargs="*", help="Pixel coords to sample (x,y)")
    parser.add_argument("--anchors", action="store_true", help="Sample all screen anchor points")
    parser.add_argument(
        "--screen",
        choices=["turn_action", "stat_select", "event", "all"],
        default="all",
        help="Which region set to overlay",
    )
    args = parser.parse_args()

    # Load or capture frame
    if args.input:
        img_rgb = np.array(Image.open(args.input).convert("RGB"))
    else:
        img_rgb = capture_screenshot(args.device)
        # Save the raw screenshot so it can be re-used with --input
        raw_path = args.raw
        Image.fromarray(img_rgb).save(raw_path)
        print(f"Raw screenshot saved to: {raw_path}")

    print(f"Frame size: {img_rgb.shape[1]}×{img_rgb.shape[0]}")

    # Convert to BGR for OCR (OpenCV format)
    frame_bgr = img_rgb[:, :, ::-1].copy()

    # Draw regions on a PIL image
    pil_img = Image.fromarray(img_rgb)

    from uma_trainer.perception.regions import (
        EVENT_REGIONS,
        STAT_SELECTION_REGIONS,
        TRAINING_TILES,
        TURN_ACTION_REGIONS,
    )

    if args.screen in ("turn_action", "all"):
        draw_regions(pil_img, TURN_ACTION_REGIONS, colour="red", label_prefix="ta.")
    if args.screen in ("stat_select", "all"):
        draw_regions(pil_img, STAT_SELECTION_REGIONS, colour="blue", label_prefix="ss.")
        draw_tiles(pil_img, TRAINING_TILES)
    if args.screen in ("event", "all"):
        draw_regions(pil_img, EVENT_REGIONS, colour="orange", label_prefix="ev.")

    # Save annotated image
    pil_img.save(args.output)
    print(f"Annotated image saved to: {args.output}")

    # Sample pixels
    if args.sample:
        points = []
        for s in args.sample:
            x, y = s.split(",")
            points.append((int(x), int(y)))
        sample_pixels(img_rgb, points)

    # Test screen identification (templates + pixel anchor fallback)
    if args.anchors:
        from uma_trainer.perception.screen_identifier import ScreenIdentifier
        from uma_trainer.perception.ocr import OCREngine
        from uma_trainer.config import OCRConfig

        ocr = OCREngine(OCRConfig())
        sid = ScreenIdentifier(ocr=ocr)
        result, details = sid.identify_with_details(frame_bgr)

        if details:
            print("\n=== Template Matching ===")
            for screen_name, tmpl_results in details.items():
                print(f"  {screen_name}:")
                for t in tmpl_results:
                    status = "HIT" if t["matched"] else "MISS"
                    loc = f" at {t['location']}" if t["location"] else ""
                    print(f"    {t['name']:20s} conf={t['confidence']:.3f} → {status}{loc}")
                print()
        else:
            print("\n  (No templates loaded — using pixel anchor fallback)")
            # Show pixel anchor details
            from uma_trainer.perception.regions import SCREEN_ANCHORS
            print("\n=== Pixel Anchor Analysis ===")
            for anchor_set in SCREEN_ANCHORS:
                matches = 0
                total = len(anchor_set.anchors)
                for anchor in anchor_set.anchors:
                    x = min(max(anchor.x, 0), img_rgb.shape[1] - 1)
                    y = min(max(anchor.y, 0), img_rgb.shape[0] - 1)
                    r, g, b = img_rgb[y, x, :3]
                    hit = anchor.matches(int(r), int(g), int(b), tolerance=30)
                    matches += hit
                    status = "HIT" if hit else "MISS"
                    print(
                        f"  [{anchor_set.screen.value:12s}] ({x:4d},{y:4d}) "
                        f"R={r:3d} G={g:3d} B={b:3d}  → {status}"
                    )
                matched = matches >= anchor_set.min_matches
                print(
                    f"  → {anchor_set.screen.value}: {matches}/{total} "
                    f"(need {anchor_set.min_matches}) — "
                    f"{'MATCHED' if matched else 'no match'}\n"
                )

        is_stat = sid.is_stat_selection(frame_bgr) if result.value == "training" else False
        print(f"  ScreenIdentifier result: {result.value}"
              f"{' (stat selection)' if is_stat else ''}")

    # Run OCR
    if args.ocr:
        if args.screen in ("turn_action", "all"):
            run_ocr_on_regions(frame_bgr, TURN_ACTION_REGIONS, "turn action")
        if args.screen in ("stat_select", "all"):
            run_ocr_on_regions(frame_bgr, STAT_SELECTION_REGIONS, "stat select")
        if args.screen in ("event", "all"):
            run_ocr_on_regions(frame_bgr, EVENT_REGIONS, "event")


if __name__ == "__main__":
    main()
