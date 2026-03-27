"""Measure the exact pixel location of UI elements in a screenshot."""

import sys
from pathlib import Path
from PIL import Image

img_path = sys.argv[1] if len(sys.argv) > 1 else "screenshots/debug_gains/after_cancel.png"
img = Image.open(img_path)
w, h = img.size
print(f"Image size: {w}x{h}")

# Scan for the Cancel button text - look for white button with dark text
# The Cancel button should be in the lower portion of the dialog
# Scan horizontal lines looking for the green "Race" button boundary
# Green buttons have high G, low R/B

print("\nScanning for green button (Race) row:")
for y in range(h // 2, h, 10):
    green_count = 0
    for x in range(w // 2, w - 50, 5):
        r, g, b = img.getpixel((x, y))[:3]
        if g > 160 and g > r + 30 and g > b + 30:
            green_count += 1
    if green_count > 10:
        # Find the bounds
        xs = []
        for x in range(0, w, 2):
            r, g, b = img.getpixel((x, y))[:3]
            if g > 160 and g > r + 30 and g > b + 30:
                xs.append(x)
        if xs:
            print(f"  y={y}: green x=[{min(xs)}..{max(xs)}], count={len(xs)}")

print("\nScanning for white button (Cancel) row:")
for y in range(h // 2, h, 10):
    white_count = 0
    for x in range(50, w // 2, 5):
        r, g, b = img.getpixel((x, y))[:3]
        if r > 230 and g > 230 and b > 230:
            white_count += 1
    if white_count > 10:
        xs = []
        for x in range(0, w, 2):
            r, g, b = img.getpixel((x, y))[:3]
            if r > 230 and g > 230 and b > 230:
                xs.append(x)
        if xs:
            left_cluster = [x for x in xs if x < w // 2]
            if left_cluster:
                print(f"  y={y}: white x=[{min(left_cluster)}..{max(left_cluster)}], count={len(left_cluster)}")

# Also scan for the green header bar "Insufficient Goal Race Result Pts"
print("\nScanning for green header bar:")
for y in range(0, h // 2, 5):
    green_count = 0
    for x in range(100, w - 100, 10):
        r, g, b = img.getpixel((x, y))[:3]
        if g > 180 and g > r and b < 50:
            green_count += 1
    if green_count > 20:
        print(f"  y={y}: green header ({green_count} green pixels)")
