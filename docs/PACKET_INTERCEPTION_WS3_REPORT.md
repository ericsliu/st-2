# WS-3: Packet Schema Design - Report

**Workstream**: WS-3 - Typed schema for decrypted CarrotJuicer msgpack packets.
**Date**: 2026-04-16
**Deliverable**: `uma_trainer/perception/carrotjuicer/schema/`

## 1. Research summary

This schema is reverse-engineered from three source communities:

### UmaLauncher (KevinVG207/UmaLauncher) - primary source

Python codebase. The modules that mattered most:

- `umalauncher/training_tracker.py` - wraps each captured msgpack dict,
  derives an `ActionType` by pattern-matching on the presence of keys
  (`single_mode_factor_select_common`, `race_start_info`,
  `unchecked_event_array`, `chara_info` etc.). This file was the
  single most valuable reference; its `determine_action_type` function
  is mirrored by our `detect_packet_kind`.
- `umalauncher/helper_table.py` - reads `chara_info.*` fields
  (speed, stamina, pow, guts, wiz, vital, motivation, fans,
  turn, skill_point, support_card_array, skill_array,
  reserved_race_array). This cemented the key names and types.
- `umalauncher/carrotjuicer.py` - the capture orchestrator. It stamps a
  `_direction` field (0=request, 1=response) onto every dict before
  forwarding. Also unwraps a top-level `data` key when present.
- `umalauncher/scenario.py` - scenario IDs, sidecar names
  (`venus_data_set`, `live_data_set`, `arc_data_set`, `sport_data_set`,
  `cook_data_set`, `mecha_data_set`, `team_data_set`).

### Hakuraku (SSHZ-ORG/hakuraku) - race simulator

TypeScript codebase. Relevant files:

- `src/lib/masters/race_simulate_horse_result_data.ts` - the per-horse
  result-blob shape (mapped to our `HorseResult`).
- `src/lib/masters/race_scenario_data.ts` - the binary `race_scenario`
  envelope; we leave this as a stub (`RaceScenarioDecoded`) because a
  Python port is out of scope for WS-3.

### cjedb (event database)

Used for event-id to name mapping. Informed the
`UncheckedEvent.is_skill_hint` / `hint_chara_id` helpers (story_id
`8XXXXX003` pattern).

### master.mdb (SQLite dump)

Not read live by this schema, but informed the enum values. Tables
consulted: `single_mode_scenario`, `command_info`, `skill_data`,
`support_card_data`.

## 2. Architecture of the schema package

Three-layer design:

```
                 +---------------------+
                 |    GamePacket       |  <- top-level typed envelope
                 |   (packets.py)      |
                 +---------+-----------+
                           |
          +----------------+----------------+
          |                                 |
+---------v--------+              +---------v----------+
|  CharaInfo /     |              |  UncheckedEvent /  |
|  HomeInfo /      |              |  RaceStartInfo /   |
|  Scenario*DataSet|              |  RaceRewardInfo    |
+---------+--------+              +---------+----------+
          |                                 |
+---------v-----------------------------v---+
| leaf dataclasses:                         |
|  SupportCardRef, SkillEntry,              |
|  CommandInfo, EventChoice,                |
|  RaceHorseData, ParamsIncDecInfo, ...     |
+-------------------------------------------+
          |
+---------v----------+
|   enums.py         |   ScenarioId, CommandId, Motivation, ...
+--------------------+
```

Every container has:

1. Field-by-field type annotations.
2. A docstring describing the field in game terms + confidence tier.
3. A `from_raw(d: dict)` classmethod that tolerates missing/renamed keys.
4. An `extras: dict` bag that captures every key the server sent that
   we did not explicitly type. This keeps Phase-2 live-capture
   auditing possible.

The top-level entry point is `parser.parse_packet(raw, direction)`,
which returns a `GamePacket` with the correct sub-blocks populated for
the detected `PacketKind`.

## 3. Known unknowns

These were not resolvable from static reading alone. Phase 2 live
capture must verify them:

### Uncertain msgpack key names

- `venus_spirit_active_effect_info_array` - UmaLauncher references but
  does not unpack the per-entry structure. Kept as `list` bag.
- `arc_info` - L'Arc scenario state; keys under it not documented.
- `selection_result_info` - L'Arc SS match result; only top-level name
  known.
- `single_mode_factor_select_common` - retirement bonus payload;
  structure unknown.
- `live_theater_save_info_array` - Grand Live concert result; unknown
  per-entry shape.

### Uncertain value ranges / meanings

- `CharaInfo.chara_effect_id_array` - definitely a list, but whether
  each entry is a bare int or `{effect_id, ...}` is unclear.
- `CommandInfo.level` - likely the training "rank" (1-5) but could be
  a different counter in scenario modes.
- `ParamsIncDecInfo.value` sign convention - seen both `+` (training)
  and `-` (fatigue) in docs, but not confirmed whether scenarios use a
  different sign.

### Binary blobs

- `race_scenario` - LZ4-compressed simulator state. Hakuraku parses
  this in TS using a home-grown reader. We have a placeholder
  `RaceScenarioDecoded` class. Decoding is deferred.

### Array-vs-int duality

UmaLauncher's code suggests several fields may appear as either a
dict or a bare int across endpoints:

- `training_partner_array` - sometimes `[id, id, ...]`, sometimes
  `[{id: ..., bond: ...}, ...]`. Our `TrainingPartnerRef.from_raw`
  handles both.
