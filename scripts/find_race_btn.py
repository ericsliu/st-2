"""Find the Race! button on race day screen."""
from PIL import Image

img = Image.open("/tmp/race_day.png")

# The Race! button is a large pink/magenta button in the center-bottom area
# Scan for pink pixels
print("Vertical scan x=540:")
for y in range(1500, 1850, 10):
    r, g, b = img.getpixel((540, y))[:3]
    is_pink = r > 150 and b > 100 and g < 100
    print(f"  (540, {y}): RGB=({r},{g},{b}) {'PINK' if is_pink else ''}")

print("\nVertical scan x=700:")
for y in range(1500, 1850, 10):
    r, g, b = img.getpixel((700, y))[:3]
    is_pink = r > 150 and b > 100 and g < 100
    print(f"  (700, {y}): RGB=({r},{g},{b}) {'PINK' if is_pink else ''}")
