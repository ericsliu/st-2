"""Training home-screen state: the ``home_info`` object.

On the training-home response, the packet carries a ``home_info`` block
listing every training tile the trainee can currently click. Each tile
entry (``command_info_array[i]``) carries its projected stat gains
(``params_inc_dec_info_array``), its failure rate, and the support cards
present on that tile (``training_partner_array``).

Source confidence: HIGH for the top-level shape. Inner field names on
scenario-specific deltas (L\u2019Arc ``add_global_exp``, UAF
``gain_sport_rank_array``, etc.) are MEDIUM confidence; they are preserved
in ``extras`` so downstream code can pick them up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .support_cards import TipsEventPartnerRef, TrainingPartnerRef


@dataclass
class ParamsIncDecInfo:
    """Entry in ``command_info_array[].params_inc_dec_info_array``.

    Represents a single stat delta a training tile is projected to give
    (e.g. +12 Speed). The mapping from ``target_type`` -> stat is in
    ``enums.ParamTargetType``.
    """

    target_type: int  # msgpack key: "target_type"
    """Which stat / resource this delta applies to. See ParamTargetType."""

    value: int  # msgpack key: "value"
    """Signed delta. Stat bumps are positive; energy drains are negative."""

    @classmethod
    def from_raw(cls, raw):
        return cls(
            target_type=int(raw.get("target_type", 0)),
            value=int(raw.get("value", 0)),
        )


@dataclass
class CommandInfo:
    """Entry in ``home_info.command_info_array`` - a single training tile.

    Scenario-specific sidecars such as ``performance_inc_dec_info_array``
    (Grand Live) and ``add_global_exp`` (L\u2019Arc) land in ``extras``.
    """

    command_id: int  # msgpack key: "command_id"
    command_type: int = 0  # msgpack key: "command_type"
    """1 = training tile, 3 = rest/recreation/infirmary, etc."""
    level: int = 1  # msgpack key: "level"
    failure_rate: int = 0  # msgpack key: "failure_rate"
    is_enable: int = 1  # msgpack key: "is_enable"
    """0 = tile disabled this turn (e.g. summer camp recreation, injured rest-only)."""
    params_inc_dec_info_array: list = field(default_factory=list)
    training_partner_array: list = field(default_factory=list)
    tips_event_partner_array: list = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    @property
    def is_training(self) -> bool:
        return self.command_type == 1

    @classmethod
    def from_raw(cls, raw):
        known = {
            "command_id", "command_type", "level", "failure_rate", "is_enable",
            "params_inc_dec_info_array",
            "training_partner_array", "tips_event_partner_array",
        }
        return cls(
            command_id=int(raw.get("command_id", 0)),
            command_type=int(raw.get("command_type", 0)),
            level=int(raw.get("level", 1)),
            failure_rate=int(raw.get("failure_rate", 0)),
            is_enable=int(raw.get("is_enable", 1)),
            params_inc_dec_info_array=[
                ParamsIncDecInfo.from_raw(x)
                for x in raw.get("params_inc_dec_info_array", []) or []
            ],
            training_partner_array=[
                TrainingPartnerRef.from_raw(x)
                for x in raw.get("training_partner_array", []) or []
            ],
            tips_event_partner_array=[
                TipsEventPartnerRef.from_raw(x)
                for x in raw.get("tips_event_partner_array", []) or []
            ],
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class CommandResult:
    """Post-training / post-rest ``command_result`` block.

    Emitted on the response to a ``REQUEST_COMMAND`` submission. Tells us
    what tile was executed (``command_id``) and a coarse outcome
    (``result_state``: 1 = normal success, 2 = good/great performance,
    3 = failure, etc. Exact enum TBD from more captures).
    The actual stat deltas live in the attached ``chara_info`` (diff the
    previous turn) — this block is the signal that a command *resolved*,
    not a gains summary.
    """

    command_id: int = 0  # msgpack key: "command_id"
    sub_id: int = 0  # msgpack key: "sub_id"
    result_state: int = 0  # msgpack key: "result_state"
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        if not isinstance(raw, dict):
            return cls()
        known = {"command_id", "sub_id", "result_state"}
        return cls(
            command_id=int(raw.get("command_id", 0)),
            sub_id=int(raw.get("sub_id", 0)),
            result_state=int(raw.get("result_state", 0)),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class ParameterBoundInfo:
    """``not_up_parameter_info`` / ``not_down_parameter_info`` - stat soft caps.

    Tracks what CANNOT go up (or down) this turn. The server uses these
    to mark skills/conditions that are frozen — e.g. a condition that
    prevents Guts gains, or a skill whose level is pinned. Each array is
    a list of IDs of the corresponding type.

    Most fields are empty in normal captures but the shape is consistent
    across every response. Bot use: detect when a stat is blocked to
    avoid wasting a training tile on it.
    """

    status_type_array: list = field(default_factory=list)
    chara_effect_id_array: list = field(default_factory=list)
    skill_id_array: list = field(default_factory=list)
    skill_tips_array: list = field(default_factory=list)
    skill_lv_id_array: list = field(default_factory=list)
    evaluation_chara_id_array: list = field(default_factory=list)
    command_lv_array: list = field(default_factory=list)
    has_chara_effect_id_array: list = field(default_factory=list)
    unsupported_evaluation_chara_id_array: list = field(default_factory=list)
    not_gain_chara_effect_array: list = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        if not isinstance(raw, dict):
            return cls()
        known = {
            "status_type_array", "chara_effect_id_array", "skill_id_array",
            "skill_tips_array", "skill_lv_id_array",
            "evaluation_chara_id_array", "command_lv_array",
            "has_chara_effect_id_array",
            "unsupported_evaluation_chara_id_array",
            "not_gain_chara_effect_array",
        }
        return cls(
            status_type_array=list(raw.get("status_type_array", []) or []),
            chara_effect_id_array=list(raw.get("chara_effect_id_array", []) or []),
            skill_id_array=list(raw.get("skill_id_array", []) or []),
            skill_tips_array=list(raw.get("skill_tips_array", []) or []),
            skill_lv_id_array=list(raw.get("skill_lv_id_array", []) or []),
            evaluation_chara_id_array=list(raw.get("evaluation_chara_id_array", []) or []),
            command_lv_array=list(raw.get("command_lv_array", []) or []),
            has_chara_effect_id_array=list(raw.get("has_chara_effect_id_array", []) or []),
            unsupported_evaluation_chara_id_array=list(raw.get("unsupported_evaluation_chara_id_array", []) or []),
            not_gain_chara_effect_array=list(raw.get("not_gain_chara_effect_array", []) or []),
            extras={k: v for k, v in raw.items() if k not in known},
        )

    @property
    def is_empty(self) -> bool:
        return not any((
            self.status_type_array, self.chara_effect_id_array,
            self.skill_id_array, self.skill_tips_array,
            self.skill_lv_id_array, self.evaluation_chara_id_array,
            self.command_lv_array, self.has_chara_effect_id_array,
            self.unsupported_evaluation_chara_id_array,
            self.not_gain_chara_effect_array,
        ))


@dataclass
class HomeInfo:
    """The ``home_info`` block on a training-home response."""

    command_info_array: list = field(default_factory=list)
    disable_command_id_array: list = field(default_factory=list)
    """Commands whose tile is greyed out (e.g. races locked this turn)."""
    race_entry_restriction: int = 0
    """0 = no restriction, 1 = race-only turn, 2 = summer camp / no race, ..."""
    shortened_race_state: int = 0
    """Skip-race animation toggle state."""
    available_continue_num: int = 0
    """Number of continue items available (revive on failed goal race)."""
    available_free_continue_num: int = 0
    free_continue_num: int = 0
    free_continue_time: int = 0
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {
            "command_info_array", "disable_command_id_array",
            "race_entry_restriction", "shortened_race_state",
            "available_continue_num", "available_free_continue_num",
            "free_continue_num", "free_continue_time",
        }
        return cls(
            command_info_array=[
                CommandInfo.from_raw(x)
                for x in raw.get("command_info_array", []) or []
            ],
            disable_command_id_array=list(raw.get("disable_command_id_array", []) or []),
            race_entry_restriction=int(raw.get("race_entry_restriction", 0)),
            shortened_race_state=int(raw.get("shortened_race_state", 0)),
            available_continue_num=int(raw.get("available_continue_num", 0)),
            available_free_continue_num=int(raw.get("available_free_continue_num", 0)),
            free_continue_num=int(raw.get("free_continue_num", 0)),
            free_continue_time=int(raw.get("free_continue_time", 0)),
            extras={k: v for k, v in raw.items() if k not in known},
        )


__all__ = [
    "CommandInfo",
    "CommandResult",
    "HomeInfo",
    "ParameterBoundInfo",
    "ParamsIncDecInfo",
]
