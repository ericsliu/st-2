#!/usr/bin/env python3
"""Train the YOLO model on collected and annotated screenshots.

Usage:
    python scripts/train_yolo.py --data datasets/uma.yaml --epochs 100

Prerequisites:
    1. Collect screenshots: python scripts/collect_screenshots.py
    2. Annotate in Label Studio (see datasets/README.md)
    3. Export annotations in YOLO format to datasets/labels/
    4. Create datasets/uma.yaml (see below)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


SAMPLE_DATASET_YAML = """
# datasets/uma.yaml — YOLO dataset configuration
# Edit paths to match your actual annotation export location

path: ../datasets          # Dataset root (relative to this yaml)
train: images/train
val: images/val
test: images/test          # Optional

# Number of classes must match uma_trainer/perception/class_map.py NUM_CLASSES
nc: 50

# Class names — must match CLASS_NAMES in class_map.py (same order)
names:
  0: btn_confirm
  1: btn_cancel
  2: btn_train_speed
  3: btn_train_stamina
  4: btn_train_power
  5: btn_train_guts
  6: btn_train_wit
  7: indicator_rainbow
  8: indicator_gold
  9: indicator_hint
  10: indicator_director
  11: mood_great
  12: mood_good
  13: mood_normal
  14: mood_bad
  15: mood_terrible
  16: screen_training
  17: screen_event
  18: screen_race
  19: screen_skill_shop
  20: screen_result
  21: screen_loading
  22: stat_box_speed
  23: stat_box_stamina
  24: stat_box_power
  25: stat_box_guts
  26: stat_box_wit
  27: energy_bar
  28: support_card_slot_0
  29: support_card_slot_1
  30: support_card_slot_2
  31: support_card_slot_3
  32: support_card_slot_4
  33: support_card_slot_5
  34: btn_race_enter
  35: btn_race_skip
  36: skill_card
  37: btn_buy_skill
  38: btn_skip_skills
  39: skill_cost_display
  40: skill_name_text
  41: event_popup
  42: event_choice_0
  43: event_choice_1
  44: event_choice_2
  45: goal_incomplete
  46: goal_complete
  47: goal_text
  48: turn_counter
  49: btn_rest
"""


def create_dataset_yaml_if_missing(yaml_path: Path) -> None:
    if not yaml_path.exists():
        yaml_path.write_text(SAMPLE_DATASET_YAML.strip())
        print(f"Created sample dataset yaml: {yaml_path}")
        print("Edit it to match your annotation export paths before training.")


def main():
    parser = argparse.ArgumentParser(description="Train YOLO on Uma Musume screenshots")
    parser.add_argument("--data", default="datasets/uma.yaml", help="Dataset YAML path")
    parser.add_argument("--model", default="yolo11n.pt", help="Base model (yolo11n/yolov8n)")
    parser.add_argument("--epochs", type=int, default=100, help="Training epochs")
    parser.add_argument("--imgsz", type=int, default=640, help="Image size")
    parser.add_argument("--batch", type=int, default=16, help="Batch size")
    parser.add_argument("--output", default="models", help="Output directory for trained weights")
    parser.add_argument("--export-coreml", action="store_true", help="Export to CoreML after training")
    args = parser.parse_args()

    data_yaml = Path(args.data)
    create_dataset_yaml_if_missing(data_yaml)

    if not data_yaml.exists():
        print(f"Dataset YAML not found: {data_yaml}")
        print("Please create it first (see the sample above).")
        sys.exit(1)

    try:
        from ultralytics import YOLO
    except ImportError:
        print("ultralytics not installed: pip install ultralytics")
        sys.exit(1)

    print(f"Training YOLO: model={args.model}, epochs={args.epochs}, data={data_yaml}")
    model = YOLO(args.model)

    results = model.train(
        data=str(data_yaml),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device="mps",         # M1 GPU
        project=args.output,
        name="uma_trainer",
        save=True,
        patience=20,          # Early stopping
        augment=True,
    )

    print(f"\nTraining complete!")
    print(f"Best weights: {results.save_dir}/weights/best.pt")

    if args.export_coreml:
        print("\nExporting to CoreML...")
        best = YOLO(f"{results.save_dir}/weights/best.pt")
        best.export(format="coreml", imgsz=args.imgsz)
        print(f"CoreML model saved to {results.save_dir}/weights/best.mlpackage")
        print(f"\nCopy it to models/uma_yolo.mlpackage and update config/default.yaml")


if __name__ == "__main__":
    main()
