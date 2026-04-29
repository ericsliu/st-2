# Packet Schema Audit — 2026-04-28

Snapshot of `uma_trainer/perception/carrotjuicer/schema/` coverage against
the captured sessions on disk:

- `data/packet_captures/session_20260421_233531` — partial career run
- `data/packet_captures/session_20260421_235333` — partial career run
- `data/packet_captures/session_20260428_001231` — career run incl. shop
  purchase + use-item + race result (target capture for the item-shop /
  inventory gap)

Together: 134 packets (67 responses / 67 requests).

Run `.venv/bin/python scripts/validate_packet_schema.py --all` to
regenerate these numbers against whatever captures are present. The
companion pytest module `tests/test_packet_schema.py` encodes the
invariants the validator checks so regressions surface in CI.

## PacketKind distribution

### Responses (67)

| Kind | Count | Notes |
|---|---:|---|
| `EVENT_TRIGGER` | 29 | Training-home response with pending events |
| `TRAINING_HOME` | 23 | Standard training-turn home screen |
| `RACE_START` | 5 | `race_scenario` + `race_start_info` |
| `RACE_RESULT` | 3 | `race_reward_info` |
| `CHOICE_REWARD_PREVIEW` | 2 | `choice_reward_array` (per-choice gain preview) |
| `BOOT_AUTH_RESP` | 1 | Login / nonce / attest handshake |
| `HOME_TOP_LOAD` | 1 | Main-menu bootstrap (item / card / support lists) |
| `SEASON_PACK_INFO` | 1 | Season-pack metadata sync |
| `RESERVED_RACES_VIEW` | 1 | Schedule-screen read |
| `NAV_ACK` | 1 | Empty server ack (response_code only) |

Zero `UNKNOWN`. Each shape routes deterministically by top-level key
presence — see `schema/packets.py::detect_packet_kind`.

### Requests (67)

| Kind | Count | Notes |
|---|---:|---|
| `REQUEST_EVENT_CHOICE` | 31 | `event_id` + `choice_number` |
| `REQUEST_NAV_POLL` | 14 | Bare poll (device boilerplate + optional current_turn) |
| `REQUEST_COMMAND` | 10 | Training / rest / infirmary tile submission |
| `REQUEST_RACE_ENTRY` | 4 | `program_id` + `current_turn` (no command_type) |
| `REQUEST_BOOT_AUTH` | 2 | `attestation_type` / `adid` / `device_token` |
| `REQUEST_RACE_SCHEDULE_EDIT` | 1 | `add_race_array` / `cancel_race_array` |
| `REQUEST_SKILL_PURCHASE` | 1 | `gain_skill_info_array` |
| `REQUEST_GRAND_LIVE_CONCERT` | 1 | `music_id` + `member_info_array` |
| `REQUEST_BUY_ITEM` | 1 | `exchange_item_info_array` (Trackblazer shop purchase) |
| `REQUEST_USE_ITEM` | 1 | `use_item_info_array` (consume inventory item) |
| `REQUEST_CONTINUE` | 1 | `continue_type` (race retry / G1 alarm clock) |

Zero `REQUEST_GENERIC`.

## Container field coverage

All of these dataclasses now have their `.extras` bag empty across every
captured packet:

- `chara_info` (CharaInfo) — added 20 previously-unmapped fields:
  `default_max_{speed,stamina,power,guts,wiz}`, `chara_grade`, `rarity`,
  `state`, `playing_state`, `short_cut_state`, `race_program_id`,
  `reserve_race_program_id`, `race_running_style`, `is_short_race`,
  `training_level_info_array`, `succession_trained_chara_id_{1,2}`,
  `disable_skill_id_array`, `nickname_id_array`, `guest_outing_info_array`.
- `race_start_info` (RaceStartInfo) — added `random_seed`, `season`,
  `weather`, `ground_condition`, `continue_num`.
