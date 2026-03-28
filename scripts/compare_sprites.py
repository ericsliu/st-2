"""Compare extracted sprites against existing sprites directory."""
import os

EXTRACTED_DIR = "/tmp/uma_sprites_extracted"
EXISTING_DIR = "sprites/Umamusume UI sprites"

extracted = set(os.listdir(EXTRACTED_DIR))
existing = set(os.listdir(EXISTING_DIR))

# Normalize for comparison (existing may lack Sprite_/Texture2D_ prefix)
existing_basenames = set()
for f in existing:
    existing_basenames.add(f)
    # Strip prefix to compare
    for prefix in ("Sprite_", "Texture2D_"):
        if f.startswith(prefix):
            existing_basenames.add(f[len(prefix):])

new_sprites = []
for f in sorted(extracted):
    base = f
    for prefix in ("Sprite_", "Texture2D_"):
        if f.startswith(prefix):
            base = f[len(prefix):]
    if f not in existing and base not in existing_basenames:
        new_sprites.append(f)

print(f"Extracted total: {len(extracted)}")
print(f"Existing total: {len(existing)}")
print(f"New sprites not in existing dir: {len(new_sprites)}")
for name in new_sprites:
    print(" ", name)
