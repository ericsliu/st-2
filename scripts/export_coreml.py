#!/usr/bin/env python3
"""Export a trained YOLO .pt model to CoreML format for M1 GPU acceleration.

Usage:
    python scripts/export_coreml.py --model models/uma_trainer/weights/best.pt
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    parser = argparse.ArgumentParser(description="Export YOLO model to CoreML")
    parser.add_argument("--model", required=True, help="Path to .pt weights file")
    parser.add_argument("--imgsz", type=int, default=640, help="Input image size")
    parser.add_argument("--output", default="models/uma_yolo.mlpackage", help="Output path")
    args = parser.parse_args()

    model_path = Path(args.model)
    if not model_path.exists():
        print(f"Model not found: {model_path}")
        sys.exit(1)

    try:
        from ultralytics import YOLO
    except ImportError:
        print("ultralytics not installed: pip install ultralytics")
        sys.exit(1)

    print(f"Loading {model_path}...")
    model = YOLO(str(model_path))

    print(f"Exporting to CoreML (imgsz={args.imgsz})...")
    export_path = model.export(
        format="coreml",
        imgsz=args.imgsz,
        half=False,  # Full precision for accuracy
        nms=True,    # Include NMS in the CoreML model
    )

    print(f"\nExported to: {export_path}")

    # Move to the specified output location
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    if Path(export_path).exists():
        shutil.move(export_path, str(output))
        print(f"Moved to: {output}")

    print(f"\nUpdate config/default.yaml:")
    print(f"  yolo:")
    print(f"    model_path: {output}")


if __name__ == "__main__":
    main()