- `race_reward_info` (RaceRewardInfo) — added `result_time`,
  `gained_fans`, `race_reward`, `race_reward_bonus`,
  `race_reward_plus_bonus`, `race_reward_bonus_win`, `campaign_id_array`
  (with `RaceRewardItem` typing the reward entries).
- `unchecked_event_array[]` (UncheckedEvent) — added `chara_id`,
  `play_timing`, `succession_event_info`, `minigame_result`.
- `chara_info.skill_tips_array[]` (SkillHintEntry) — added `group_id`,
  `rarity`.
- `free_data_set` (FreeDataSet) — fully typed; container clean across
  every Trackblazer turn captured (15 fields).
- `free_data_set.pick_up_item_info_array[]` (TrackblazerShopItem) —
  fully typed.
- `free_data_set.user_item_info_array[]` (OwnedItem) — fully typed.
- `choice_reward_array[]` (ChoiceReward) — fully typed; nested
  `gain_param_array[]` (GainParam) also clean.

## New top-level typed fields on `GamePacket`

- `race_condition_array: list[RaceCondition]` — server-authoritative
  upcoming-race list with per-race `weather` + `ground_condition`.
  Replaces the bot's `data/race_calendar.json` lookahead for live state.
- `command_result: CommandResult` — post-command resolution envelope
  (`command_id`, `sub_id`, `result_state`).
- `not_up_parameter_info: ParameterBoundInfo` — stats/skills/effects
  that *cannot* go up this turn.
- `not_down_parameter_info: ParameterBoundInfo` — stats/skills/effects
  that *cannot* go down this turn.
- `event_effected_factor_array: list` — post-event stat/skill deltas
  (kept list-shaped; inner schema TBD when a capture shows it non-empty).
- `free_data_set: FreeDataSet` — Trackblazer scenario sidecar (shop
  offerings, inventory, coin balance, scoring, rivals).
- `choice_reward_array: list[ChoiceReward]` — per-choice gain preview
  emitted on event-choice menus.

### `FreeDataSet` — Trackblazer scenario sidecar (Cygames `free_data_set`)

Lives on every TRAINING_HOME / EVENT_TRIGGER / RACE_START response while
the trainee is in scenario_id=4 (Trackblazer). Replaces the
inventory-bootstrap OCR path and provides authoritative shop state.

| Field | Replaces / source of truth for |
|---|---|
| `coin_num` | Coin wallet (Trackblazer shop currency) |
| `gained_coin_num` | Coins earned this turn |
| `shop_id` | Shop rotation id (refreshes drive new offerings) |
| `sale_value` | Active discount amount |
| `win_points` / `prev_win_points` | Trackblazer score (race score progression) |
| `pick_up_item_info_array[]` | Current shop offerings (5/turn observed) |
| `user_item_info_array[]` | Per-career inventory (item_id, num) |
| `item_effect_array` | Active item effects this turn (None when empty) |
| `command_info_array[]` | Trackblazer-specific scoring per training tile |
| `rival_race_info_array[]` | Rival race draws (chara_id, program_id) |
| `twinkle_race_npc_info_array[]` | Twinkle/spark NPC race entries |
| `twinkle_race_npc_result_array[]` | Twinkle/spark race results |
| `twinkle_race_ranking` | Trainee's rank in last twinkle race |
| `unchecked_event_achievement_id` | Pending Trackblazer achievement event |

Each `pick_up_item_info_array` entry (`TrackblazerShopItem`):
`shop_item_id`, `item_id`, `coin_num` (price), `original_coin_num` (pre-
sale), `item_buy_num` (already bought this rotation),
`limit_buy_count`, `limit_turn`. Helpers: `stock_remaining`,
`is_on_sale`.

Each `user_item_info_array` entry (`OwnedItem`): `item_id`, `num`.

### `ChoiceReward` — server-emitted event-choice gain preview

When the player opens an event-choice menu, the server preempts the
choice with a CHOICE_REWARD_PREVIEW response listing what each option
would grant:

