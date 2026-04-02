"""Helper for manual career run-through — screenshot + basic analysis."""

import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

DEVICE = "127.0.0.1:5555"
SCREENSHOTS_DIR = Path("screenshots/run_log")
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def adb(cmd: str) -> str:
    result = subprocess.run(
        ["adb", "-s", DEVICE] + cmd.split(),
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip()


def tap(x: int, y: int, delay: float = 2.0):
    adb(f"shell input tap {x} {y}")
    time.sleep(delay)


TARGET_W, TARGET_H = 1080, 1920


def screenshot(name: str) -> Image.Image:
    path = SCREENSHOTS_DIR / f"{name}.png"
    subprocess.run(
        ["adb", "-s", DEVICE, "exec-out", "screencap", "-p"],
        stdout=open(path, "wb"), timeout=10,
    )
    img = Image.open(path)
    if img.size != (TARGET_W, TARGET_H):
        img = img.resize((TARGET_W, TARGET_H), Image.LANCZOS)
        img.save(path)
    return img


def detect_screen(img: Image.Image) -> str:
    """Basic screen detection by sampling key pixel regions."""
    w, h = img.size

    # Check title region (top-left ~20-200, y ~15-40)
    # Sample colors to distinguish screens
    title_pixels = []
    for x in range(20, 200, 10):
        for y in range(10, 45, 5):
            title_pixels.append(img.getpixel((x, y))[:3])

    # Check for specific title background colors
    # Career home: "Career" title with green/dark background
    # Training: "Training" title
    # Race List: "Race List" title
    # Learn: "Learn" title (skills)
    # Shop: "Shop" title

    # Sample the title bar area for text-like dark pixels
    title_text_area = img.crop((0, 0, 250, 50))

    # Check for action buttons (career home has them at y=1400-1700)
    has_training_btn = False
    for x in range(350, 600, 10):
        r, g, b = img.getpixel((x, 1540))[:3]
        if 30 < b < 230 and b > r:  # Blue-ish (Training button)
            has_training_btn = True
            break

    # Check for tile bubbles (training screen has them at y=1650-1700)
    has_tile_bubbles = False
    tile_colors = 0
    for x in range(100, 1000, 50):
        r, g, b = img.getpixel((x, 1660))[:3]
        if (r + g + b) > 400:  # Bright colored circles
            tile_colors += 1
    has_tile_bubbles = tile_colors >= 3

    # Check for event choices (speech bubbles y=1000-1400)
    has_choices = False
    white_bands = 0
    for y in range(1000, 1500, 50):
        whites = sum(1 for x in range(100, 900, 30)
                     if all(c > 230 for c in img.getpixel((x, y))[:3]))
        if whites > 10:
            white_bands += 1
    has_choices = white_bands >= 2

    # Check for race list entries
    has_race_rows = False
    for y in range(900, 1400, 50):
        # Race rows have distinctive borders
        border_pixels = sum(1 for x in range(50, 1030, 20)
                           if all(c > 200 for c in img.getpixel((x, y))[:3]))
        if border_pixels > 20:
            has_race_rows = True
            break

    # Check first few pixels of title for screen name
    # "Career" = career home, etc.
    px_10_20 = img.getpixel((10, 20))[:3]
    px_50_20 = img.getpixel((50, 20))[:3]

    if has_tile_bubbles:
        return "training"
    if has_training_btn:
        return "career_home"
    if has_choices:
        return "event"
    if has_race_rows:
        return "race_list"

    # Check for skill/shop by looking for "Skill Points" or "Shop Coins"
    # These have distinctive green bars
    for y in range(280, 380, 10):
        greens = sum(1 for x in range(300, 700, 10)
                     if img.getpixel((x, y))[1] > 180 and img.getpixel((x, y))[0] < 100)
        if greens > 5:
            # Could be skills or shop - check for item rows vs skill descriptions
            return "skills_or_shop"

    return "unknown"


def get_energy_pct(img: Image.Image) -> int:
    """Estimate energy from the green bar."""
    # Energy bar spans roughly x=190-520, y=245-260
    green_count = 0
    total = 0
    for x in range(190, 520, 5):
        r, g, b = img.getpixel((x, 250))[:3]
        total += 1
        if g > 150 and r < 150:
            green_count += 1
    return int(100 * green_count / max(total, 1))


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else f"frame_{int(time.time())}"
    img = screenshot(name)
    screen = detect_screen(img)
    energy = get_energy_pct(img)
    print(f"Screen: {screen}")
    print(f"Energy: ~{energy}%")
    print(f"Saved: {SCREENSHOTS_DIR / name}.png")
