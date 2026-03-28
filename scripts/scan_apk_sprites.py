"""Scan APK asset bundles for Texture2D/Sprite assets using UnityPy."""
import UnityPy
import os

BUNDLE_DIR = "/tmp/uma_assets/assets/bin/Data"

files = sorted(os.listdir(BUNDLE_DIR))
print(f"Total bundles: {len(files)}")

found = []
for f in files:
    env = UnityPy.load(os.path.join(BUNDLE_DIR, f))
    for obj in env.objects:
        if obj.type.name in ("Texture2D", "Sprite"):
            try:
                data = obj.read()
                found.append((f, obj.type.name, getattr(data, "name", "?")))
            except Exception as e:
                found.append((f, obj.type.name, f"ERR:{e}"))

print(f"\nTotal Texture2D/Sprite assets: {len(found)}")
for item in found[:100]:
    print(item)
if len(found) > 100:
    print(f"... and {len(found) - 100} more")