```
choice_reward_array: [
  { select_index: 1, gain_param_array: [{display_id, effect_value_0/1/2}, ...] },
  { select_index: 2, gain_param_array: [...] },
]
```

`display_id` identifies the gain category (stat / motivation / item /
skill / coin / bond) and `effect_value_0/1/2` carry magnitudes / target
ids. Bot can score event choices off this directly without a curated
event handler — but full decoding requires `master.mdb` lookup keyed on
`display_id`.

## Game-mechanic coverage audit

The list below traces every bot-relevant game mechanic back to a typed
packet field (or flags it as a gap).

### Covered

| Mechanic | Source |
|---|---|
| Stats (Speed/Stamina/Power/Guts/Wit) | `chara_info.{speed,stamina,power,guts,wiz}` |
| Stat caps (current) | `chara_info.max_{speed,stamina,power,guts,wiz}` |
| Stat caps (baseline) | `chara_info.default_max_{speed,stamina,power,guts,wiz}` |
| Energy | `chara_info.vital` / `max_vital` |
| Mood (motivation) | `chara_info.motivation` |
| Turn counter | `chara_info.turn` (also on every request as `current_turn`) |
| Skill points | `chara_info.skill_point` |
| Fan count | `chara_info.fans` |
| Distance aptitudes | `chara_info.proper_distance_{short,mile,middle,long}` |
| Surface aptitudes | `chara_info.proper_ground_{turf,dirt}` |
| Running-style aptitudes | `chara_info.proper_running_style_*` |
| Conditions / status effects | `chara_info.chara_effect_id_array` |
| Locked / disabled skills | `chara_info.disable_skill_id_array` |
| Owned skills | `chara_info.skill_array` |
| Revealed skill hints | `chara_info.skill_tips_array` (incl. `group_id` + `rarity`) |
| Support cards (deck + positions) | `chara_info.support_card_array` |
| Bond per partner | `chara_info.evaluation_info_array` |
| Scheduled races this career | `chara_info.reserved_race_array` + `route_race_id_array` |
| Facility / training command levels | `chara_info.training_level_info_array` |
| Training tiles (gain preview / failure rate / partners) | `home_info.command_info_array` |
| Tile-disable mask | `home_info.disable_command_id_array` |
| Continue-item availability | `home_info.available_continue_num` et al |
| Race-lock state for the turn | `home_info.race_entry_restriction` |
| Upcoming-race weather / ground | `race_condition_array[].weather / ground_condition` |
| Race start venue / conditions | `race_start_info.{program_id,season,weather,ground_condition,random_seed}` |
| Race AI field (trainee + 17) | `race_start_info.race_horse_data` |
| Race finish rank + time | `race_reward_info.result_rank` / `result_time` |
| Race rewards (fans / items / bonuses) | `race_reward_info.race_reward*` + `gained_fans` |
| Active scenario campaigns | `race_reward_info.campaign_id_array` |
| Pending story events | `unchecked_event_array` (+ `chara_id`, `play_timing`) |
| Hint-event detection | `UncheckedEvent.is_skill_hint` property |
| Post-command resolution | `command_result.{command_id,sub_id,result_state}` |
| Stat movement blockers | `not_up_parameter_info` / `not_down_parameter_info` |
| Career-end trigger | top-level `single_mode_factor_select_common` → `PacketKind.RUN_ENDED` |
| Scenario sidecars | `venus_data_set` / `live_data_set` / `arc_data_set` / `sport_data_set` / `cook_data_set` / `mecha_data_set` / `team_data_set` / `free_data_set` |
| Trackblazer coin balance | `free_data_set.coin_num` (delta = `gained_coin_num`) |
| Trackblazer shop offerings | `free_data_set.pick_up_item_info_array` (5/turn, with prices, sales, stock) |
| Per-career inventory | `free_data_set.user_item_info_array` (item_id + num) |
| Active item effects | `free_data_set.item_effect_array` (None when none active) |
| Trackblazer score | `free_data_set.win_points` (vs `prev_win_points`) |
| Rival race draws | `free_data_set.rival_race_info_array` |
| Twinkle-race state | `free_data_set.twinkle_race_npc_*` |
| Event-choice gain preview | `choice_reward_array[]` → `ChoiceReward.gain_param_array` |
| Buy item from shop | `REQUEST_BUY_ITEM` (`exchange_item_info_array[shop_item_id, current_num]`) |
| Use item from inventory | `REQUEST_USE_ITEM` (`use_item_info_array`) |
| Continue / G1 retry | `REQUEST_CONTINUE` (`continue_type`) |

