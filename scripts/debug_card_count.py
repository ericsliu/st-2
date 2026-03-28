"""Debug: measure edge density for each tile's support_cards region."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
from PIL import Image

from uma_trainer.perception.regions import TRAINING_TILES

src = sys.argv[1] if len(sys.argv) > 1 else "runs/hishi_amazon_20260327/screen_004_validate.png"
frame = cv2.imread(src)
if frame is None:
    print(f"Cannot read {src}")
    sys.exit(1)

stat_names = ["speed", "stamina", "power", "guts", "wit"]
out_dir = Path(src).parent

for i, tile in enumerate(TRAINING_TILES):
    x1, y1, x2, y2 = tile.support_cards
    roi = frame[y1:y2, x1:x2]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 80, 200)
    edge_ratio = float(np.mean(edges > 0))

    # Save the crop and edges for visual inspection
    cv2.imwrite(str(out_dir / f"cards_{stat_names[i]}_roi.png"), roi)
    cv2.imwrite(str(out_dir / f"cards_{stat_names[i]}_edges.png"), edges)

    # Updated thresholds (2026-03-27)
    from uma_trainer.perception.pixel_analysis import count_support_cards
    actual_count = count_support_cards(frame, tile.support_cards)

    print(f"{stat_names[i]:8s}: edge_ratio={edge_ratio:.3f} -> count={actual_count}")
