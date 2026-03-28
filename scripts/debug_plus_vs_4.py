"""Test the +/digit disambiguation fix on saved gain samples."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
from uma_trainer.perception.template_digits import TemplateDigitReader
import logging

logging.basicConfig(level=logging.DEBUG, format="%(name)-40s %(levelname)-5s %(message)s")

reader = TemplateDigitReader()
reader._ensure_loaded()

samples = [
    (7, "data/gain_ocr_samples/1774662944807_175_1185.png", "+7 misread as 47"),
    (7, "data/gain_ocr_samples/1774662940501_175_1185.png", "+7 correct"),
    (3, "data/gain_ocr_samples/1774663753315_20_1185.png", "+3 misread as 37"),
]

for expected, path, desc in samples:
    img = cv2.imread(path)
    if img is None:
        print(f"  MISSING: {path}")
        continue

    # Show glyph details
    glyphs = reader._segment_glyphs(img)
    print(f"\n--- {desc} ({path}) ---")
    print(f"  {len(glyphs)} glyphs found:")
    for i, (x, y, w, h, mask) in enumerate(glyphs):
        label, score = reader._match_glyph(mask)
        plus_score = reader._match_glyph_against(mask, "+")
        print(f"    glyph {i}: x={x} w={w} h={h} best='{label}' iou={score:.3f}, '+' iou={plus_score:.3f}")

    result = reader.read_gain(img)
    status = "OK" if result == expected else "FAIL"
    print(f"  [{status}] expected={expected}, got={result}")
