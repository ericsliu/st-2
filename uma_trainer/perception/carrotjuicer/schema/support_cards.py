"""Support card lineup and bond state on the training home screen.

Support cards appear in two places in a training response packet:

1. ``chara_info.support_card_array`` - the six equipped cards plus their
   static identifiers.
2. ``chara_info.evaluation_info_array`` - per-partner bond / evaluation
   values (bond points, 0-100) keyed by ``training_partner_id``.

UmaLauncher combines both into a single ``TrainingPartner`` view, joined
via ``support_card_array[partner_id - 1]``. ``training_partner_id`` is a
1-based index into the support_card_array (confirmed in UmaLauncher
``helper_table.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SupportCardRef:
    """A single entry in ``chara_info.support_card_array``.

    There are always six entries (five support cards + one scenario/friend
    card). ``position`` (1-based) matches ``training_partner_id`` used
    elsewhere in the packet.
    """

    support_card_id: int  # msgpack key: "support_card_id"
    """Card model ID; joins to ``support_card_data`` in master.mdb."""

    position: int = 0  # msgpack key: "position"
    """1-based slot index. Matches ``training_partner_id`` in evaluation_info
    and training_partner_array."""

    limit_break_count: int = 0  # msgpack key: "limit_break_count"
    """Card limit-break count (0-4)."""

    exp: int = 0  # msgpack key: "exp"
    """Card experience points."""

    owner_viewer_id: int = 0  # msgpack key: "owner_viewer_id"
    """0 = own card; nonzero = borrowed friend card (the friend's viewer id)."""

    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def is_friend_card(self) -> bool:
        """True when this is a borrowed friend card (not the trainee's own)."""
        return self.owner_viewer_id != 0

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "SupportCardRef":
        known = {"support_card_id", "position", "limit_break_count",
                 "exp", "owner_viewer_id"}
        return cls(
            support_card_id=int(raw.get("support_card_id", 0)),
            position=int(raw.get("position", 0)),
            limit_break_count=int(raw.get("limit_break_count", 0)),
            exp=int(raw.get("exp", 0)),
            owner_viewer_id=int(raw.get("owner_viewer_id", 0)),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class EvaluationInfo:
    """A single entry in ``chara_info.evaluation_info_array``.

    The array contains bond data for every training partner including the
    scenario/friend card. Note the evaluation value is the *raw* bond in
    0-100; the coloured ring in the UI is derived via ``BondBand``.
    """

    training_partner_id: int  # msgpack key: "training_partner_id"
    """1-based index into ``support_card_array``. 0 has been observed for
    the scenario / trainee entry in some scenarios; verify with live packet.
    """

    evaluation: int  # msgpack key: "evaluation"
    """Current bond level (0-100)."""

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "EvaluationInfo":
        return cls(
            training_partner_id=int(raw.get("training_partner_id", 0)),
            evaluation=int(raw.get("evaluation", 0)),
        )


@dataclass
class TrainingPartnerRef:
    """Per-tile participation of a support card on a training command.

    Sourced from ``home_info.command_info_array[].training_partner_array``.
    Each element identifies which partner(s) are present on the tile - these
    are the characters whose portraits appear around a training button.
    """

    training_partner_id: int  # msgpack key: "training_partner_id"
    """1-based index into ``support_card_array`` (matches EvaluationInfo)."""

    extras: dict[str, Any] = field(default_factory=dict)
    """Unknown additional fields (hint flags, rainbow indicator?) - verify live."""

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "TrainingPartnerRef":
        if isinstance(raw, int):
            # Some scenario variants emit a bare int array rather than objects.
            return cls(training_partner_id=int(raw))
        known = {"training_partner_id"}
        return cls(
            training_partner_id=int(raw.get("training_partner_id", 0)),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class TipsEventPartnerRef:
    """Entry in ``command_info_array[].tips_event_partner_array``.

    Identifies partners whose participation on this tile would trigger a
    hint / tips event rather than a plain training outcome. Used by the bot
    to score \u201crainbow\u201d hint opportunities.
    """

    training_partner_id: int  # msgpack key: "training_partner_id"

    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "TipsEventPartnerRef":
        if isinstance(raw, int):
            return cls(training_partner_id=int(raw))
        known = {"training_partner_id"}
        return cls(
            training_partner_id=int(raw.get("training_partner_id", 0)),
            extras={k: v for k, v in raw.items() if k not in known},
        )


__all__ = [
    "EvaluationInfo",
    "SupportCardRef",
    "TrainingPartnerRef",
    "TipsEventPartnerRef",
]
