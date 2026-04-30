"""Generate ``data/chara_effect_lookup.json`` from master.mdb.

# Polarity table
#
# master.mdb has no polarity column for chara effects; polarity is encoded
# here as a hand-mapped table keyed on master.mdb effect_id (text_data
# category 142). Re-runnable when master.mdb refreshes — the script fails
# loud if a new effect appears that this table does not cover.
#
# Negative (keys must match scripts.auto_turn.CONDITION_CURES):
#   1   Night Owl                     -> "night owl"
#   2   Slacker                       -> "slacker"
#   3   Skin Outbreak                 -> "skin outbreak"
#   4   Slow Metabolism               -> "overweight"   (display: "Slow Metabolism";
#                                        bot key matches OCR fallback's "overweight")
#   5   Migraine                      -> "migraine"
#   6   Practice Poor                 -> "practice poor"
#   12  Under the Weather             -> "under the weather"   (no shop cure)
#   19  Not Ready                     -> "not ready"           (no cure)
#   20  Legs of Glass                 -> "legs of glass"       (no cure)
#
# Positive (keys must match scripts.auto_turn POSITIVE_KEYWORDS):
#   7   Fast Learner                  -> "fast learner"
#   8   Charming O                    -> "charming"
#   9   Hot Topic                     -> "hot topic"
#   10  Practice Perfect O            -> "practice perfect"
#   11  Practice Perfect @            -> "practice perfect"   (collapses to one key)
#   13  Shining Brightly              -> "shining brightly"
#   100 Pure Passion: Team Sirius     -> "pure passion"
#   101 Pure Passion: Heirs to Throne -> "pure passion"       (collapses to one key)
#
# Neutral (Fan Promise: geographic boost; not a buff or debuff by itself):
#   14-18 Fan Promise (Hokkaido / Hokuto / Nakayama / Kansai / Kokura)
#         -> "fan promise hokkaido" / "fan promise hokuto" /
#            "fan promise nakayama" / "fan promise kansai"   /
#            "fan promise kokura"   (polarity="neutral")
#
# Neutral entries appear in the JSON for reference, but the runtime adapter
# never places them on _active_conditions or _positive_statuses.

This script is idempotent: deterministic key-sorted JSON output, byte-stable
across runs.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MDB_PATH = REPO_ROOT / "data" / "master.mdb"
OUTPUT_PATH = REPO_ROOT / "data" / "chara_effect_lookup.json"


# effect_id -> (bot_key, polarity)
POLARITY_BY_ID: dict[int, tuple[str, str]] = {
    # negative
    1:   ("night owl",          "negative"),
    2:   ("slacker",             "negative"),
    3:   ("skin outbreak",       "negative"),
    4:   ("overweight",          "negative"),
    5:   ("migraine",            "negative"),
    6:   ("practice poor",       "negative"),
    12:  ("under the weather",   "negative"),
    19:  ("not ready",            "negative"),
    20:  ("legs of glass",        "negative"),
    # positive
    7:   ("fast learner",        "positive"),
    8:   ("charming",            "positive"),
    9:   ("hot topic",           "positive"),
    10:  ("practice perfect",    "positive"),
    11:  ("practice perfect",    "positive"),
    13:  ("shining brightly",    "positive"),
    100: ("pure passion",        "positive"),
    101: ("pure passion",        "positive"),
    # neutral (Fan Promise — geographic; never goes on the bot's lists)
    14:  ("fan promise hokkaido", "neutral"),
    15:  ("fan promise hokuto",   "neutral"),
    16:  ("fan promise nakayama", "neutral"),
    17:  ("fan promise kansai",   "neutral"),
    18:  ("fan promise kokura",   "neutral"),
}


def main() -> None:
    if not MDB_PATH.exists():
        raise SystemExit(f"master.mdb not found at {MDB_PATH}")

    conn = sqlite3.connect(f"file:{MDB_PATH}?mode=ro", uri=True)
    rows = conn.execute(
        'SELECT "index", text FROM text_data WHERE category=142 ORDER BY "index"'
    ).fetchall()

    # Defensive: every category-142 row MUST appear in POLARITY_BY_ID. A new
    # game patch could introduce a new effect; this fails loud rather than
    # silently dropping it.
    unmapped = [(idx, name) for idx, name in rows if idx not in POLARITY_BY_ID]
    if unmapped:
        msg = (
            "POLARITY_BY_ID is missing entries from master.mdb category 142:\n"
            + "\n".join(f"  id={idx}  name={name!r}" for idx, name in unmapped)
            + "\nUpdate scripts/extract_effect_ids.py before re-running."
        )
        raise SystemExit(msg)

    # Also confirm POLARITY_BY_ID doesn't reference ids that no longer exist.
    db_ids = {idx for idx, _name in rows}
    extras = sorted(set(POLARITY_BY_ID) - db_ids)
    if extras:
        msg = (
            "POLARITY_BY_ID references effect_ids not present in master.mdb "
            "category 142:\n"
            + "\n".join(f"  id={i}" for i in extras)
            + "\nMaster.mdb may have been refreshed; prune the dead entries."
        )
        raise SystemExit(msg)

    # Build deterministic JSON keyed by stringified effect_id (mirrors how
    # the carrotjuicer schema delivers ids — int — but JSON keys must be
    # strings; the loader converts back).
    name_by_id = {idx: name for idx, name in rows}
    out: dict[str, dict[str, str]] = {}
    for effect_id in sorted(POLARITY_BY_ID):
        key, polarity = POLARITY_BY_ID[effect_id]
        out[str(effect_id)] = {
            "key": key,
            "polarity": polarity,
            "name": name_by_id[effect_id],
        }

    payload = json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False)
    OUTPUT_PATH.write_text(payload + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} ({len(out)} entries)")


if __name__ == "__main__":
    main()
