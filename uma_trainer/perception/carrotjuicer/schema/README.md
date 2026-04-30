# CarrotJuicer Packet Schema

Typed Python dataclasses mirroring the msgpack dicts that Uma Musume's
game server sends to the client. Packets are decrypted / LZ4-decompressed
upstream by a CarrotJuicer-style hook (see
`docs/PACKET_INTERCEPTION_SPEC.md`); this package only cares about the
resulting Python dict structure.

This directory is the **schema layer**. It does not do I/O, networking,
or decompression. It takes a `dict` that came off the wire and turns it
into typed objects that the rest of `uma_trainer` can reason about.

## Module map

| module | purpose |
|---|---|
| `enums.py` | Integer / string enums (scenario_id, command_id, mood, etc.) |
| `career.py` | Per-turn trainee state (`CharaInfo`, `ReservedRace`) |
| `training_state.py` | Training home screen (`HomeInfo`, `CommandInfo`, `ParamsIncDecInfo`) |
| `scenario_data.py` | Venus / Live / Arc / Sport / Cook / Mecha / Team sidecars |
| `support_cards.py` | Support card lineup, bond, evaluation, training partners |
| `events.py` | `UncheckedEvent`, event choices |
| `skills.py` | Skill entries, skill hints, skill purchase requests |
| `race.py` | Race start info, race reward info, `RaceScenarioDecoded` |
| `packets.py` | Top-level `GamePacket` envelope + `detect_packet_kind` router |
| `parser.py` | Main orchestrator: `parse_packet(raw)` |

## Usage

```python
from uma_trainer.perception.carrotjuicer.schema import (
    parse_packet, PacketKind, PacketDirection,
)

# raw is a dict produced upstream by msgpack.unpackb(...)
pkt = parse_packet(raw, direction=PacketDirection.RESPONSE)

if pkt.kind == PacketKind.TRAINING_HOME:
    chara = pkt.chara_info
    home = pkt.home_info
    print(f"Turn {chara.turn}  SPD={chara.speed} STA={chara.stamina}")
    for cmd in home.command_info_array:
        print(f"  cmd={cmd.command_id} fail={cmd.failure_rate}%")

elif pkt.kind == PacketKind.EVENT_TRIGGER:
    ev = pkt.unchecked_event_array[0]
    if ev.is_skill_hint:
        print(f"Skill hint from chara {ev.hint_chara_id}")

elif pkt.kind == PacketKind.RACE_RESULT:
    print(f"Finished rank = {pkt.race_reward_info.result_rank}")
```

## Packet kinds

`PacketKind` is the first thing downstream consumers look at. Routing is
pure key-name inspection (see `packets.detect_packet_kind`):

### Server-to-client

- `TRAINING_HOME` - normal training turn: `chara_info` + `home_info`
- `TRAINING_SCENARIO_HOME` - training turn + a scenario sidecar block
  (e.g. `venus_data_set`, `arc_data_set`, `sport_data_set`...)
