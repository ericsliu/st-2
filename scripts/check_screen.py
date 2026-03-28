"""Take a screenshot and identify the current screen."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.career_helper import screenshot, detect_screen, get_energy_pct

img = screenshot("current_state")
screen = detect_screen(img)
energy = get_energy_pct(img)
print(f"Screen: {screen}")
print(f"Energy: ~{energy}%")
print(f"Size: {img.size}")
