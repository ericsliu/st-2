"""Track support card identities and bond levels across turns via portrait matching.

Each support card has a unique character portrait on training preview screens.
The tracker crops these portraits, builds a template library at runtime, and
matches subsequent sightings to known cards. Bond levels are updated on every
sighting (bond only goes up, so we take the max).

Portrait regions: x=940..1060, y=bar_y-130..bar_y-10 for each of 6 slots.
Uses cv2.matchTemplate (TM_CCOEFF_NORMED) with threshold 0.70.
"""

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Portrait crop regions (1080x1920 training preview screen).
# Bar y-centers match read_bond_levels in pixel_analysis.py.
BAR_Y_CENTERS = [424, 604, 784, 964, 1144, 1324]
# Portrait circle is centered ~x=965, ~65px above the bond bar.
# Crop a 130x130 square centered on the circle.
PORTRAIT_X1 = 900
PORTRAIT_X2 = 1030
PORTRAIT_Y_OFFSET = 132
PORTRAIT_Y_BOTTOM = 4

MATCH_THRESHOLD = 0.70
NAMED_MATCH_THRESHOLD = 0.90  # High threshold to avoid cross-card false positives

# Named card templates directory (parallel to data/npc_templates/).
_CARD_TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "card_templates"


def _load_named_templates() -> list[tuple[str, np.ndarray, np.ndarray | None]]:
    """Load named support card portrait templates from data/card_templates/.

    Templates may be RGBA (with alpha channel for background transparency).
    Returns list of (name, bgr_array, mask_or_None) tuples. Cached after first call.
    """
    if hasattr(_load_named_templates, "_cache"):
        return _load_named_templates._cache

    import cv2
    templates = []
    if _CARD_TEMPLATE_DIR.is_dir():
        for png in sorted(_CARD_TEMPLATE_DIR.glob("*.png")):
            tmpl = cv2.imread(str(png), cv2.IMREAD_UNCHANGED)
            if tmpl is None:
                continue
            if tmpl.shape[2] == 4:
                # RGBA — extract alpha as mask, convert to BGR
                mask = tmpl[:, :, 3]
                bgr = tmpl[:, :, :3]
                logger.info("Loaded card template: %s (%s, with alpha mask)", png.stem, bgr.shape[:2])
            else:
                bgr = tmpl
                mask = None
                logger.info("Loaded card template: %s (%s)", png.stem, bgr.shape[:2])
            templates.append((png.stem, bgr, mask))

    _load_named_templates._cache = templates
    return templates


