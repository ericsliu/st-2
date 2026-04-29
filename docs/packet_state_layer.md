# Packet State Layer — Design

Replacement layer for the OCR-based state builder in `auto_turn.py`. Every field
the bot currently reads from the screen is present in the HTTP response stream
we capture via `captureCuteHttpDelegates` (see `frida_agent/src/hook_deserializer.ts`).
Ground truth is the msgpack response body from each Uma API call; OCR remains
only as a fallback when packet capture is unavailable.

## Capture surface

Hooks on `Cute.Http.HttpManager.set_DecompressFunc` / `set_CompressFunc` delegate
slots. Each round-trip gives us two plaintext msgpack buffers:

- request: `compress.method_ptr` input (plaintext → LZ4 → AES → base64 on the wire)
- response: `decompress.method_ptr` output (base64 → AES → LZ4 → plaintext)

Pairing is temporal — compress → decompress happens serially per request.
See `scripts/decode_uma_capture.py` for the pairing + msgpack decode.

## Authoritative game-state map

All paths below are under the response root `data.*`.

### `chara_info` — trainee state (authoritative)

| Field | Replaces OCR of |
|---|---|
| `speed`, `stamina`, `power`, `guts`, `wiz` | Stat bar numerics |
| `max_speed`, `max_stamina`, … | Stat caps |
| `motivation` | Mood (1–5: Awful/Bad/Normal/Good/Great) |
| `turn` | Turn counter (also on every request as `current_turn`) |
| `skill_point` | SP counter |
| `fans` | Fan count |
| `proper_distance_short/mile/middle/long` | Distance aptitudes |
| `proper_ground_turf/dirt` | Surface aptitudes |
| `proper_running_style_*` | Running-style aptitudes |
| `support_card_array[]` | Deck composition (position, card_id, limit_break, owner) |
| `evaluation_info_array[]` | Bond bars (`evaluation` per `training_partner_id`) |
| `skill_array[]` | Acquired skills |
| `route_race_id_array` | Scheduled races this career |
| `single_mode_chara_id` | Career-run chara id |
| `scenario_id` | Scenario (4 = Trackblazer) |

### `home_info.command_info_array[]` — training tile preview (authoritative)

One entry per home-screen action (5 training tiles + rest + recreation + infirmary + race + skills). Replaces the entire "tap each bubble → OCR gains → sum boosted rows" scan loop.

```
{
  command_id: 101..106 (Speed/Stamina/Power/Guts/Wit/…),
  command_type: 1=train, 3=rest, ...,
  level: training level,
  failure_rate: exact integer percent,
  is_enable: tile available this turn,
  params_inc_dec_info_array: [
    { target_type: 1..5=stats, 10=energy, 30=skill pts, ..., value: signed delta }
  ],
  training_partner_array: [ int, ... ],   // positions — see partner resolution
  tips_event_partner_array: [ ... ]
}
```

Item-boosted gains already arrive summed — no more two-row OCR hack.

### Partner resolution

`command_info_array[i].training_partner_array` is a list of small integers.
They are **position / training_partner_id** values, not card IDs.

**Support card slots (positions 1–6):**
- Join `chara_info.support_card_array` on `position` → `support_card_id`.
- Join `master.mdb.support_card_data.id = support_card_id` → `chara_id`.
- Join `master.mdb.text_data` with `category=75`, `index=support_card_id` → display name.

**Scenario partners / NPCs (positions 100+):**
- Join `master.mdb.single_mode_unique_chara` on `partner_id` with
  `scenario_id=chara_info.scenario_id` → `chara_id`.
- `chara_id` in the 9000s are NPCs (e.g. 9002 = Yayoi Akikawa / director,
  9003 = Etsuko Otonashi / reporter, 9006 = Riko Kashimoto).
- Join `master.mdb.text_data` with `category=170`, `index=chara_id` → display name.

Example (Trackblazer, observed in `session_20260421_233531`):