- `skill_array` - sometimes `[{skill_id, level}]`, sometimes `[id]`.
  `SkillEntry.from_raw` handles both.
- `tips_event_partner_array` - likely the same duality.

## 4. How WS-5 and WS-6 will consume

### WS-5 (capture pipeline)

WS-5 will own the hook -> msgpack unpack -> dict flow. Its outward API
should be:

```python
from uma_trainer.perception.carrotjuicer.schema import (
    parse_packet, PacketDirection,
)

def on_raw_dict(raw: dict, direction: PacketDirection):
    pkt = parse_packet(raw, direction=direction)
    # pkt.kind, pkt.chara_info, pkt.home_info, ... all typed
    dispatch(pkt)
```

WS-5 should emit only fully-typed `GamePacket` objects to downstream;
nothing downstream should ever see a raw dict. `pkt.raw` remains
available for logging/diagnostics but should not be the primary
consumption path.

### WS-6 (GameState API)

WS-6 builds a process-spanning `GameState` object. Its constructor /
update loop consumes `GamePacket` as follows:

```python
def update(self, pkt: GamePacket) -> None:
    if pkt.kind == PacketKind.TRAINING_HOME:
        self._apply_turn(pkt.chara_info, pkt.home_info)
    elif pkt.kind == PacketKind.TRAINING_SCENARIO_HOME:
        self._apply_turn(pkt.chara_info, pkt.home_info)
        self._apply_scenario_sidecar(pkt)
    elif pkt.kind == PacketKind.EVENT_TRIGGER:
        self.pending_event = pkt.unchecked_event_array[0]
    elif pkt.kind == PacketKind.RACE_START:
        self.current_race = pkt.race_start_info
    elif pkt.kind == PacketKind.RACE_RESULT:
        self._apply_race_result(pkt.race_reward_info)
    elif pkt.kind == PacketKind.RUN_ENDED:
        self.career_done = True
    # ...
```

WS-6 should **not** re-route on key presence (that is our job). It
should route on `pkt.kind` exclusively.

Legacy OCR-driven state (in `scripts/auto_turn.py`) can populate a
`GameState` from CarrotJuicer packets when available, falling back to
OCR when not. The point of WS-3 + WS-5 + WS-6 is that
`build_game_state` becomes a lookup, not a screen-scrape, for ~95% of
state.

## 5. Implementation notes / landmines

- **`start_chara` can be `{}` (empty dict) on a request**. Our
  `_detect_request_kind` uses `"start_chara" in raw` not
  `raw.get("start_chara")`. If you change that,
  `REQUEST_START_CHARA` will silently fall through to
  `REQUEST_GENERIC`.
- **Wisdom is `wiz` on the wire everywhere**, even though
  `COMMAND_ID_TO_STAT_KEY` maps to `wisdom` in our Python schema for
  downstream ergonomics. Beware when writing to the game.
- **Race horse data uses `pow` not `power`**. This is because the MDB
  field is `pow` and the server preserves it there specifically. We
  map it to our `power` attribute at parse time.
- **Events piggyback on training turns**. Check `unchecked_event_array`
  *before* assuming a packet with `chara_info` is a plain training
  turn. Our `detect_packet_kind` does this.
- **Nested race in Venus**. Grand Masters' goddess race payload nests
  `race_scenario` / `race_reward_info` inside `venus_data_set`. Our
  router handles this by peeking into the sidecar when the top-level
  keys are absent.

## 6. Verification against live capture

When Phase 2 live capture is available, the verification checklist is:

1. Dump 100+ packets of each kind. Confirm `detect_packet_kind`
   classifies every one correctly (no `UNKNOWN`).
2. For each dataclass, confirm `extras` is empty for all fields we
   claim to have typed. Any unexpected entries in `extras` indicate
   either a renamed key or a new field.
3. Verify value ranges:
   - `motivation` in [1, 5]
   - `vital` in [0, max_vital] with `max_vital` in [100, 200]
   - `command_id` drawn from our enum set
   - `result_rank` in [1, 18]
4. Confirm the `_direction` field is always stamped by the wrapper; if
   not, WS-5 must stamp it.
5. Round-trip a few REQUEST packets from our outgoing command codec
   (WS-7) through `parse_request` to confirm our `PacketKind` routing
   matches the wire format.

## 7. File manifest

| path | purpose |
|---|---|
| `schema/__init__.py` | Package re-exports |
| `schema/enums.py` | All enums + lookup dicts |
| `schema/career.py` | CharaInfo, ReservedRace |
| `schema/training_state.py` | HomeInfo, CommandInfo, ParamsIncDecInfo |
| `schema/support_cards.py` | SupportCardRef, EvaluationInfo, partners |
| `schema/events.py` | UncheckedEvent, EventChoice |
| `schema/skills.py` | SkillEntry, SkillHintEntry, purchase requests |
| `schema/race.py` | RaceStartInfo, RaceRewardInfo, HorseResult |
| `schema/scenario_data.py` | All 7 scenario sidecar dataclasses |
| `schema/packets.py` | PacketKind, GamePacket, detect_packet_kind |
| `schema/parser.py` | parse_packet orchestrator |
| `schema/README.md` | Schema usage + examples |

## 8. Out of scope for WS-3

- Actual msgpack decoding (WS-2 / WS-5)
- `race_scenario` binary-blob parsing (future WS, port from Hakuraku)
- Hooking into libnative.so (WS-2)
- GameState construction (WS-6)
- Outgoing command serialisation (WS-7)
- Integration into `scripts/auto_turn.py` (WS-8)
