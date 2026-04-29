"""Map raw ``support_card_id`` values to the semantic keys the bot uses
internally.

The OCR-based ``CardTracker`` identifies cards by sprite match against
named PNGs in ``data/card_templates/`` (``team_sirius``, ``riko``, etc.).
The playbook YAML and ``TrainingScorer`` use those same keys for
friendship-priority logic. When we drive the bot from packets instead,
we have ``support_card_id`` integers but no built-in mapping back to
those keys — so curate one in ``data/support_card_semantic.yaml``.

Schema::

    # data/support_card_semantic.yaml
    30074: team_sirius
    30056: riko
    ...

Cards not in the file fall through to the upstream default
(localized display name from ``master.mdb`` or a ``card_<id>``
placeholder), preserving backwards compatibility when the mapping is
incomplete.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_PATH = Path("data/support_card_semantic.yaml")


@lru_cache(maxsize=4)
def load_card_semantic_map(path: str | None = None) -> dict[int, str]:
    """Return the curated ``support_card_id → semantic_key`` mapping.

    Missing or empty file → empty dict (no translation applied).
    """
    target = Path(path) if path else DEFAULT_PATH
    if not target.exists():
        return {}
    raw = yaml.safe_load(target.read_text()) or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[int, str] = {}
    for k, v in raw.items():
        try:
            out[int(k)] = str(v)
        except (TypeError, ValueError):
            continue
    return out


__all__ = ["load_card_semantic_map", "DEFAULT_PATH"]