- `EVENT_TRIGGER` - `unchecked_event_array` is non-empty
- `RACE_START` - `race_scenario` binary blob + `race_start_info`
- `RACE_RESULT` - `race_reward_info` populated
- `SKILL_PURCHASE_ACK` - response to a `gain_skill_info_array` request
- `START_CAREER` - response to a `start_chara` request (career init)
- `RUN_ENDED` - `single_mode_factor_select_common` present (career done)
- `AOHARU_TEAM_RACE` - Aoharu team race result
- `LARC_SS_MATCH` - `selection_result_info` (L'Arc SS match)
- `CONCERT` - Grand Live concert response

### Client-to-server (requests)

- `REQUEST_START_CHARA` - key `start_chara` exists (even if empty dict)
- `REQUEST_COMMAND` - `command_type` + `command_id` (training / rest / outing / infirmary)
- `REQUEST_EVENT_CHOICE` - `event_id` + `choice_number`
- `REQUEST_SKILL_PURCHASE` - `gain_skill_info_array`
- `REQUEST_BUY_ITEM` - `exchange_item_info_array`
- `REQUEST_USE_ITEM` - `use_item_info_array`
- `REQUEST_CONTINUE` - `continue_type`
- `REQUEST_AOHARU_TEAM_RACE` - `team_race_set_id`
- `REQUEST_GRAND_LIVE_LESSON` - `square_id`
- `REQUEST_GENERIC` - catch-all

## Design principles

1. **Typed but forgiving.** Every field is typed, but any unmapped key
   the server sends lands in an `extras: dict` on the enclosing
   dataclass. The server adds fields across patches; we never want to
   drop data on the floor.

2. **Zero I/O.** These modules must stay pure Python + `typing` only.
   No msgpack import, no network, no file parsing. The upstream hook
   (WS-2) decompresses and unpacks; we type-check.

3. **Priority order matters in detection.** See
   `detect_packet_kind` - `single_mode_factor_select_common` (run
   ended) wins over everything, then `start_chara`, then events
   (because events piggyback on turn responses), then race, then
   training home.

4. **Payloads may be nested.** Some envelopes wrap everything under a
   top-level `data` key. `detect_packet_kind` transparently unwraps
   that. Nested races inside `venus_data_set` are also handled.

5. **Handle weird payload shapes.** Several arrays come back as
   either dicts or bare ints depending on the endpoint
   (e.g. `training_partner_array`). Leaf dataclasses have `from_raw`
   classmethods that accept either shape.

6. **Known gotchas are documented inline.** The biggest ones:
   - wisdom is `wiz` on the wire, never `wisdom` or `int`
   - in `race_horse_data`, power is `pow`, not `power`
   - `start_chara` may be `{}` (empty dict, falsy) - use
     `in` not `get(...)`
   - skills come through as `[id, level]` tuples sometimes

## Confidence tiers

Every field has a **confidence** marker in the dataclass docstring:

- **H (high)** - Verified from multiple sources (UmaLauncher, Hakuraku,
  or cjedb). Key name and value range are certain.
- **M (medium)** - Seen in one source but not cross-referenced.
- **L (low)** - Best guess based on context. Needs Phase 2 live-capture
  verification.

## Example raw msgpack dicts

### Training turn response

```json
{
  "chara_info": {
    "card_id": 101401,
    "scenario_id": 10,
    "turn": 18,
    "speed": 543,
    "stamina": 287,
    "power": 421,
    "guts": 112,
    "wiz": 198,
    "vital": 85,
    "max_vital": 100,
    "motivation": 4,
    "fans": 2350,
    "skill_point": 310,
    "support_card_array": [
      {"support_card_id": 30028, "talent_level": 4}
    ],
    "skill_array": [{"skill_id": 200021, "level": 1}],
    "reserved_race_array": []
  },
  "home_info": {
    "command_info_array": [
      {
        "command_id": 101,
        "level": 3,
        "failure_rate": 3,
        "params_inc_dec_info_array": [
          {"target_type": 1, "value": 11},
          {"target_type": 3, "value": 5}
        ],
        "training_partner_array": [30028, 30055]
      }
    ]
  }
}
```

Notes: wisdom stat is `wiz`, not `wisdom` or `int`. `motivation` is
1-5 where 5 = Great.  `command_id` 101=Speed, 102=Power, 103=Guts,
105=Stamina, 106=Wisdom (601-605 in summer, 1101-1105 overseas,
2101-2305 for scenario-specific training). `chara_effect_id_array`
elements are bare ints — see `data/chara_effect_lookup.json` for the
id → key/polarity mapping; regenerate via `scripts/extract_effect_ids.py`
when `data/master.mdb` refreshes.

### Event trigger

```json
{
  "chara_info": {"...": "..."},
  "unchecked_event_array": [
    {
      "story_id": 800280003,
      "event_id": 500123,
      "event_contents_info": {
        "support_card_id": 30028,
        "choice_array": [
          {"choice_number": 1},
          {"choice_number": 2}
        ]
      }
    }
  ]
}
```

A `story_id` matching `8XXXXX003` is a skill hint event. `hint_chara_id`
is the middle 5 digits (derived via `UncheckedEvent.hint_chara_id`).

### Race start

```json
{
  "race_scenario": "<binary blob: bytes>",
  "race_start_info": {
    "program_id": 100101,
    "race_horse_data": [
      {
        "frame_order": 3,
        "speed": 560, "stamina": 320, "pow": 430,
        "guts": 120,  "wiz": 210,
        "motivation": 4, "fan_count": 2350,
        "skill_array": [200021, 201034]
      }
    ]
  }
}
```

Note: at race time, power is `pow` not `power`. The binary
`race_scenario` blob is a proprietary serialised simulator state that
Hakuraku parses in TS; we wrap it as `RaceScenarioDecoded` (WIP).

### Race result

```json
{
  "race_reward_info": {
    "result_rank": 1,
    "reward_info_array": []
  }
}
```

### Client-to-server: training command

```json
{
  "command_type": 1,
  "command_id": 101,
  "current_turn": 18
}
```

### Client-to-server: event choice

```json
{
  "event_id": 500123,
  "choice_number": 2
}
```

## Known unknowns

The following fields are currently typed as `Any` or are left in
`extras` because we could not positively identify their structure from
static reading. Phase 2 (live packet capture) should verify:

- `race_reward_info.reward_info_array` - per-reward structure.
- `race_scenario` binary blob - handled by `race.RaceScenarioDecoded`
  placeholder; real parser lives in Hakuraku's TS code and needs a
  Python port.
- `venus_spirit_active_effect_info_array` - element schema uncertain.
- `selection_result_info` (L'Arc SS match) - fields uncertain.
- `single_mode_factor_select_common` - retirement bonus structure.
- `live_theater_save_info_array` (Grand Live) - per-entry schema
  uncertain.

## Confidence summary

- **HIGH**: CharaInfo, HomeInfo, CommandInfo, SupportCardRef, UncheckedEvent
  structure, EventChoice, RaceHorseData core stats, RaceStartInfo,
  RaceRewardInfo.result_rank, all enums.
- **MEDIUM**: ParamsIncDecInfo target types, SkillEntry level
  semantics, TeamDataSet shape, TrainingPartnerRef (dict vs bare-int).
- **LOW**: Scenario sidecar internals (Venus/Live/Arc/Sport/Cook/Mecha)
  beyond top-level key names, RaceScenarioDecoded contents.
