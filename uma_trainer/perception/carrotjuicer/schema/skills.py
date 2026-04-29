"""Skill and skill-hint dataclasses.

Three related packet sub-shapes:

1. ``chara_info.skill_array`` - skills currently owned by the trainee.
   Each entry has at minimum a skill_id and a level.
2. ``chara_info.skill_tips_array`` - revealed but not-yet-bought hints, i.e.
   discounted skill previews from support cards.
3. Request payloads for skill purchase use ``gain_skill_info_array``.

Schema confidence: HIGH on ``skill_array`` (every turn). MEDIUM on
``skill_tips_array`` (present but field names partially reverse-engineered).
LOW on the request-side purchase payload - UmaLauncher only checks the key
exists, so we capture it as a free-form dict for now.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SkillEntry:
    """Entry in ``chara_info.skill_array`` (owned skills).

    UmaLauncher iterates ``{tuple(item.values()) for item in skill_array}``
    which implies a simple dict of order-stable keys. Observed keys:
    ``skill_id`` and ``level`` (and occasionally metadata fields).
    """

    skill_id: int  # msgpack key: "skill_id"
    """Skill ID; joins to master.mdb ``skill_data``."""

    level: int = 1  # msgpack key: "level"
    """Skill level (evolves skills bump past 1)."""

    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: Any) -> "SkillEntry":
        # Some packet dumps store skills as 2-tuples [id, level] rather than
        # dicts. Handle both.
        if isinstance(raw, (list, tuple)):
            skill_id = int(raw[0]) if len(raw) > 0 else 0
            level = int(raw[1]) if len(raw) > 1 else 1
            return cls(skill_id=skill_id, level=level)
        known = {"skill_id", "level"}
        return cls(
            skill_id=int(raw.get("skill_id", 0)),
            level=int(raw.get("level", 1)),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class SkillHintEntry:
    """Entry in ``chara_info.skill_tips_array`` (revealed hints).

    UmaLauncher uses ``(hint_chara_id, skill_id, level)`` triples via
    ``mdb.get_skill_hint_name_dict``; we keep the same three fields.
    Confidence MEDIUM - UL treats unknown tuple positions generically.
    """

    chara_id: int  # msgpack key: "chara_id" (tentative)
    """ID of the partner whose hint this is (per UL\u2019s
    ``(chara_id, skill_id, level)`` tuple usage)."""

    skill_id: int  # msgpack key: "skill_id"
    """Skill ID being hinted at."""

    level: int = 1  # msgpack key: "level"
    """Hint level (1-5; higher means more discount)."""

    group_id: int = 0  # msgpack key: "group_id"
    """Skill-group id (joins to master.mdb ``skill_data.group_id``). Lets us
    detect that two hints are for the same 'family' of skills (base/evolve)."""

    rarity: int = 0  # msgpack key: "rarity"
    """Skill rarity (1=common, 2=rare, 3=unique)."""

    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: Any) -> "SkillHintEntry":
        if isinstance(raw, (list, tuple)):
            chara_id = int(raw[0]) if len(raw) > 0 else 0
            skill_id = int(raw[1]) if len(raw) > 1 else 0
            level = int(raw[2]) if len(raw) > 2 else 1
            return cls(chara_id=chara_id, skill_id=skill_id, level=level)
        known = {"chara_id", "skill_id", "level", "group_id", "rarity"}
        return cls(
            chara_id=int(raw.get("chara_id", 0)),
            skill_id=int(raw.get("skill_id", 0)),
            level=int(raw.get("level", 1)),
            group_id=int(raw.get("group_id", 0)),
            rarity=int(raw.get("rarity", 0)),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class SkillGainRequest:
    """Request payload item for buying skills (``gain_skill_info_array[]``).

    UmaLauncher only checks the outer key exists. Inner shape is assumed
    from convention. LOW confidence on field names; verify with live packet.
    """

    skill_id: int  # msgpack key: "skill_id"
    level: int = 1  # msgpack key: "level"
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: Any) -> "SkillGainRequest":
        if isinstance(raw, dict):
            known = {"skill_id", "level"}
            return cls(
                skill_id=int(raw.get("skill_id", 0)),
                level=int(raw.get("level", 1)),
                extras={k: v for k, v in raw.items() if k not in known},
            )
        return cls(skill_id=int(raw), level=1)


@dataclass
class SkillPurchaseRequest:
    """A client request that purchases one or more skills.

    Identified by presence of ``gain_skill_info_array``. Often paired with
    a corresponding response packet containing updated ``chara_info`` whose
    ``skill_array`` now includes the new skill and ``skill_point`` is reduced.
    """

    gain_skill_info_array: list[SkillGainRequest] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "SkillPurchaseRequest":
        arr = raw.get("gain_skill_info_array") or []
        return cls(gain_skill_info_array=[SkillGainRequest.from_raw(x) for x in arr])


__all__ = [
    "SkillEntry",
    "SkillHintEntry",
    "SkillGainRequest",
    "SkillPurchaseRequest",
]