| partner_id | chara_id | name |
|---|---|---|
| 101 | 9001 | Tazuna Hayakawa |
| 102 | 9002 | Yayoi Akikawa (director) |
| 103 | 9003 | Etsuko Otonashi (reporter) |
| 104 | 9004 | Aoi Kiryuin |
| 106 | 9006 | Riko Kashimoto |

**Bond** for any partner (card or NPC) is
`chara_info.evaluation_info_array[k].evaluation` where
`training_partner_id == k`. No bond-bar OCR needed.

### Other high-value response fields

- `race_condition_array` — upcoming races (id, turn, grade, conditions). Replaces race calendar + "race available" detection.
- `race_start_info` — pre-race venue/track/conditions when entering a race.
- `unchecked_event_array` — pending story events.
- `event_effected_factor_array` — post-event stat/skill deltas.
- `command_result` — post-training result (actual gains, skill procs, bond changes).
- `not_up_parameter_info` / `not_down_parameter_info` — stat soft-caps (what can't move this turn). Usage TBD.
- `free_data_set` — Trackblazer scenario state (grade points, TS Climax phase).

## Integration surface — where the bot plugs in

Current `auto_turn.build_game_state(img, screen)` scrapes stats/energy/mood via
`ocr_region` + `PlaqueMatcher` + per-tile tap-and-OCR. The packet layer replaces
this end-to-end:

1. **State builder rewrite.** `build_game_state` consumes the most-recent
   `home_info` + `chara_info` instead of an ADB screenshot. Screenshots remain
   only for screen-type detection (`detect_screen`) and UI tap targeting.
2. **Training preview.** `_handle_career_home` drops its tile-scan loop —
   gains, failure rates, partners, and bond per tile come from `command_info_array`.
3. **Bond tracking.** `card_tracker.py` can stop sprite-matching; read
   `evaluation_info_array` directly.
4. **Race selection.** `race_selector.py` consumes `race_condition_array`
   instead of deriving upcoming races from `data/race_calendar.json`.

Order of operations matters: the bot still drives the game via taps, so we
always have a screenshot → screen detection pass. The packet data fills the
"what's on screen" question; taps answer "what do we do about it".

## IPC between probe and bot

Currently the probe writes `.bin` files to
`data/packet_captures/session_*/` and appends to `index.jsonl`. For live
integration the bot needs the decoded message within the same turn window.
Two reasonable approaches:

- **Tail index.jsonl + lazy decode.** Bot polls the latest session dir, decodes
  any new `.bin` it hasn't seen. Simple, survives probe restarts, but adds ~1
  msgpack decode of latency per turn.
- **Local socket from probe agent.** `frida_c1_probe.py` grows a Unix socket;
  `auto_turn.py` connects and receives already-decoded pairs. Lowest latency,
  but couples the two processes' lifecycles.

Start with tail+decode. Switch to socket only if turn latency becomes an issue.

## Known gaps

- Pairing is temporal-adjacency; concurrent requests would break it. Uma's HTTP
  is serial in practice, but validate before assuming.
- Ciphertext sides (`compress.out`, `decompress.in`) are opaque. Not needed for
  state, but means we can't replay or mutate requests without reimplementing
  the AES + LZ4 stack.
- We have no write path — everything is observation. Bot still acts via ADB taps.
- First-turn capture starts wherever the probe was attached; pre-attach requests
  (login, asset loading) are missing from the session. Expected; bot only cares
  about home-screen state onward.

## References

- Runtime stack + capture trigger lifecycle: `docs/packet_capture_runtime.md`
- Typed schema coverage + gap/TODO audit: `docs/packet_schema_audit.md`
- Hook implementation: `frida_agent/src/hook_deserializer.ts` (`captureCuteHttpDelegates`)
- Probe driver + persistence: `scripts/frida_c1_probe.py` (`--capture-cute-http`; armed-by-default, `--capture-auto` to fire on attach)
- Offline decoder: `scripts/decode_uma_capture.py`
- Research trail (how we got here): `docs/PACKET_INTERCEPTION_SPEC_ADDENDUM_4.md`
- OCR layer (being replaced): `docs/perception_pipeline.md`