class CardTracker:
    """Identifies support cards by portrait sprite and tracks their bond levels."""

    def __init__(self):
        self._templates: list[tuple[str, np.ndarray]] = []
        self._bonds: dict[str, int] = {}
        self._next_id = 0

    def reset(self):
        """Clear all tracked cards (new career run)."""
        self._templates.clear()
        self._bonds.clear()
        self._next_id = 0

    def identify_cards(
        self,
        frame_bgr: np.ndarray,
        n_cards: int,
        bond_levels: list[int],
    ) -> list[str]:
        """Identify cards on a training tile and update bond tracking.

        Args:
            frame_bgr: Full 1080x1920 BGR frame from training preview.
            n_cards: Number of support cards detected on this tile.
            bond_levels: Bond percentages per card (0-100), ordered top-to-bottom.

        Returns:
            List of card_id strings matching each detected card.
        """
        import cv2
        from uma_trainer.perception.pixel_analysis import _is_npc_portrait

        card_ids = []
        bond_idx = 0

        for slot in range(min(n_cards, len(BAR_Y_CENTERS))):
            bar_y = BAR_Y_CENTERS[slot]
            y1 = max(0, bar_y - PORTRAIT_Y_OFFSET)
            y2 = bar_y - PORTRAIT_Y_BOTTOM
            x1 = PORTRAIT_X1
            x2 = PORTRAIT_X2

            if y2 > frame_bgr.shape[0] or x2 > frame_bgr.shape[1]:
                bond_idx += 1
                continue

            # Skip NPC portraits (Director, Reporter)
            npc = _is_npc_portrait(frame_bgr, (x1, y1, x2, y2))
            if npc:
                logger.debug("Slot %d is NPC '%s' — skipping", slot, npc)
                bond_idx += 1
                continue

            portrait = frame_bgr[y1:y2, x1:x2].copy()

            # Check named templates first (e.g., team_sirius, riko)
            card_id = self._match_named(portrait, cv2)
            if card_id is None:
                # Fall back to runtime-registered templates
                card_id = self._match(portrait, cv2)

            if card_id is None:
                card_id = f"card_{self._next_id}"
                self._next_id += 1
                self._templates.append((card_id, portrait))
                logger.info("New card registered: %s (slot %d)", card_id, slot)

            # Update bond — take max since bond only goes up
            bond = bond_levels[bond_idx] if bond_idx < len(bond_levels) else 0
            prev = self._bonds.get(card_id, 0)
            self._bonds[card_id] = max(prev, bond)
            bond_idx += 1

            card_ids.append(card_id)

        return card_ids

    def _match_named(self, portrait: np.ndarray, cv2) -> str | None:
        """Match a portrait against pre-saved named card templates.

        Templates may have an alpha mask (from RGBA PNGs) that marks which
        pixels are meaningful. Background pixels (alpha=0) are excluded from
        matching so that varying background colors don't affect the score.
        """
        named = _load_named_templates()
        if not named:
            return None

        best_score = 0.0
        best_name = None
        ph, pw = portrait.shape[:2]

        for name, tmpl, tmpl_mask in named:
            th, tw = tmpl.shape[:2]
            if th != ph or tw != pw:
                tmpl_resized = cv2.resize(tmpl, (pw, ph))
                mask_resized = cv2.resize(tmpl_mask, (pw, ph)) if tmpl_mask is not None else None
            else:
                tmpl_resized = tmpl
                mask_resized = tmpl_mask

            if mask_resized is not None:
                # Apply mask to both: zero out background pixels
                mask_3ch = cv2.merge([mask_resized, mask_resized, mask_resized])
                tmpl_fg = cv2.bitwise_and(tmpl_resized, mask_3ch)
                portrait_fg = cv2.bitwise_and(portrait, mask_3ch)
                result = cv2.matchTemplate(portrait_fg, tmpl_fg, cv2.TM_CCOEFF_NORMED)
            else:
                result = cv2.matchTemplate(portrait, tmpl_resized, cv2.TM_CCOEFF_NORMED)

            score = float(result.max())
            logger.debug("Named card match '%s': score=%.3f", name, score)

            if score > best_score:
                best_score = score
                best_name = name

        if best_score >= NAMED_MATCH_THRESHOLD:
            logger.info("Matched named card '%s' (score=%.3f)", best_name, best_score)
            return best_name

        return None

    def _match(self, portrait: np.ndarray, cv2) -> str | None:
        """Match a portrait crop against known card templates."""
        if not self._templates:
            return None

        best_score = 0.0
        best_id = None

        for card_id, template in self._templates:
            th, tw = template.shape[:2]
            ph, pw = portrait.shape[:2]
            if th != ph or tw != pw:
                tmpl = cv2.resize(template, (pw, ph))
            else:
                tmpl = template

            result = cv2.matchTemplate(portrait, tmpl, cv2.TM_CCOEFF_NORMED)
            score = float(result.max())

            if score > best_score:
                best_score = score
                best_id = card_id

        if best_score >= MATCH_THRESHOLD:
            logger.debug("Matched %s (score=%.3f)", best_id, best_score)
            return best_id

        return None

    def get_bond(self, card_id: str) -> int:
        """Get last known bond level for a card."""
        return self._bonds.get(card_id, 0)

    def all_bonds_maxed(self) -> bool:
        """True if all known support cards have bond >= 80."""
        if not self._bonds:
            return False
        return all(b >= 80 for b in self._bonds.values())

    @property
    def card_count(self) -> int:
        """Number of unique cards registered so far."""
        return len(self._templates)

    def has_friendship(self, card_name: str) -> bool:
        """True if a named card has reached friendship level (bond >= 80)."""
        return self._bonds.get(card_name, 0) >= 80

    def is_tracked(self, card_name: str) -> bool:
        """True if a card with this name has been seen at least once."""
        return card_name in self._bonds

    def summary(self) -> str:
        """Human-readable bond tracking summary."""
        if not self._bonds:
            return "no cards tracked"
        parts = [f"{cid}={bond}%" for cid, bond in sorted(self._bonds.items())]
        n_maxed = sum(1 for b in self._bonds.values() if b >= 80)
        status = "ALL MAXED" if self.all_bonds_maxed() else f"{n_maxed}/{len(self._bonds)} maxed"
        return f"{status} [{', '.join(parts)}]"
