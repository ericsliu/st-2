"""Test screen detection on current frame."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
import numpy as np
from PIL import Image

from uma_trainer.perception.regions import SCREEN_ANCHORS

result = subprocess.run(
    ["adb", "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
    capture_output=True, timeout=10,
)
img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
frame = np.array(img)[:, :, ::-1]  # RGB->BGR

print(f"Frame shape: {frame.shape}")

for anchor_set in SCREEN_ANCHORS:
    matches = 0
    details = []
    for anchor in anchor_set.anchors:
        b, g, r = frame[anchor.y, anchor.x]
        ok = anchor.matches(r, g, b)
        details.append(
            f"  ({anchor.x},{anchor.y}): R={r} G={g} B={b} "
            f"expect R[{anchor.r_min}-{anchor.r_max}] "
            f"G[{anchor.g_min}-{anchor.g_max}] "
            f"B[{anchor.b_min}-{anchor.b_max}] → {'MATCH' if ok else 'MISS'}"
        )
        if ok:
            matches += 1
    detected = matches >= anchor_set.min_matches
    if detected or matches > 0:
        print(f"\n{anchor_set.screen.value}: {matches}/{len(anchor_set.anchors)} matches (need {anchor_set.min_matches}) {'✓ DETECTED' if detected else ''}")
        for d in details:
            print(d)
