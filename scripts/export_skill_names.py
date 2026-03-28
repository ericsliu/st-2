"""Export all skill names + descriptions from master.mdb to data/skills.json."""
import json
import sqlite3

c = sqlite3.connect("data/master.mdb")

# Join skill_data with text_data for names (cat=47) and descriptions (cat=48)
rows = c.execute("""
    SELECT sd.id, tn.text AS name, td.text AS description,
           sd.rarity, sd.skill_category, sd.grade_value
    FROM skill_data sd
    JOIN text_data tn ON tn.`index` = sd.id AND tn.category = 47
    LEFT JOIN text_data td ON td.`index` = sd.id AND td.category = 48
    ORDER BY sd.skill_category, sd.grade_value DESC
""").fetchall()

# Map skill_category numbers to readable names
CAT_MAP = {1: "wit", 2: "speed", 3: "power", 4: "stamina", 5: "unique", 6: "guts"}
# Rarity: 1=white, 2=gold, 3=unique, 4=evolved, 5=special
RARITY_MAP = {1: "normal", 2: "rare", 3: "unique", 4: "evolved", 5: "special"}

skills = []
for sid, name, desc, rarity, scat, grade_value in rows:
    skills.append({
        "skill_id": str(sid),
        "name": name,
        "description": desc or "",
        "category": CAT_MAP.get(scat, "unknown"),
        "rarity": RARITY_MAP.get(rarity, "unknown"),
        "grade_value": grade_value,
    })

out_path = "data/skills.json"
with open(out_path, "w") as f:
    json.dump(skills, f, indent=2, ensure_ascii=False)

print(f"Exported {len(skills)} skills to {out_path}")

# Print some stats
by_cat = {}
for s in skills:
    by_cat.setdefault(s["category"], 0)
    by_cat[s["category"]] += 1
print(f"By category: {by_cat}")

by_rarity = {}
for s in skills:
    by_rarity.setdefault(s["rarity"], 0)
    by_rarity[s["rarity"]] += 1
print(f"By rarity: {by_rarity}")
