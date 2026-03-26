#!/usr/bin/env python3
"""Collect labelled screenshots for screen anchor calibration.

Takes screenshots at a regular interval, labels each with a specified
screen type, samples all current anchor points, and writes results to
a JSON dataset.  After collection, analyse the dataset to compute
optimal RGB ranges for each anchor.

Usage:
    # Collect samples labelled as "main_menu", one every 2s for 30s:
    python scripts/sample_anchors.py main_menu

    # Faster interval, longer duration:
    python scripts/sample_anchors.py training --interval 1 --duration 60

    # Analyse collected samples and print recommended anchor ranges:
    python scripts/sample_anchors.py --analyse

    # Analyse a single screen type:
    python scripts/sample_anchors.py --analyse main_menu

    # Generate a regions.py snippet you can paste in:
    python scripts/sample_anchors.py --analyse --codegen
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image

from uma_trainer.perception.regions import SCREEN_ANCHORS
from uma_trainer.types import ScreenState

# Where samples are stored
SAMPLES_DIR = Path("data/anchor_samples")
SAMPLES_FILE = SAMPLES_DIR / "samples.jsonl"
SCREENSHOTS_DIR = SAMPLES_DIR / "screenshots"

# All screen types that can be labelled
VALID_SCREENS = [s.value for s in ScreenState]


def capture_screenshot(device: str) -> np.ndarray:
    """Capture a screenshot via ADB and return as RGB numpy array."""
    # Ensure ADB is connected
    subprocess.run(["adb", "connect", device], capture_output=True, timeout=5)

    result = subprocess.run(
        ["adb", "-s", device, "exec-out", "screencap", "-p"],
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ADB capture failed: {result.stderr.decode()[:200]}")
    img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
    return np.array(img)


def sample_anchor_points(frame_rgb: np.ndarray) -> list[dict]:
    """Sample RGB at every defined anchor point."""
    h, w = frame_rgb.shape[:2]
    results = []
    for anchor_set in SCREEN_ANCHORS:
        for anchor in anchor_set.anchors:
            x = min(max(anchor.x, 0), w - 1)
            y = min(max(anchor.y, 0), h - 1)
            r, g, b = int(frame_rgb[y, x, 0]), int(frame_rgb[y, x, 1]), int(frame_rgb[y, x, 2])
            results.append({
                "anchor_screen": anchor_set.screen.value,
                "x": anchor.x,
                "y": anchor.y,
                "r": r,
                "g": g,
                "b": b,
            })
    return results


def sample_grid(frame_rgb: np.ndarray, points: list[tuple[int, int]]) -> list[dict]:
    """Sample RGB at a list of arbitrary (x, y) points."""
    h, w = frame_rgb.shape[:2]
    results = []
    for x, y in points:
        x = min(max(x, 0), w - 1)
        y = min(max(y, 0), h - 1)
        r, g, b = int(frame_rgb[y, x, 0]), int(frame_rgb[y, x, 1]), int(frame_rgb[y, x, 2])
        results.append({"x": x, "y": y, "r": r, "g": g, "b": b})
    return results


def collect(
    screen_label: str,
    device: str,
    interval: float,
    duration: float,
    save_images: bool,
) -> None:
    """Collect labelled samples over a time window."""
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    if save_images:
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    end_time = time.monotonic() + duration
    count = 0
    print(f"Collecting '{screen_label}' samples every {interval}s for {duration}s...")
    print("Press Ctrl+C to stop early.\n")

    try:
        while time.monotonic() < end_time:
            t0 = time.monotonic()
            try:
                frame = capture_screenshot(device)
            except Exception as e:
                print(f"  Capture error: {e}")
                time.sleep(interval)
                continue

            anchor_samples = sample_anchor_points(frame)
            ts = datetime.now().isoformat()

            record = {
                "timestamp": ts,
                "screen_label": screen_label,
                "frame_size": [frame.shape[1], frame.shape[0]],
                "anchor_samples": anchor_samples,
            }

            # Save screenshot
            if save_images:
                img_name = f"{screen_label}_{count:04d}.png"
                Image.fromarray(frame).save(SCREENSHOTS_DIR / img_name)
                record["screenshot"] = img_name

            # Append to JSONL
            with open(SAMPLES_FILE, "a") as f:
                f.write(json.dumps(record) + "\n")

            count += 1
            elapsed = time.monotonic() - t0
            print(f"  [{count}] captured ({elapsed:.1f}s)")

            remaining = interval - elapsed
            if remaining > 0:
                time.sleep(remaining)

    except KeyboardInterrupt:
        print("\nStopped early.")

    print(f"\nCollected {count} samples → {SAMPLES_FILE}")


def load_samples(screen_filter: str | None = None) -> list[dict]:
    """Load all samples from the JSONL file."""
    if not SAMPLES_FILE.exists():
        return []
    samples = []
    for line in SAMPLES_FILE.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if screen_filter and record["screen_label"] != screen_filter:
            continue
        samples.append(record)
    return samples


def analyse(screen_filter: str | None = None, codegen: bool = False) -> None:
    """Analyse collected samples and recommend anchor RGB ranges."""
    samples = load_samples(screen_filter)
    if not samples:
        print("No samples found. Run collection first.")
        return

    # Group by screen label
    by_screen: dict[str, list[dict]] = {}
    for s in samples:
        label = s["screen_label"]
        by_screen.setdefault(label, []).append(s)

    print(f"=== Anchor Sample Analysis ({len(samples)} total samples) ===\n")

    for label, records in sorted(by_screen.items()):
        print(f"Screen: {label} ({len(records)} samples)")
        print("-" * 60)

        # Gather per-anchor-point stats
        # Key: (anchor_screen, x, y)
        point_data: dict[tuple[str, int, int], list[tuple[int, int, int]]] = {}
        for record in records:
            for ap in record["anchor_samples"]:
                key = (ap["anchor_screen"], ap["x"], ap["y"])
                point_data.setdefault(key, []).append((ap["r"], ap["g"], ap["b"]))

        # Also collect all unique (x, y) with their RGB values
        # for finding good NEW anchor candidates
        xy_data: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
        for record in records:
            for ap in record["anchor_samples"]:
                xy_data.setdefault((ap["x"], ap["y"]), []).append(
                    (ap["r"], ap["g"], ap["b"])
                )

        # Print stats for each anchor point
        codegen_lines: list[str] = []
        for (a_screen, x, y), values in sorted(point_data.items()):
            rs = [v[0] for v in values]
            gs = [v[1] for v in values]
            bs = [v[2] for v in values]
            r_min, r_max = min(rs), max(rs)
            g_min, g_max = min(gs), max(gs)
            b_min, b_max = min(bs), max(bs)
            r_mean = sum(rs) / len(rs)
            g_mean = sum(gs) / len(gs)
            b_mean = sum(bs) / len(bs)
            r_std = (sum((v - r_mean) ** 2 for v in rs) / len(rs)) ** 0.5
            g_std = (sum((v - g_mean) ** 2 for v in gs) / len(gs)) ** 0.5
            b_std = (sum((v - b_mean) ** 2 for v in bs) / len(bs)) ** 0.5
            spread = (r_max - r_min) + (g_max - g_min) + (b_max - b_min)

            # Is this a "own" anchor (defined for this screen) or "other"?
            own = a_screen == label
            marker = ">>>" if own else "   "
            stability = "STABLE" if spread < 60 else "NOISY" if spread < 150 else "UNSTABLE"

            print(
                f"  {marker} ({x:4d},{y:4d}) [{a_screen:12s}]  "
                f"R={r_min:3d}-{r_max:3d} (σ={r_std:4.1f})  "
                f"G={g_min:3d}-{g_max:3d} (σ={g_std:4.1f})  "
                f"B={b_min:3d}-{b_max:3d} (σ={b_std:4.1f})  "
                f"[{stability}]"
            )

            if own and codegen:
                # Add 10% padding to observed range
                pad = 15
                codegen_lines.append(
                    f"            PixelAnchor({x}, {y}, "
                    f"{max(0, r_min - pad)}, {min(255, r_max + pad)}, "
                    f"{max(0, g_min - pad)}, {min(255, g_max + pad)}, "
                    f"{max(0, b_min - pad)}, {min(255, b_max + pad)}),"
                )

        if codegen and codegen_lines:
            print(f"\n  Suggested anchors for {label}:")
            for line in codegen_lines:
                print(line)

        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect and analyse labelled screenshots for anchor calibration"
    )
    parser.add_argument(
        "screen",
        nargs="?",
        help=f"Screen label: {', '.join(VALID_SCREENS)}",
    )
    parser.add_argument(
        "--analyse", action="store_true",
        help="Analyse collected samples instead of collecting",
    )
    parser.add_argument(
        "--codegen", action="store_true",
        help="With --analyse, print PixelAnchor code snippets",
    )
    parser.add_argument(
        "--interval", type=float, default=2.0,
        help="Seconds between captures (default: 2)",
    )
    parser.add_argument(
        "--duration", type=float, default=30.0,
        help="Total collection duration in seconds (default: 30)",
    )
    parser.add_argument(
        "--device", "-d", default="127.0.0.1:5555",
        help="ADB device serial",
    )
    parser.add_argument(
        "--no-images", action="store_true",
        help="Skip saving screenshot PNGs (saves disk space)",
    )
    args = parser.parse_args()

    if args.analyse:
        analyse(screen_filter=args.screen, codegen=args.codegen)
        return

    if not args.screen:
        parser.error("screen label is required (or use --analyse)")
    if args.screen not in VALID_SCREENS:
        parser.error(f"Unknown screen '{args.screen}'. Valid: {', '.join(VALID_SCREENS)}")

    collect(
        screen_label=args.screen,
        device=args.device,
        interval=args.interval,
        duration=args.duration,
        save_images=not args.no_images,
    )


if __name__ == "__main__":
    main()
