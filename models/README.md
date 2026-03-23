# Models Directory

Place trained model files here. They are not committed to git (too large).

## Required files

| File | Size | How to get it |
|------|------|---------------|
| `uma_yolo.mlpackage` | ~6 MB | Train YOLO then export to CoreML (see below) |

## Training the YOLO model

1. Collect screenshots:
   ```bash
   python main.py collect --output datasets/images --interval 5
   ```

2. Annotate in Label Studio (see `datasets/README.md`)

3. Train:
   ```bash
   python scripts/train_yolo.py --epochs 100 --export-coreml
   ```

4. The CoreML model is saved to `models/uma_yolo.mlpackage` automatically.

## During development (before model is trained)

The bot runs in **stub mode** — YOLO returns no detections, and the bot makes
decisions based on fallback fixed-region OCR. Most functionality works except
YOLO-dependent features like tile indicator detection.
