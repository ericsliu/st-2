"""Extract actual on-screen digit appearances from known screenshots.

From stat_selection.png we know speed region has "+13".
Let's look at the actual pixel data to understand the rendering.
"""
import cv2
import numpy as np

frame = cv2.imread("screenshots/debug_gains/stat_selection.png")

# Speed gain region: (20, 1185, 200, 1255) — contains "+13"
region = frame[1185:1255, 20:200]
cv2.imwrite("/tmp/onscreen_speed_region.png", region)

# Let's look at just the digit area more precisely
# The "+13" appears to be roughly in the center-left of the region
# Let's look at a few vertical slices to find the digit boundaries
h, w = region.shape[:2]
print(f"Region: {w}x{h}")

# Convert to HSV and find orange pixels
hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
orange = cv2.inRange(hsv, (5, 60, 130), (35, 255, 255))

# Find columns with orange pixels (digit columns)
col_sums = np.sum(orange > 0, axis=0)
print(f"\nOrange pixel column sums (nonzero columns):")
for x in range(w):
    if col_sums[x] > 0:
        print(f"  x={x}: {col_sums[x]} orange pixels")

# Find rows with orange pixels
row_sums = np.sum(orange > 0, axis=1)
print(f"\nOrange pixel row sums (nonzero rows):")
for y in range(h):
    if row_sums[y] > 0:
        print(f"  y={y}: {row_sums[y]} orange pixels")

cv2.imwrite("/tmp/onscreen_speed_orange.png", orange)

# Also check the +11 crop
crop = cv2.imread("data/gain_ocr_samples/1774657070255_175_1185.png")
if crop is not None:
    hsv2 = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    orange2 = cv2.inRange(hsv2, (5, 60, 130), (35, 255, 255))
    col_sums2 = np.sum(orange2 > 0, axis=0)
    print(f"\n+11 crop orange columns (nonzero):")
    for x in range(crop.shape[1]):
        if col_sums2[x] > 0:
            print(f"  x={x}: {col_sums2[x]}")
    cv2.imwrite("/tmp/onscreen_plus11_orange.png", orange2)
