"""Extract Texture2D/Sprite assets from APK bundles using UnityPy."""
import UnityPy
import os

BUNDLE_DIR = "/tmp/uma_assets/assets/bin/Data"
OUT_DIR = "/tmp/uma_sprites_extracted"
os.makedirs(OUT_DIR, exist_ok=True)

files = sorted(os.listdir(BUNDLE_DIR))
exported = 0
errors = 0

for f in files:
    env = UnityPy.load(os.path.join(BUNDLE_DIR, f))
    for obj in env.objects:
        if obj.type.name not in ("Texture2D", "Sprite"):
            continue
        try:
            data = obj.read()
            # Get name from multiple possible attributes
            name = getattr(data, "name", None) or getattr(data, "m_Name", None) or f"unknown_{exported}"
            name = name.strip() or f"unnamed_{exported}"
            # Clean name for filesystem
            safe_name = name.replace("/", "_").replace("\\", "_")
            out_path = os.path.join(OUT_DIR, f"{obj.type.name}_{safe_name}.png")
            # Export image
            if hasattr(data, "image"):
                img = data.image
                img.save(out_path)
                exported += 1
            elif hasattr(data, "get_image"):
                img = data.get_image()
                img.save(out_path)
                exported += 1
        except Exception as e:
            errors += 1

print(f"Exported: {exported}, Errors: {errors}")
print(f"Output dir: {OUT_DIR}")
exported_files = os.listdir(OUT_DIR)
print(f"Files created: {len(exported_files)}")
for name in sorted(exported_files)[:50]:
    print(" ", name)
