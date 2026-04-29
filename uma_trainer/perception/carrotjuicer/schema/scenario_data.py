"""Scenario-specific sidecar packets.

Most scenarios piggyback per-turn state on the training response via a
single top-level key (``venus_data_set``, ``live_data_set``, etc.). Each
block contains its own ``command_info_array`` plus scenario-unique arrays
like rival lists or token counters.

Source: UmaLauncher ``helper_table.py``. Field names are reverse
engineered; outer shape is HIGH confidence, inner shape is MEDIUM. Unknown
fields are preserved in ``extras``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .training_state import CommandInfo


@dataclass
class VenusDataSet:
    """Grand Masters scenario (``venus_data_set``)."""

    spirit_info_array: list = field(default_factory=list)
    # msgpack key: "spirit_info_array" - (spirit_id, spirit_num)
    venus_spirit_active_effect_info_array: list = field(default_factory=list)
    # msgpack key: "venus_spirit_active_effect_info_array"
    venus_chara_info_array: list = field(default_factory=list)
    # msgpack key: "venus_chara_info_array" - (chara_id, venus_level)
    venus_chara_command_info_array: list = field(default_factory=list)
    race_scenario: Optional[bytes] = None
    race_start_info: Optional[dict] = None
    race_reward_info: Optional[dict] = None
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {
            "spirit_info_array", "venus_spirit_active_effect_info_array",
            "venus_chara_info_array", "venus_chara_command_info_array",
            "race_scenario", "race_start_info", "race_reward_info",
        }
        return cls(
            spirit_info_array=list(raw.get("spirit_info_array", []) or []),
            venus_spirit_active_effect_info_array=list(
                raw.get("venus_spirit_active_effect_info_array", []) or []
            ),
            venus_chara_info_array=list(raw.get("venus_chara_info_array", []) or []),
            venus_chara_command_info_array=list(
                raw.get("venus_chara_command_info_array", []) or []
            ),
            race_scenario=raw.get("race_scenario"),
            race_start_info=raw.get("race_start_info"),
            race_reward_info=raw.get("race_reward_info"),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class LiveDataSet:
    """Grand Live scenario (``live_data_set``)."""

    command_info_array: list = field(default_factory=list)
    live_performance_info: Optional[dict] = None
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {"command_info_array", "live_performance_info"}
        return cls(
            command_info_array=[
                CommandInfo.from_raw(x)
                for x in raw.get("command_info_array", []) or []
            ],
            live_performance_info=raw.get("live_performance_info"),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class ArcDataSet:
    """Project L\u2019Arc scenario (``arc_data_set``)."""

    arc_rival_array: list = field(default_factory=list)
    # (chara_id, rival_boost, approval_point)
    command_info_array: list = field(default_factory=list)
    selection_info: Optional[dict] = None
    arc_info: Optional[dict] = None  # global_exp, approval_rate
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {"arc_rival_array", "command_info_array", "selection_info", "arc_info"}
        return cls(
            arc_rival_array=list(raw.get("arc_rival_array", []) or []),
            command_info_array=[
                CommandInfo.from_raw(x)
                for x in raw.get("command_info_array", []) or []
            ],
            selection_info=raw.get("selection_info"),
            arc_info=raw.get("arc_info"),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class SportDataSet:
    """UAF Ready GO! scenario (``sport_data_set``)."""

    training_array: list = field(default_factory=list)
    competition_result_array: list = field(default_factory=list)
    compe_effect_id_array: list = field(default_factory=list)
    command_info_array: list = field(default_factory=list)
    item_id_array: list = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {
            "training_array", "competition_result_array",
            "compe_effect_id_array", "command_info_array", "item_id_array",
        }
        return cls(
            training_array=list(raw.get("training_array", []) or []),
            competition_result_array=list(raw.get("competition_result_array", []) or []),
            compe_effect_id_array=list(raw.get("compe_effect_id_array", []) or []),
            command_info_array=[
                CommandInfo.from_raw(x)
                for x in raw.get("command_info_array", []) or []
            ],
            item_id_array=list(raw.get("item_id_array", []) or []),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class CookDataSet:
    """Great Food Festival scenario (``cook_data_set``)."""

    cook_info: Optional[dict] = None
    care_point_gain_num: Optional[int] = None
    material_info_array: list = field(default_factory=list)
    facility_info_array: list = field(default_factory=list)
    material_harvest_info_array: list = field(default_factory=list)
    command_info_array: list = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {
            "cook_info", "care_point_gain_num",
            "material_info_array", "facility_info_array",
            "material_harvest_info_array", "command_info_array",
        }
        return cls(
            cook_info=raw.get("cook_info"),
            care_point_gain_num=raw.get("care_point_gain_num"),
            material_info_array=list(raw.get("material_info_array", []) or []),
            facility_info_array=list(raw.get("facility_info_array", []) or []),
            material_harvest_info_array=list(
                raw.get("material_harvest_info_array", []) or []
            ),
            command_info_array=[
                CommandInfo.from_raw(x)
                for x in raw.get("command_info_array", []) or []
            ],
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class MechaDataSet:
    """Run! Mecha Umamusume scenario (``mecha_data_set``)."""

    command_info_array: list = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {"command_info_array"}
        return cls(
            command_info_array=[
                CommandInfo.from_raw(x)
                for x in raw.get("command_info_array", []) or []
            ],
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class TeamDataSet:
    """Aoharu Cup scenario team race data (``team_data_set``)."""

    race_result_array: list = field(default_factory=list)
    # win_type: 1=win, 2=loss, 3=draw
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {"race_result_array"}
        return cls(
            race_result_array=list(raw.get("race_result_array", []) or []),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class TrackblazerShopItem:
    """One entry in ``free_data_set.pick_up_item_info_array``.

    The bot consumes ``coin_num`` (current price) vs ``original_coin_num``
    (pre-discount) to detect sales, and ``limit_buy_count - item_buy_num``
    for stock remaining this rotation.
    """

    shop_item_id: int = 0
    item_id: int = 0
    coin_num: int = 0
    original_coin_num: int = 0
    item_buy_num: int = 0
    limit_buy_count: int = 0
    limit_turn: int = 0
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {
            "shop_item_id", "item_id", "coin_num", "original_coin_num",
            "item_buy_num", "limit_buy_count", "limit_turn",
        }
        return cls(
            shop_item_id=int(raw.get("shop_item_id", 0) or 0),
            item_id=int(raw.get("item_id", 0) or 0),
            coin_num=int(raw.get("coin_num", 0) or 0),
            original_coin_num=int(raw.get("original_coin_num", 0) or 0),
            item_buy_num=int(raw.get("item_buy_num", 0) or 0),
            limit_buy_count=int(raw.get("limit_buy_count", 0) or 0),
            limit_turn=int(raw.get("limit_turn", 0) or 0),
            extras={k: v for k, v in raw.items() if k not in known},
        )

    @property
    def stock_remaining(self) -> int:
        return max(0, self.limit_buy_count - self.item_buy_num)

    @property
    def is_on_sale(self) -> bool:
        return self.original_coin_num > 0 and self.coin_num < self.original_coin_num


@dataclass
class OwnedItem:
    """One entry in ``free_data_set.user_item_info_array`` (career inventory)."""

    item_id: int = 0
    num: int = 0
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {"item_id", "num"}
        return cls(
            item_id=int(raw.get("item_id", 0) or 0),
            num=int(raw.get("num", 0) or 0),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class RivalRaceInfo:
    """One entry in ``free_data_set.rival_race_info_array``."""

    chara_id: int = 0
    program_id: int = 0
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {"chara_id", "program_id"}
        return cls(
            chara_id=int(raw.get("chara_id", 0) or 0),
            program_id=int(raw.get("program_id", 0) or 0),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class FreeDataSet:
    """Trackblazer (Race Score) scenario sidecar (``free_data_set``).

    Carries the in-career shop offerings, the player's per-career inventory
    (separate from the meta-game ``item_list`` on HOME_TOP_LOAD), the coin
    balance, Trackblazer scoring (``win_points``), and the rival/twinkle
    race state. ``command_info_array`` mirrors the home_info one but with
    Trackblazer-specific scoring rather than stat gains.
    """

    coin_num: int = 0
    gained_coin_num: int = 0
    shop_id: int = 0
    sale_value: int = 0
    win_points: int = 0
    prev_win_points: int = 0
    twinkle_race_ranking: int = 0
    unchecked_event_achievement_id: Optional[int] = None

    pick_up_item_info_array: list = field(default_factory=list)  # TrackblazerShopItem[]
    user_item_info_array: list = field(default_factory=list)  # OwnedItem[]
    item_effect_array: list = field(default_factory=list)  # active item effects (None when empty)
    rival_race_info_array: list = field(default_factory=list)  # RivalRaceInfo[]
    twinkle_race_npc_info_array: list = field(default_factory=list)
    twinkle_race_npc_result_array: list = field(default_factory=list)
    command_info_array: list = field(default_factory=list)  # CommandInfo[]
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {
            "coin_num", "gained_coin_num", "shop_id", "sale_value",
            "win_points", "prev_win_points", "twinkle_race_ranking",
            "unchecked_event_achievement_id",
            "pick_up_item_info_array", "user_item_info_array",
            "item_effect_array", "rival_race_info_array",
            "twinkle_race_npc_info_array", "twinkle_race_npc_result_array",
            "command_info_array",
        }
        return cls(
            coin_num=int(raw.get("coin_num", 0) or 0),
            gained_coin_num=int(raw.get("gained_coin_num", 0) or 0),
            shop_id=int(raw.get("shop_id", 0) or 0),
            sale_value=int(raw.get("sale_value", 0) or 0),
            win_points=int(raw.get("win_points", 0) or 0),
            prev_win_points=int(raw.get("prev_win_points", 0) or 0),
            twinkle_race_ranking=int(raw.get("twinkle_race_ranking", 0) or 0),
            unchecked_event_achievement_id=raw.get("unchecked_event_achievement_id"),
            pick_up_item_info_array=[
                TrackblazerShopItem.from_raw(x)
                for x in raw.get("pick_up_item_info_array", []) or []
                if isinstance(x, dict)
            ],
            user_item_info_array=[
                OwnedItem.from_raw(x)
                for x in raw.get("user_item_info_array", []) or []
                if isinstance(x, dict)
            ],
            item_effect_array=list(raw.get("item_effect_array", []) or []),
            rival_race_info_array=[
                RivalRaceInfo.from_raw(x)
                for x in raw.get("rival_race_info_array", []) or []
                if isinstance(x, dict)
            ],
            twinkle_race_npc_info_array=list(raw.get("twinkle_race_npc_info_array", []) or []),
            twinkle_race_npc_result_array=list(raw.get("twinkle_race_npc_result_array", []) or []),
            command_info_array=[
                CommandInfo.from_raw(x)
                for x in raw.get("command_info_array", []) or []
                if isinstance(x, dict)
            ],
            extras={k: v for k, v in raw.items() if k not in known},
        )


__all__ = [
    "ArcDataSet",
    "CookDataSet",
    "FreeDataSet",
    "LiveDataSet",
    "MechaDataSet",
    "OwnedItem",
    "RivalRaceInfo",
    "SportDataSet",
    "TeamDataSet",
    "TrackblazerShopItem",
    "VenusDataSet",
]
