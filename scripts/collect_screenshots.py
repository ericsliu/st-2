#!/usr/bin/env python3
"""Interactive screenshot collection tool for building the YOLO training dataset.

Usage:
    python scripts/collect_screenshots.py --output datasets/images --interval 5

Controls (while running):
    Enter  — save the current frame immediately
    q      — quit
"""

import argparse
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from uma_trainer.capture import get_capture_backend
from uma_trainer.config import load_config


def main():
    parser = argparse.ArgumentParser(description="Collect screenshots for YOLO training data")
    parser.add_argument("--config", default="config/default.yaml", help="Config file path")
    parser.add_argument("--output", default="datasets/images", help="Output directory")
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Auto-capture interval in seconds (0 = manual only)",
    )
    parser.add_argument("--count", type=int, default=0, help="Max frames to capture (0 = unlimited)")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(args.config)
    capture = get_capture_backend(config.capture)

    print(f"Starting screenshot collection → {output_dir}")
    print("Press Enter to capture a frame, 'q' + Enter to quit")
    print(f"Auto-capture interval: {args.interval}s (0 = disabled)")

    capture.start()
    count = 0
    last_auto = time.monotonic()

    import threading
    input_queue = []
    input_lock = threading.Lock()

    def input_thread():
        while True:
            line = input()
            with input_lock:
                input_queue.append(line.strip())

    t = threading.Thread(target=input_thread, daemon=True)
    t.start()

    try:
        while True:
            should_capture = False

            # Check for manual input
            with input_lock:
                if input_queue:
                    cmd = input_queue.pop(0)
                    if cmd.lower() == "q":
                        break
                    should_capture = True

            # Auto-capture
            if args.interval > 0 and (time.monotonic() - last_auto) >= args.interval:
                should_capture = True
                last_auto = time.monotonic()

            if should_capture:
                try:
                    frame = capture.grab_frame()
                    import cv2
                    filename = output_dir / f"frame_{count:05d}_{int(time.time())}.png"
                    cv2.imwrite(str(filename), frame)
                    print(f"  Saved: {filename.name} ({frame.shape[1]}×{frame.shape[0]})")
                    count += 1
                    if args.count > 0 and count >= args.count:
                        print(f"Reached target count ({args.count}). Stopping.")
                        break
                except Exception as e:
                    print(f"  Error capturing frame: {e}")

            time.sleep(0.1)

    except KeyboardInterrupt:
        pass
    finally:
        capture.stop()

    print(f"\nCollection complete. {count} frames saved to {output_dir}")
    print(f"\nNext step: annotate images in Label Studio")
    print(f"  See datasets/README.md for Label Studio setup instructions")


if __name__ == "__main__":
    main()
