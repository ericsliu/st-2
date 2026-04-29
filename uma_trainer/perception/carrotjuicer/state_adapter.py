"""Build a ``GameState`` from a decoded Uma training-home response packet.

This is the bridge between the carrotjuicer schema layer (typed dataclasses
mirroring server response shapes) and the bot's internal ``GameState`` type
(what ``scripts.auto_turn`` and the decision components consume).

Only the training-home path is covered here — race/event/shop screens still
come from the OCR pipeline for now. On a training-home response we can
populate stats, energy, mood, turn, support cards with bond, and per-tile
training previews (gains, failure rate, partner identity) end-to-end from
the packet, with zero screen reads beyond the screenshot-based
``detect_screen`` step.

See ``docs/packet_state_layer.md`` for the overall design.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from uma_trainer.types import (
    ActiveItemEffect,
    GameState,
    Mood,
    ScenarioInventoryEntry,
    ScenarioShopItem,
    ScenarioState,
    ScreenState,
    StatType,
    SupportCard,
    TraineeStats,
    TrainingTile,
    UpcomingRace,
)

from .schema.career import CharaInfo
from .schema.race import RaceCondition
from .schema.scenario_data import FreeDataSet
from .schema.training_state import CommandInfo, HomeInfo
from .schema.enums import ParamTargetType, ScenarioId

# Training command_id → stat.  104 is NOT training (scenario-specific action
# slot such as infirmary on some scenarios); real stat trainings are only 5.
_TRAINING_COMMAND_STAT: dict[int, StatType] = {
    101: StatType.SPEED,
    102: StatType.POWER,
    103: StatType.GUTS,
    105: StatType.STAMINA,
    106: StatType.WIT,
}

_MOTIVATION_TO_MOOD: dict[int, Mood] = {
    1: Mood.TERRIBLE,
    2: Mood.BAD,
    3: Mood.NORMAL,
    4: Mood.GOOD,
    5: Mood.GREAT,
}

# master.mdb item_id (text_data category 225) -> shop_manager ITEM_CATALOGUE
# key. Generated via scripts/probe_item_ids.py against text_data category 225
# (item display name) on 2026-04-28. Items in ITEM_CATALOGUE that have no
# matching master.mdb name (notepad, manual, scroll, training_application,
# cat_food, practice_perfect) are intentionally absent — those don't appear
# as Trackblazer pick-up offerings.
_ITEM_ID_TO_KEY: dict[int, str] = {
    2001: "vita_20",
    2002: "vita_40",
    2003: "vita_65",
    2101: "royal_kale",
    2201: "energy_drink_max",
    2202: "energy_drink_max_ex",
    2301: "plain_cupcake",
    2302: "berry_cupcake",
    3101: "grilled_carrots",
    4001: "pretty_mirror",
    4002: "hot_topic",
    4004: "scholar_hat",
    4101: "fluffy_pillow",
    4102: "pocket_planner",
    4103: "rich_hand_cream",
    4104: "smart_scale",
    4105: "aroma_diffuser",
    4106: "practice_dvd",
    4201: "miracle_cure",
    7001: "reset_whistle",
    8001: "coaching_mega",
    8002: "motivating_mega",
    8003: "empowering_mega",
    9001: "speed_ankle_weights",
    9002: "stamina_ankle_weights",
    9003: "power_ankle_weights",
    9004: "guts_ankle_weights",
    10001: "good_luck_charm",
    11001: "artisan_hammer",
    11002: "master_hammer",
    11003: "glow_sticks",
}


# ---------------------------------------------------------------------------
# Card / NPC name registry
# ---------------------------------------------------------------------------

@dataclass
class PartnerName:
    name: str
    chara_id: int = 0
    is_npc: bool = False


class CardRegistry:
    """Reads master.mdb to turn numeric IDs into display strings.

    Cheap to construct: the underlying sqlite connection is opened lazily on
    first query and all lookups are LRU-cached for the life of the registry.
    """

    def __init__(self, mdb_path: Path | str = "data/master.mdb") -> None:
        self.mdb_path = Path(mdb_path)
        self._conn: Optional[sqlite3.Connection] = None

    def _cursor(self) -> sqlite3.Cursor:
        if self._conn is None:
            if not self.mdb_path.exists():
                raise FileNotFoundError(
                    f"master.mdb not found at {self.mdb_path}; see "
                    f"docs/reference_master_mdb.md for extraction"
                )
            self._conn = sqlite3.connect(f"file:{self.mdb_path}?mode=ro", uri=True)
        return self._conn.cursor()

    @lru_cache(maxsize=512)
    def support_card_name(self, support_card_id: int) -> str:
        if not support_card_id:
            return ""
        row = self._cursor().execute(
            'SELECT text FROM text_data WHERE category=75 AND "index"=?',
            (support_card_id,),
        ).fetchone()
        return row[0] if row else f"card_{support_card_id}"

    @lru_cache(maxsize=512)
    def race_program_info(self, program_id: int) -> dict | None:
        """Resolve a ``single_mode_program.id`` to race metadata.

        Returns a dict with ``race_id``, ``name``, ``grade`` (human string),
        ``distance_m``, ``surface``, ``month``, ``half``. Returns None if
        the program_id is unknown to master.mdb.
        """
        if not program_id:
            return None
        cur = self._cursor()
        row = cur.execute(
            'SELECT smp.month, smp.half, ri.race_id, ri.id, r.grade, '
            '       rcs.distance, rcs.ground '
            'FROM single_mode_program smp '
            'JOIN race_instance ri ON ri.id = smp.race_instance_id '
            'JOIN race r           ON r.id  = ri.race_id '
            'JOIN race_course_set rcs ON rcs.id = r.course_set '
            'WHERE smp.id=?',
            (program_id,),
        ).fetchone()
        if row is None:
            return None
        month, half_int, race_id, instance_id, grade_int, distance, ground = row
        # Race names live across several text_data categories. Major graded
        # races use category 38 ("Niigata Junior S."), scenario / maiden /
        # campaign races key by race.id under category 32 ("Junior Maiden
        # Race"), and a final fallback resolves by race_instance.id under
        # category 28. Empirically this covers every program_id seen in
        # captures.
        name = ""
        for cat, idx in (
            (38, race_id),
            (32, race_id),
            (28, instance_id),
        ):
            r = cur.execute(
                'SELECT text FROM text_data WHERE category=? AND "index"=?',
                (cat, idx),
            ).fetchone()
            if r and r[0]:
                name = r[0]
                break
        return {
            "race_id": race_id,
            "name": name or f"race_{race_id}",
            "grade": _race_grade_to_string(grade_int),
            "distance_m": distance,
            "surface": "turf" if ground == 1 else "dirt",
            "month": month,
            # half_int: 1=early, 2=late (matches single_mode_program.half)
            "half": "early" if half_int == 1 else "late",
        }

    @lru_cache(maxsize=256)
    def scenario_partner(self, scenario_id: int, partner_id: int) -> PartnerName:
        """Look up a scenario-specific partner (Trackblazer reporter, etc.)."""
        row = self._cursor().execute(
            'SELECT u.chara_id, td.text '
            'FROM single_mode_unique_chara u '
            'LEFT JOIN text_data td ON td.category=170 AND td."index"=u.chara_id '
            'WHERE u.partner_id=? AND u.scenario_id=?',
            (partner_id, scenario_id),
        ).fetchone()
        if row is None:
            return PartnerName(name=f"npc_{partner_id}", is_npc=True)
        chara_id, name = row
        return PartnerName(name=name or f"npc_{chara_id}", chara_id=chara_id, is_npc=True)


# ---------------------------------------------------------------------------
# Intermediate assembled view
# ---------------------------------------------------------------------------

@dataclass
class ResolvedPartner:
    """A training partner that has been resolved to a real name."""

    partner_id: int                # training_partner_id (position, 1..6 or 100+)
    name: str
    is_npc: bool = False
    support_card_id: int = 0
    limit_break_count: int = 0
    is_friend_card: bool = False
    bond: int = 0                  # 0..100


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def game_state_from_response(
    response: dict,
    *,
    registry: CardRegistry | None = None,
    screen: ScreenState = ScreenState.TRAINING,
    card_semantic_map: dict[int, str] | None = None,
) -> GameState:
    """Assemble a ``GameState`` from a decoded training-home response dict.

    ``response`` is the msgpack-decoded dict *as it arrives from the server*
    (the outer envelope — we unwrap ``data`` ourselves). Any missing blocks
    produce default values rather than raising, matching auto_turn's
    defensive style.

    ``card_semantic_map`` (optional): ``support_card_id → semantic_key``
    overlay. When set, partner names emitted into ``tile.support_cards``
    use the semantic key (e.g. ``"team_sirius"``) for any matching card,
    so the playbook's friendship-priority lookups keep working when the
    state layer is fed from packets instead of OCR. Cards not in the
    map fall back to the localized name from ``master.mdb``.
    """
    data = response.get("data") if isinstance(response, dict) and "data" in response else response
    if not isinstance(data, dict):
        return GameState(screen=screen)

    ci_raw = data.get("chara_info") or {}
    hi_raw = data.get("home_info") or {}
    chara = CharaInfo.from_raw(ci_raw) if isinstance(ci_raw, dict) else None
    home = HomeInfo.from_raw(hi_raw) if isinstance(hi_raw, dict) else None

    gs = GameState(screen=screen)

    if chara is not None:
        _fill_trainee(gs, chara, registry, card_semantic_map)

    if home is not None and chara is not None:
        partners = _resolve_partners(chara, registry, card_semantic_map)
        gs.training_tiles = _build_training_tiles(home, partners)
        gs.is_race_day = home.race_entry_restriction == 1

    rca_raw = data.get("race_condition_array")
    if isinstance(rca_raw, list) and rca_raw:
        gs.upcoming_races = _resolve_upcoming_races(rca_raw, registry)

    fds_raw = data.get("free_data_set")
    if isinstance(fds_raw, dict):
        gs.scenario_state = _build_scenario_state(fds_raw)

    return gs


def _build_scenario_state(fds_raw: dict) -> ScenarioState:
    """Convert a ``free_data_set`` dict into a :class:`ScenarioState`.

    Trackblazer is the only scenario with this sidecar today. Each shop
    offering and inventory entry is enriched with the bot's
    ``ITEM_CATALOGUE`` semantic key when known via :data:`_ITEM_ID_TO_KEY`;
    items not in the map keep ``item_key=""`` so callers can still react
    to the raw ``item_id``.
    """
    fds = FreeDataSet.from_raw(fds_raw)
    pick_ups = [
        ScenarioShopItem(
            shop_item_id=p.shop_item_id,
            item_id=p.item_id,
            item_key=_ITEM_ID_TO_KEY.get(p.item_id, ""),
            coin_num=p.coin_num,
            original_coin_num=p.original_coin_num,
            item_buy_num=p.item_buy_num,
            limit_buy_count=p.limit_buy_count,
            limit_turn=p.limit_turn,
        )
        for p in fds.pick_up_item_info_array
    ]
    inventory = [
        ScenarioInventoryEntry(
            item_id=oi.item_id,
            item_key=_ITEM_ID_TO_KEY.get(oi.item_id, ""),
            num=oi.num,
        )
        for oi in fds.user_item_info_array
    ]
    active_effects = [
        ActiveItemEffect(
            use_id=int(e.get("use_id", 0) or 0),
            item_id=int(e.get("item_id", 0) or 0),
            item_key=_ITEM_ID_TO_KEY.get(int(e.get("item_id", 0) or 0), ""),
            effect_type=int(e.get("effect_type", 0) or 0),
            effect_value_1=int(e.get("effect_value_1", 0) or 0),
            effect_value_2=int(e.get("effect_value_2", 0) or 0),
            effect_value_3=int(e.get("effect_value_3", 0) or 0),
            effect_value_4=int(e.get("effect_value_4", 0) or 0),
            begin_turn=int(e.get("begin_turn", 0) or 0),
            end_turn=int(e.get("end_turn", 0) or 0),
        )
        for e in fds.item_effect_array
        if isinstance(e, dict)
    ]
    return ScenarioState(
        scenario_key="trackblazer",
        coin=fds.coin_num,
        score=fds.win_points,
        pick_up_items=pick_ups,
        inventory=inventory,
        active_effects=active_effects,
    )


def _resolve_upcoming_races(
    rca_raw: list,
    registry: CardRegistry | None,
) -> list[UpcomingRace]:
    """Convert ``race_condition_array`` entries into ``UpcomingRace`` objects.

    Without a registry we still return entries — name/grade/distance fall
    back to defaults but ``program_id`` is preserved so callers can
    re-resolve later. With a registry, every entry is hydrated from
    master.mdb.
    """
    out: list[UpcomingRace] = []
    for raw in rca_raw:
        if not isinstance(raw, dict):
            continue
        cond = RaceCondition.from_raw(raw)
        info = registry.race_program_info(cond.program_id) if registry else None
        if info is None:
            out.append(
                UpcomingRace(
                    program_id=cond.program_id,
                    weather=cond.weather,
                    ground_condition=cond.ground_condition,
                )
            )
            continue
        out.append(
            UpcomingRace(
                program_id=cond.program_id,
                race_id=info["race_id"],
                name=info["name"],
                grade=info["grade"],
                distance_m=info["distance_m"],
                surface=info["surface"],
                month=info["month"],
                half=info["half"],
                weather=cond.weather,
                ground_condition=cond.ground_condition,
            )
        )
    return out


def _fill_trainee(
    gs: GameState,
    chara: CharaInfo,
    registry: CardRegistry | None,
    card_semantic_map: dict[int, str] | None,
) -> None:
    gs.stats = TraineeStats(
        speed=chara.speed,
        stamina=chara.stamina,
        power=chara.power,
        guts=chara.guts,
        wit=chara.wiz,
    )
    gs.energy = chara.vital
    gs.mood = _MOTIVATION_TO_MOOD.get(chara.motivation, Mood.NORMAL)
    gs.current_turn = chara.turn
    gs.fan_count = chara.fans
    gs.skill_pts = chara.skill_point
    gs.scenario = f"scenario_{chara.scenario_id}"
    gs.trainee_aptitudes = _aptitudes_to_letters(chara)
    gs.support_cards = _resolve_support_cards(chara, registry, card_semantic_map)


def _resolve_support_cards(
    chara: CharaInfo,
    registry: CardRegistry | None,
    card_semantic_map: dict[int, str] | None,
) -> list[SupportCard]:
    bond_by_partner = {
        e.training_partner_id: e.evaluation for e in chara.evaluation_info_array
    }
    out: list[SupportCard] = []
    for sc in sorted(chara.support_card_array, key=lambda s: s.position):
        name = registry.support_card_name(sc.support_card_id) if registry else ""
        # Semantic overlay — when the bot has a curated key for this
        # support_card_id, use it as the card_id (so friendship-priority
        # lookups keyed on "team_sirius" / "riko" / etc. keep working).
        cid_str = str(sc.support_card_id)
        if card_semantic_map and sc.support_card_id in card_semantic_map:
            cid_str = card_semantic_map[sc.support_card_id]
        out.append(
            SupportCard(
                card_id=cid_str,
                name=name,
                bond_level=bond_by_partner.get(sc.position, 0),
                is_friend=sc.is_friend_card,
            )
        )
    return out


def _resolve_partners(
    chara: CharaInfo,
    registry: CardRegistry | None,
    card_semantic_map: dict[int, str] | None,
) -> dict[int, ResolvedPartner]:
    """Index every potential training partner by ``training_partner_id``.

    Both support card slots (1..6) and scenario NPC slots (100+) are
    included; callers filter by whoever actually shows up on a tile.
    """
    bond_by_partner = {
        e.training_partner_id: e.evaluation for e in chara.evaluation_info_array
    }
    out: dict[int, ResolvedPartner] = {}

    for sc in chara.support_card_array:
        # Semantic overlay first (e.g. "team_sirius") so the playbook
        # priority list keeps matching; fall back to the localized
        # display name from master.mdb otherwise.
        if card_semantic_map and sc.support_card_id in card_semantic_map:
            name = card_semantic_map[sc.support_card_id]
        else:
            name = registry.support_card_name(sc.support_card_id) if registry else ""
        out[sc.position] = ResolvedPartner(
            partner_id=sc.position,
            name=name,
            is_npc=False,
            support_card_id=sc.support_card_id,
            limit_break_count=sc.limit_break_count,
            is_friend_card=sc.is_friend_card,
            bond=bond_by_partner.get(sc.position, 0),
        )

    # Scenario NPCs appear in evaluation_info_array with target_id >= 100.
    for e in chara.evaluation_info_array:
        if e.training_partner_id < 100 or e.training_partner_id in out:
            continue
        partner = (
            registry.scenario_partner(chara.scenario_id, e.training_partner_id)
            if registry
            else PartnerName(name=f"npc_{e.training_partner_id}", is_npc=True)
        )
        out[e.training_partner_id] = ResolvedPartner(
            partner_id=e.training_partner_id,
            name=partner.name,
            is_npc=True,
            bond=e.evaluation,
        )

    return out


def _build_training_tiles(
    home: HomeInfo,
    partners: dict[int, ResolvedPartner],
) -> list[TrainingTile]:
    tiles: list[TrainingTile] = []
    for pos, cmd in enumerate(
        sorted(
            (c for c in home.command_info_array if c.is_training and c.command_id in _TRAINING_COMMAND_STAT),
            key=lambda c: c.command_id,
        )
    ):
        stat = _TRAINING_COMMAND_STAT[cmd.command_id]
        stat_gains = _params_to_gains(cmd)
        card_names: list[str] = []
        bond_levels: list[int] = []
        for partner_ref in cmd.training_partner_array:
            pid = partner_ref.training_partner_id
            p = partners.get(pid)
            if p is None:
                continue
            card_names.append(p.name or (f"slot{pid}"))
            bond_levels.append(p.bond)

        tile = TrainingTile(
            stat_type=stat,
            support_cards=card_names,
            failure_rate=cmd.failure_rate / 100.0,
            position=pos,
            stat_gains=stat_gains,
            bond_levels=bond_levels,
            # Rainbow/gold/hint detection requires cross-referencing bond
            # tiers + partner stat specialisation; leave false for now.
        )
        tiles.append(tile)
    return tiles


def _params_to_gains(cmd: CommandInfo) -> dict[str, int]:
    gains: dict[str, int] = {}
    for p in cmd.params_inc_dec_info_array:
        tt = p.target_type
        if tt == ParamTargetType.SPEED.value:
            gains["speed"] = p.value
        elif tt == ParamTargetType.STAMINA.value:
            gains["stamina"] = p.value
        elif tt == ParamTargetType.POWER.value:
            gains["power"] = p.value
        elif tt == ParamTargetType.GUTS.value:
            gains["guts"] = p.value
        elif tt == ParamTargetType.WISDOM.value:
            gains["wit"] = p.value
        elif tt == ParamTargetType.SKILL_PT.value:
            gains["skill_pts"] = p.value
        elif tt == ParamTargetType.ENERGY.value:
            gains["energy"] = p.value
    return gains


# master.mdb ``race.grade`` integer -> bot-facing string. Values not in the
# map (e.g. 700/900/999/1000 = scenario / debut races) fall through to "".
_RACE_GRADE_LABELS: dict[int, str] = {
    100: "G1",
    200: "G2",
    300: "G3",
    400: "OP",
    800: "Pre-OP",
}


def _race_grade_to_string(grade_int: int) -> str:
    return _RACE_GRADE_LABELS.get(grade_int, "")


# Game aptitude integers run 1 (G) .. 8 (S).
_APTITUDE_LETTERS = ["", "G", "F", "E", "D", "C", "B", "A", "S"]


def _apt(v: int) -> str:
    return _APTITUDE_LETTERS[v] if 0 < v < len(_APTITUDE_LETTERS) else ""


def _aptitudes_to_letters(chara: CharaInfo) -> dict[str, str]:
    return {
        "short": _apt(chara.proper_distance_short),
        "mile": _apt(chara.proper_distance_mile),
        "medium": _apt(chara.proper_distance_middle),
        "long": _apt(chara.proper_distance_long),
        "turf": _apt(chara.proper_ground_turf),
        "dirt": _apt(chara.proper_ground_dirt),
        "nige": _apt(chara.proper_running_style_nige),
        "senko": _apt(chara.proper_running_style_senko),
        "sashi": _apt(chara.proper_running_style_sashi),
        "oikomi": _apt(chara.proper_running_style_oikomi),
    }


__all__ = [
    "CardRegistry",
    "PartnerName",
    "ResolvedPartner",
    "game_state_from_response",
]
