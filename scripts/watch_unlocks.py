"""Live watcher for Sirius bond + Riko recreation unlock signatures.

Tails the latest packet capture session and logs changes to the fields
most likely to carry the unlock signal:

  - data.chara_info.chara_effect_id_array  (Pure Passion, Charming, ...)
  - data.chara_info.evaluation_info_array  (per-card bond + story_step)
  - data.unchecked_event_array              (firing event_ids)

Output: data/unlock_watch.log + stdout. Run alongside the career bot.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import msgpack

ROOT = Path(__file__).resolve().parent.parent
CAPTURES = ROOT / "data" / "packet_captures"
LOG = ROOT / "data" / "unlock_watch.log"
EFFECT_LOOKUP = ROOT / "data" / "chara_effect_lookup.json"

sys.path.insert(0, str(ROOT))
from uma_trainer.perception.carrotjuicer.state_adapter import CardRegistry  # noqa: E402
from uma_trainer.perception.carrotjuicer.card_semantic import (  # noqa: E402
    load_card_semantic_map,
)


def load_effect_lookup() -> dict[int, dict]:
    if not EFFECT_LOOKUP.exists():
        return {}
    raw = json.loads(EFFECT_LOOKUP.read_text())
    return {int(k): v for k, v in raw.items()}


def describe_effect(eid: int, lookup: dict[int, dict]) -> str:
    info = lookup.get(eid)
    if not info:
        return f"effect_id={eid} (unknown)"
    return f"effect_id={eid} ({info.get('name', '?')}, {info.get('polarity', '?')})"


def latest_session() -> Path | None:
    if not CAPTURES.exists():
        return None
    sessions = [p for p in CAPTURES.iterdir() if p.is_dir() and p.name.startswith("session_")]
    return max(sessions, key=lambda p: p.stat().st_mtime) if sessions else None


def load_index(index: Path, last_offset: int) -> tuple[list[dict], int]:
    if not index.exists():
        return [], last_offset
    rows = []
    with index.open("rb") as f:
        f.seek(last_offset)
        for raw in f:
            try:
                rows.append(json.loads(raw.decode("utf-8")))
            except Exception:
                continue
        new_offset = f.tell()
    return rows, new_offset


def decode(path: Path) -> dict | None:
    try:
        return msgpack.unpackb(path.read_bytes(), raw=False, strict_map_key=False)
    except Exception:
        return None


def emit(line: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    out = f"[{stamp}] {line}"
    print(out, flush=True)
    with LOG.open("a") as f:
        f.write(out + "\n")


def _refresh_partner_names(
    chara: dict,
    registry: CardRegistry,
    semantic_map: dict[int, str],
    cache: dict[int, str],
) -> None:
    """Rebuild target_id -> human label from the latest chara_info."""
    scenario_id = chara.get("scenario_id") or 0
    for sc in chara.get("support_card_array") or []:
        pos = sc.get("position")
        cid = sc.get("support_card_id") or 0
        if pos is None:
            continue
        if cid in semantic_map:
            cache[pos] = semantic_map[cid]
        else:
            try:
                cache[pos] = registry.support_card_name(cid) or f"card_{cid}"
            except Exception:
                cache[pos] = f"card_{cid}"
    for ev in chara.get("evaluation_info_array") or []:
        tid = ev.get("target_id")
        if tid is None or tid < 100 or tid in cache:
            continue
        try:
            partner = registry.scenario_partner(scenario_id, tid)
            cache[tid] = partner.name or f"npc_{tid}"
        except Exception:
            cache[tid] = f"npc_{tid}"


def label_for(tid: int, cache: dict[int, str]) -> str:
    name = cache.get(tid)
    return f"{name} (target_id={tid})" if name else f"target_id={tid}"


def main() -> int:
    seen_effect_ids: set[int] = set()
    seen_event_ids: set[int] = set()
    seen_bond_milestones: set[tuple[int, int]] = set()  # (target_id, milestone)
    seen_story_steps: dict[int, int] = {}                 # target_id -> last story_step
    partner_names: dict[int, str] = {}

    registry = CardRegistry(ROOT / "data" / "master.mdb")
    semantic_map = load_card_semantic_map()
    effect_lookup = load_effect_lookup()

    current_session: Path | None = None
    offset = 0
    BOND_MILESTONES = (40, 60, 80, 100)

    emit("watcher start — polling captures every 1.5s")
    while True:
        session = latest_session()
        if session is None:
            time.sleep(2.0)
            continue
        if session != current_session:
            emit(f"following session={session.name}")
            current_session = session
            offset = 0
            seen_effect_ids.clear()
            seen_event_ids.clear()
            seen_bond_milestones.clear()
            seen_story_steps.clear()
            partner_names.clear()

        index = session / "index.jsonl"
        rows, offset = load_index(index, offset)
        for row in rows:
            slot = row.get("slot", "")
            direction = row.get("dir", "")
            # decompress + out = decoded server response (plaintext msgpack)
            if "decompress" not in slot or direction != "out":
                continue
            fname = row.get("file") or ""
            if not fname:
                continue
            payload = decode(session / fname)
            if not payload or not isinstance(payload, dict):
                continue
            data = payload.get("data") or {}
            chara = data.get("chara_info") or {}
            _refresh_partner_names(chara, registry, semantic_map, partner_names)

            effect_ids = chara.get("chara_effect_id_array") or []
            for eid in effect_ids:
                if not isinstance(eid, int):
                    continue
                if eid not in seen_effect_ids:
                    seen_effect_ids.add(eid)
                    emit(f"NEW {describe_effect(eid, effect_lookup)} (full set: {sorted(seen_effect_ids)})")

            evals = chara.get("evaluation_info_array") or []
            for ev in evals:
                if not isinstance(ev, dict):
                    continue
                tid = ev.get("target_id")
                value = ev.get("evaluation", 0)
                step = ev.get("story_step", 0)
                if tid is None:
                    continue
                who = label_for(tid, partner_names)
                for m in BOND_MILESTONES:
                    if value >= m and (tid, m) not in seen_bond_milestones:
                        seen_bond_milestones.add((tid, m))
                        emit(f"BOND {who} crossed {m} (eval={value}, story_step={step})")
                prev = seen_story_steps.get(tid)
                if prev is None:
                    seen_story_steps[tid] = step
                elif step != prev:
                    seen_story_steps[tid] = step
                    emit(f"STORY_STEP {who}: {prev} -> {step}")

            for ev in (data.get("unchecked_event_array") or []):
                if not isinstance(ev, dict):
                    continue
                eid = ev.get("event_id")
                cid = ev.get("chara_id")
                sid = ev.get("story_id")
                if eid is None:
                    continue
                if eid not in seen_event_ids:
                    seen_event_ids.add(eid)
                    emit(f"NEW event_id={eid} chara_id={cid} story_id={sid}")
        time.sleep(1.5)


if __name__ == "__main__":
    sys.exit(main() or 0)
