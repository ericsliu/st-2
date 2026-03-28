"""Check what's actually in the guts gain region of stat_selection.png."""
import cv2
import numpy as np

frame = cv2.imread("screenshots/debug_gains/stat_selection.png")
# guts: (510, 1185, 700, 1255)
region = frame[1185:1255, 510:700]
cv2.imwrite("/tmp/guts_region_raw.png", region)

hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
orange = cv2.inRange(hsv, (3, 40, 100), (40, 255, 255))
cv2.imwrite("/tmp/guts_region_orange.png", orange)
print(f"Guts region: {region.shape[1]}x{region.shape[0]}")
print(f"Orange pixels: {np.count_nonzero(orange)}")

# Also check what the actual stat_selection screenshot shows
# The screenshot shows "Speed Lv.3 Exercise Bike" selected
# So only speed gains (+13) would be visible, not guts gains
print("\nNote: stat_selection.png has Speed tile selected.")
print("Only speed gains (+13) should be visible.")
print("Guts gains are only shown when a tile affecting guts is previewed.")
