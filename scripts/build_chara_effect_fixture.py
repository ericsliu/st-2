"""Synthesize a chara_effect test fixture by mutating
``home_response_turn21.msgpack``.

Step 5 Path B from ``effect-id-lookup-drop-fullstats.md``: we have no live
captures with a non-empty ``chara_effect_id_array``, so build one by
decoding the existing turn-21 home fixture, injecting a representative mix
of effect ids (negative + positive + dedupe-able), and re-packing.

The mix is chosen to exercise:
  - 1 (Night Owl, negative) — bot cure target
  - 5 (Migraine, negative) — bot cure target
  - 8 (Charming, positive) — bond bonus
  - 100 (Pure Passion: Team Sirius, positive) — Sirius bond unlock signal
  - 14 (Fan Promise Hokkaido, neutral) — verifies neutral is dropped
  - 9999 (unknown id) — verifies unknown is silently skipped
"""
from __future__ import annotations

from pathlib import Path

import msgpack


SOURCE = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "home_response_turn21.msgpack"
)
TARGET = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "chara_effect_synthetic.msgpack"
)

INJECT_IDS = [1, 5, 8, 100, 14, 9999]


def main() -> None:
    response = msgpack.unpackb(
        SOURCE.read_bytes(), raw=False, strict_map_key=False
    )
    data = response.get("data") if isinstance(response, dict) and "data" in response else response
    if not isinstance(data, dict):
        raise SystemExit(f"Source fixture missing data dict: {SOURCE}")
    chara = data.get("chara_info")
    if not isinstance(chara, dict):
        raise SystemExit(
            f"Source fixture missing chara_info dict: {SOURCE}"
        )

    chara["chara_effect_id_array"] = list(INJECT_IDS)

    payload = msgpack.packb(response, use_bin_type=True)
    TARGET.write_bytes(payload)
    print(f"Wrote {TARGET} ({TARGET.stat().st_size} bytes) ids={INJECT_IDS}")


if __name__ == "__main__":
    main()
