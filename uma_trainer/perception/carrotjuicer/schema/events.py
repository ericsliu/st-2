"""Training event dataclasses.

Events appear on the response side in ``unchecked_event_array``. The bot’s
client-side choice is sent back in a follow-up request containing
``event_id`` and ``choice_number``.

The ``story_id`` embedded in each unchecked event is a concatenation of a
7-digit number; for generic skill hints UmaLauncher matches against the
pattern ``80XXXX003`` where XXXX is the hinting character id.

Source confidence: HIGH for the outer array and ``story_id`` / ``event_id``
/ ``choice_number`` fields (exercised on every event). MEDIUM for the
inner ``event_contents_info`` shape - UmaLauncher reads
``tips_training_partner_id`` off it but other inner fields are opaque.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class EventChoice:
    """Entry in ``event_contents_info.choice_array`` (if present).

    The packet seems to expose choice metadata pre-decision. UmaLauncher
    does not read per-choice fields directly - the external cjedb / GameTora
    lookup keyed on ``story_id`` is what feeds the choice titles. We keep
    whatever fields the server emits in ``extras``.
    """

    choice_number: int  # msgpack key: "choice_number" (1-indexed)
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        if isinstance(raw, int):
            return cls(choice_number=int(raw))
        known = {"choice_number"}
        return cls(
            choice_number=int(raw.get("choice_number", 0)),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class EventContentsInfo:
    """``unchecked_event_array[].event_contents_info`` - event body."""

    tips_training_partner_id: Optional[int] = None
    # msgpack key: "tips_training_partner_id" - 1-based index into
    # support_card_array when a hint event is keyed off a generic partner.
    choice_array: list = field(default_factory=list)
    # msgpack key: "choice_array"
    support_card_id: Optional[int] = None
    # msgpack key: "support_card_id" - if the event is bound to a specific card.
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        if raw is None:
            return cls()
        known = {"tips_training_partner_id", "choice_array", "support_card_id"}
        return cls(
            tips_training_partner_id=raw.get("tips_training_partner_id"),
            choice_array=[EventChoice.from_raw(x) for x in raw.get("choice_array", []) or []],
            support_card_id=raw.get("support_card_id"),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class UncheckedEvent:
    """Entry in the response ``unchecked_event_array`` - a pending event."""

    story_id: int  # msgpack key: "story_id"
    """Story id; joins to master.mdb ``single_mode_story_data`` for title.
    IDs of form ``80XXXX003`` indicate skill-hint events (XXXX = chara id)."""

    event_id: Optional[int] = None  # msgpack key: "event_id"
    """Event classifier. Known buckets per UmaLauncher:
      7005-7007 - post-race events.
    Other values are not catalogued."""

    chara_id: int = 0  # msgpack key: "chara_id"
    """Chara the event is attributed to (trainee for self events, partner
    chara_id for support-card / NPC events)."""

    play_timing: int = 0  # msgpack key: "play_timing"
    """When in the turn this event fires (1=pre-home, 2=post-home/post-race,
    etc.). Used by the game to order multi-event queues."""

    succession_event_info: Optional[Any] = None  # msgpack key: "succession_event_info"
    """Opaque payload for inheritance-chain events; None when absent."""

    minigame_result: Optional[Any] = None  # msgpack key: "minigame_result"
    """Opaque payload for minigame-driven events (e.g. UAF rhythm game).
    None when absent."""

    event_contents_info: EventContentsInfo = field(default_factory=EventContentsInfo)
    # msgpack key: "event_contents_info"

    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {
            "story_id", "event_id", "event_contents_info",
            "chara_id", "play_timing", "succession_event_info",
            "minigame_result",
        }
        return cls(
            story_id=int(raw.get("story_id", 0)),
            event_id=raw.get("event_id"),
            chara_id=int(raw.get("chara_id", 0) or 0),
            play_timing=int(raw.get("play_timing", 0) or 0),
            succession_event_info=raw.get("succession_event_info"),
            minigame_result=raw.get("minigame_result"),
            event_contents_info=EventContentsInfo.from_raw(raw.get("event_contents_info")),
            extras={k: v for k, v in raw.items() if k not in known},
        )

    @property
    def is_skill_hint(self) -> bool:
        """Skill-hint story IDs match ``80XXXX003``."""
        s = str(self.story_id)
        return len(s) == 9 and s.startswith("80") and s.endswith("003")

    @property
    def hint_chara_id(self) -> Optional[int]:
        """For skill-hint events, extract XXXX from 80XXXX003."""
        if not self.is_skill_hint:
            return None
        return int(str(self.story_id)[2:6])


@dataclass
class GainParam:
    """One reward entry inside ``ChoiceReward.gain_param_array``.

    The server packs each gain as ``display_id`` (what the UI calls it) plus
    three opaque effect values. ``display_id`` is the source of truth for
    *what kind* of reward it is (stat / motivation / item / skill / coin /
    bond / etc.); ``effect_value_0/1/2`` carry the magnitudes / target ids.
    Decoding these values fully requires master.mdb lookup keyed on
    ``display_id``.
    """

    display_id: int = 0
    effect_value_0: int = 0
    effect_value_1: int = 0
    effect_value_2: int = 0
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {"display_id", "effect_value_0", "effect_value_1", "effect_value_2"}
        return cls(
            display_id=int(raw.get("display_id", 0) or 0),
            effect_value_0=int(raw.get("effect_value_0", 0) or 0),
            effect_value_1=int(raw.get("effect_value_1", 0) or 0),
            effect_value_2=int(raw.get("effect_value_2", 0) or 0),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class ChoiceReward:
    """Entry in ``choice_reward_array`` - the server's preview of what each
    event choice grants.

    Captured on every choice-event preview turn. Allows the bot to score
    options off the live preview rather than a curated event handler.
    """

    select_index: int = 0  # 1-indexed match into the choice menu
    gain_param_array: list = field(default_factory=list)  # GainParam[]
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {"select_index", "gain_param_array"}
        return cls(
            select_index=int(raw.get("select_index", 0) or 0),
            gain_param_array=[
                GainParam.from_raw(x)
                for x in raw.get("gain_param_array", []) or []
                if isinstance(x, dict)
            ],
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class EventChoiceRequest:
    """Client request selecting an event choice.

    Identified by presence of ``event_id`` (non-zero) + ``choice_number``.
    """

    event_id: int  # msgpack key: "event_id"
    choice_number: int  # msgpack key: "choice_number"
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        known = {"event_id", "choice_number"}
        return cls(
            event_id=int(raw.get("event_id", 0)),
            choice_number=int(raw.get("choice_number", 0)),
            extras={k: v for k, v in raw.items() if k not in known},
        )


__all__ = [
    "ChoiceReward",
    "EventChoice",
    "EventChoiceRequest",
    "EventContentsInfo",
    "GainParam",
    "UncheckedEvent",
]
