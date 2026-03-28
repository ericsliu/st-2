"""Debug why gain_ocr_samples crops fail template matching."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
from uma_trainer.perception.template_digits import TemplateDigitReader

reader = TemplateDigitReader()
reader._ensure_loaded()

# Test the +11 crop
crop = cv2.imread("data/gain_ocr_samples/1774657070255_175_1185.png")
print(f"+11 crop: {crop.shape[1]}x{crop.shape[0]}")

# Check orange mask
orange = reader._get_orange_mask(crop)
print(f"Orange pixels: {np.count_nonzero(orange)}")

# Segment
glyphs = reader._segment_glyphs(crop)
print(f"Glyphs found: {len(glyphs)}")
for i, (x, y, w, h, mask) in enumerate(glyphs):
    label, score = reader._match_glyph(mask)
    print(f"  glyph {i}: x={x} size={w}x{h} -> '{label}' (IoU={score:.3f})")
    cv2.imwrite(f"/tmp/crop11_glyph_{i}.png", mask)

# Save orange mask
cv2.imwrite("/tmp/crop11_orange.png", orange)

# Also the +5 crop
print()
crop5 = cv2.imread("data/gain_ocr_samples/1774657071547_510_1185.png")
print(f"+5 crop: {crop5.shape[1]}x{crop5.shape[0]}")
orange5 = reader._get_orange_mask(crop5)
print(f"Orange pixels: {np.count_nonzero(orange5)}")
glyphs5 = reader._segment_glyphs(crop5)
print(f"Glyphs found: {len(glyphs5)}")
for i, (x, y, w, h, mask) in enumerate(glyphs5):
    label, score = reader._match_glyph(mask)
    print(f"  glyph {i}: x={x} size={w}x{h} -> '{label}' (IoU={score:.3f})")
    cv2.imwrite(f"/tmp/crop5_glyph_{i}.png", mask)
cv2.imwrite("/tmp/crop5_orange.png", orange5)