### Resolved (was TODO before 2026-04-28 capture)

1. ~~**Item shop**~~ — RESOLVED. Trackblazer shop lives on every turn's
   `free_data_set` (typed as `FreeDataSet`):
   - In-stock items: `pick_up_item_info_array[]` with shop_item_id,
     item_id, coin_num (price), original_coin_num (pre-sale),
     item_buy_num (used), limit_buy_count, limit_turn.
   - Coin balance: `free_data_set.coin_num`.
   - Coins earned this turn: `free_data_set.gained_coin_num`.
   - Sale magnitude: `free_data_set.sale_value`.
   - Rotation: `free_data_set.shop_id`.
   - Purchase transaction: `REQUEST_BUY_ITEM` with
     `exchange_item_info_array` (entries: shop_item_id, current_num).
2. ~~**Inventory (per-career)**~~ — RESOLVED. Lives on every Trackblazer
   turn at `free_data_set.user_item_info_array[]` (item_id, num). The
   meta-game bag on `HOME_TOP_LOAD.item_list` is for menu state; the
   per-career inventory is the in-scenario bag the bot acts on.
3. ~~**Event outcome preview**~~ — PARTIALLY RESOLVED via the new
   CHOICE_REWARD_PREVIEW packet kind: server pre-broadcasts each
   choice's gains. (The post-resolution `event_effected_factor_array`
   side-effects path is still empty in all captures — see remaining
   gap #2 below.)

### Gaps (TODO)

1. **Scenario-specific state beyond the sidecar dataclass.** Our
   captures are Trackblazer (`scenario_id=4`); typing for
   non-Trackblazer sidecars (`venus_data_set`, `live_data_set`,
   `arc_data_set`, `sport_data_set`, `cook_data_set`, `mecha_data_set`,
   `team_data_set`) remains shallow. Type each on demand when we run
   that scenario.

2. **Event outcome side-effects.** `event_effected_factor_array` is
   typed as a `list` at the root because every capture we have of it
   is empty. Once we see a non-empty one (event that grants stats /
   skills / mood change post-resolution, separate from the choice
   preview), type the inner entries.

3. **`race_scenario` binary blob.** Stays as `race_scenario_bytes` for
   now. Not needed unless we want the bot to replay or diff races.

4. **`command_result.result_state` enum.** We see values 1 and 2 in
   captures (rest vs. training) — the full enum (failure, great,
   perfect, …) needs more data to confirm.

5. **`GainParam.display_id` decoding.** Decoded `ChoiceReward` carries
   `display_id` + 3 effect values per gain; mapping `display_id` → gain
   type (stat / motivation / item / skill / coin / bond / SP) requires
   master.mdb cross-reference. Observed values so far: 1 (stat?), 4
   (skill?), 6 (item?). Confirm via master.mdb during integration.

## References

- Runtime stack: `docs/packet_capture_runtime.md`
- State layer design: `docs/packet_state_layer.md`
- Validator: `scripts/validate_packet_schema.py`
- Field inspector: `scripts/inspect_field.py <dotted.path>`
- Unknown/generic dump: `scripts/dump_unknown_packets.py`
- Schema tests: `tests/test_packet_schema.py`
