"""Copy game digit sprites into data/digit_templates/ for template matching."""
import shutil
from pathlib import Path

SRC = Path("sprites/Umamusume UI sprites")
DST = Path("data/digit_templates")
DST.mkdir(parents=True, exist_ok=True)

# Normal (orange) digits and plus sign — these are the standard gain numbers
for i in range(10):
    src = SRC / f"utx_txt_trainingselect_num_{i:02d}.png"
    dst = DST / f"digit_{i}.png"
    shutil.copy2(src, dst)
    print(f"  {src.name} -> {dst.name}")

src = SRC / "utx_txt_trainingselect_plus_00.png"
dst = DST / "digit_plus.png"
shutil.copy2(src, dst)
print(f"  {src.name} -> {dst.name}")

# Limit (gold) variants
for i in range(10):
    src = SRC / f"utx_txt_trainingselect_num_limit_{i:02d}.png"
    dst = DST / f"digit_{i}_limit.png"
    shutil.copy2(src, dst)
    print(f"  {src.name} -> {dst.name}")

src = SRC / "utx_txt_trainingselect_plus_limit_00.png"
dst = DST / "digit_plus_limit.png"
shutil.copy2(src, dst)
print(f"  {src.name} -> {dst.name}")

# Max (red) variants
for i in range(10):
    src = SRC / f"utx_txt_trainingselect_num_max_{i:02d}.png"
    dst = DST / f"digit_{i}_max.png"
    shutil.copy2(src, dst)
    print(f"  {src.name} -> {dst.name}")

src = SRC / "utx_txt_trainingselect_plus_max_00.png"
dst = DST / "digit_plus_max.png"
shutil.copy2(src, dst)
print(f"  {src.name} -> {dst.name}")

print(f"\nDone: {len(list(DST.glob('digit_*.png')))} templates copied")
