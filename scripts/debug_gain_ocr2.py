"""Debug: show raw OCR output for each gain region on the current screen."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.regions import STAT_SELECTION_REGIONS
from uma_trainer.config import OCRConfig

ocr = OCREngine(OCRConfig())

src = sys.argv[1] if len(sys.argv) > 1 else None
if src:
    frame = cv2.imread(src)
else:
    from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
    cap = ScrcpyCapture()
    frame = cap.grab()

if frame is None:
    print("Cannot get frame")
    sys.exit(1)

h, w = frame.shape[:2]
print(f"Frame: {w}x{h}")

# Test both bar gains and panel gains
gain_keys = [
    ("speed", "gain_speed"),
    ("stamina", "gain_stamina"),
    ("power", "gain_power"),
    ("guts", "gain_guts"),
    ("wit", "gain_wit"),
]

print("\n=== Bar gain regions ===")
for stat, key in gain_keys:
    region = STAT_SELECTION_REGIONS.get(key)
    if not region:
        print(f"  {stat}: no region defined")
        continue
    x1, y1, x2, y2 = region
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        print(f"  {stat}: empty ROI")
        continue

    # Raw OCR on original
    raw_text = ocr.read_text(roi).strip()

    # Preprocessed (3x upscale)
    roi_up = cv2.resize(roi, (roi.shape[1] * 3, roi.shape[0] * 3), interpolation=cv2.INTER_LANCZOS4)
    up_text = ocr.read_text(roi_up).strip()

    # Gain parsing
    val = ocr.read_gain_region(frame, region)

    print(f"  {stat:8s}: region={region}  raw='{raw_text}'  upscaled='{up_text}'  parsed={val}")
    cv2.imwrite(f"/tmp/gain_{stat}.png", roi)
    cv2.imwrite(f"/tmp/gain_{stat}_up.png", roi_up)

print(f"\nSaved gain crops to /tmp/gain_*.png")
