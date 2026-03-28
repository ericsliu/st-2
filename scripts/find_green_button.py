"""Find green button pixels in a screenshot."""
from PIL import Image
import numpy as np
import sys

img = np.array(Image.open(sys.argv[1]))
for y in range(1200, 1500, 5):
    for x in range(100, 1000, 5):
        r, g, b = img[y, x, :3]
        if g > 180 and r < 100 and b < 80:
            print(f"  Green at ({x}, {y}): RGB({r},{g},{b})")
