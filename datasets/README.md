# Datasets Directory

YOLO training data lives here. Images and labels are not committed to git.

## Directory structure (after setup)

```
datasets/
├── images/
│   ├── train/     # ~80% of images
│   ├── val/       # ~15% of images
│   └── test/      # ~5% of images
├── labels/        # YOLO format .txt files (same names as images)
│   ├── train/
│   ├── val/
│   └── test/
└── uma.yaml       # Dataset config for YOLO training
```

## Annotation workflow

### 1. Install Label Studio

```bash
pip install label-studio
label-studio start
```

Open http://localhost:8080

### 2. Create a project

- Project type: **Object Detection with Bounding Boxes**
- Import images from `datasets/images/`
- Label interface: add all 50 class names from `uma_trainer/perception/class_map.py`

### 3. Annotate

Target: **500–1000 images** covering all screen types:
- Training screen (all 5 tiles visible)
- Event popup (with choices)
- Skill shop
- Race screens
- Main menu / loading

Tips:
- Capture ~100 images per screen type
- Ensure variety: different moods, energy levels, support card combinations
- Rainbow/gold/hint tiles are rare — capture specifically

### 4. Export

Export format: **YOLO** (creates `images/` and `labels/` structure)

### 5. Split into train/val/test

```bash
# Quick manual split — put 80% in train, 15% in val, 5% in test
```

### 6. Train

```bash
python scripts/train_yolo.py --epochs 100 --export-coreml
```
