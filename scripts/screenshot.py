"""Take a screenshot at emulator resolution (1080x1920) and save it."""
import subprocess
from PIL import Image
from pathlib import Path

DEVICE = "127.0.0.1:5555"
TARGET_W, TARGET_H = 1080, 1920
OUT = Path("screenshots/current.png")

result = subprocess.run(
    ["adb", "-s", DEVICE, "exec-out", "screencap", "-p"],
    stdout=subprocess.PIPE, timeout=10,
)
OUT.write_bytes(result.stdout)

img = Image.open(OUT)
w, h = img.size

if (w, h) != (TARGET_W, TARGET_H):
    img = img.resize((TARGET_W, TARGET_H), Image.LANCZOS)
    img.save(OUT)
    print(f"Saved {OUT} (rescaled {w}x{h} -> {TARGET_W}x{TARGET_H})")
else:
    print(f"Saved {OUT} ({w}x{h})")
