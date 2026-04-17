"""Race plaque template matcher.

Given a cropped region of the race list screen containing a plaque graphic,
identify which of the ~302 known race plaques it is by multi-scale template
matching.

Approach:
  1. Load all PNG templates from ``data/race_plaques/`` (128x64 each).
  2. Coarse pass: grayscale TM_CCOEFF_NORMED against every template at a
     small set of scales. Keep the top-K candidates.
  3. Refine pass: BGR match the top-K with a denser scale grid to pick the
     final best. This gives a strong confidence margin without scanning all
     302 templates in BGR.

The plaque is scaled ~2.0-2.2x up from the template when rendered in-game at
1080x1920 portrait, so the scale windows are centred there.

On top of raw template matching, ``resolve_race()`` combines the plaque
confidence with OCR'd track features (distance, surface, direction, venue)
to disambiguate banner_ids that map to multiple race variants and to reject
plaque matches that contradict the observed track.

This module is deterministic and side-effect free -- do not import it from
``auto_turn`` at module load time if startup latency matters; the first
instantiation reads 302 PNGs (~50ms) which is fine.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

import cv2
import numpy as np
from PIL import Image


_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PLAQUE_DIR = _DEFAULT_REPO_ROOT / "data" / "race_plaques"
_DEFAULT_INDEX_PATH = _DEFAULT_REPO_ROOT / "data" / "race_plaque_index.json"
_DEFAULT_RACES_PATH = _DEFAULT_REPO_ROOT / "data" / "gametora_races.json"


# gametora_races.json encodings (see ``scripts/debug_race_data.py`` output):
#   terrain: 1 = turf, 2 = dirt
#   direction: 1 = right, 2 = left, 4 = line/straight
_TERRAIN_TO_SURFACE = {1: "turf", 2: "dirt"}
_DIRECTION_MAP = {1: "right", 2: "left", 4: "line"}

# track_id -> venue string. Derived by cross-referencing race_calendar.json
# venues with gametora_races.json track ids (each id is dominated by a single
# Japanese racecourse; a handful of races run on a second track for
# renovations). Overseas tracks added from known-race lookups.
_TRACK_TO_VENUE: dict[int, str] = {
    10001: "Sapporo",
    10002: "Hakodate",
    10003: "Niigata",
    10004: "Fukushima",
    10005: "Nakayama",
    10006: "Tokyo",
    10007: "Chukyo",
    10008: "Kyoto",
    10009: "Hanshin",
    10010: "Kokura",
    10101: "Oi",          # NAR/local - Tokyo City Keiba
    10103: "Kawasaki",
    10104: "Funabashi",
    10105: "Morioka",
    10201: "Longchamp",   # Prix de l'Arc de Triomphe
    10202: "Santa Anita", # American Oaks etc.
}


@dataclass
class MatchResult:
    banner_id: int
    race_names: list[str]
    confidence: float
    method: str = "template_bgr"
    # Secondary match for debugging/ambiguity detection.
    runner_up_banner_id: Optional[int] = None
    runner_up_confidence: float = 0.0
    scale: tuple[float, float] = (0.0, 0.0)


@dataclass
class ResolvedRace:
    """Result of combining a plaque template match with OCR'd track features.

    ``combined_confidence`` is a weighted sum of the plaque template score
    and a 0-1 feature match score. Hard-rejected candidates (distance or
    surface mismatch) are never returned; this object always represents a
    race that agrees with the observed distance + surface.
    """

    race_id: int
    race_name: str
    banner_id: int
    plaque_confidence: float
    feature_score: float
    combined_confidence: float
    # Fields from gametora_races.json that informed the decision. Useful for
    # logging and downstream race selection.
    distance: int = 0
    surface: str = ""
    direction: str = ""
    venue: str = ""
    # Top-K rank (0 = best plaque candidate). Useful to detect when we
    # overrode a higher-ranked plaque via feature disambiguation.
    plaque_rank: int = 0


@dataclass
class _Template:
    banner_id: int
    race_names: list[str]
    bgr: np.ndarray          # HxWx3 uint8
    gray: np.ndarray         # HxW uint8


# Plaque zone within a race card on the 1080x1920 race list screen.
# The plaque graphic starts near x=25 and is ~300-315 px wide.
# Card regions in auto_turn._ocr_race_list are ~180px tall and the plaque
# sits in the upper portion with some header/padding above and text below.
DEFAULT_PLAQUE_X_RANGE = (5, 360)


class PlaqueMatcher:
    """Match in-game race plaques against a library of template images."""

    # Coarse scan scales (grayscale, all templates).
    _COARSE_SCALES: tuple[float, ...] = (2.0, 2.1, 2.2)
    # Refine scales (BGR, top-K candidates).
    _REFINE_SCALES: tuple[float, ...] = (1.9, 2.0, 2.05, 2.1, 2.15, 2.2, 2.25, 2.3)
    # Number of candidates from coarse pass to refine in BGR.
    _TOP_K = 10
    # Minimum confidence to count as a match.
    DEFAULT_CONFIDENCE_THRESHOLD = 0.55

    # Weights used by ``resolve_race()`` when combining the template
    # confidence with the OCR'd track-feature score.
    PLAQUE_WEIGHT: float = 0.6
    FEATURE_WEIGHT: float = 0.4

    # Per-feature sub-weights within the feature score (sum normalised).
    FEATURE_SUB_WEIGHTS = {
        "distance": 1.0,
        "surface": 1.0,
        "direction": 0.5,
        "venue": 0.5,
    }

    def __init__(
        self,
        plaque_dir: Optional[Path] = None,
        index_path: Optional[Path] = None,
        races_path: Optional[Path] = None,
    ) -> None:
        self.plaque_dir = Path(plaque_dir) if plaque_dir else _DEFAULT_PLAQUE_DIR
        self.index_path = Path(index_path) if index_path else _DEFAULT_INDEX_PATH
        self.races_path = Path(races_path) if races_path else _DEFAULT_RACES_PATH
        self._templates: dict[int, _Template] = {}
        # banner_id -> list of race dicts (for resolve_race lookup)
        self._races_by_banner: dict[int, list[dict]] = {}
        self._load_index()
        self._load_races()

    # ------------------------------------------------------------------ load
    def _load_index(self) -> None:
        if not self.index_path.exists():
            raise FileNotFoundError(f"Plaque index not found: {self.index_path}")
        data = json.loads(self.index_path.read_text())
        missing = 0
        for entry in data:
            bid = int(entry["banner_id"])
            names = list(entry.get("race_names") or [])
            rel_file = entry.get("file") or f"race_plaques/{bid:04d}.png"
            path = self.plaque_dir.parent / rel_file if not (self.plaque_dir / f"{bid:04d}.png").exists() else (self.plaque_dir / f"{bid:04d}.png")
            # Fall back to the conventional location if index path resolution fails.
            if not path.exists():
                path = self.plaque_dir / f"{bid:04d}.png"
            if not path.exists():
                missing += 1
                continue
            bgr = self._read_bgr(path)
            if bgr is None:
                missing += 1
                continue
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            self._templates[bid] = _Template(
                banner_id=bid,
                race_names=names,
                bgr=bgr,
                gray=gray,
            )
        if not self._templates:
            raise RuntimeError(
                f"No plaque templates loaded from {self.plaque_dir}"
                f" (index has {len(data)} entries, {missing} missing)"
            )

    def _load_races(self) -> None:
        """Load ``data/gametora_races.json`` and group races by banner_id."""
        if not self.races_path.exists():
            # Not fatal -- ``match()`` still works without races data; only
            # ``resolve_race()`` requires it.
            return
        try:
            data = json.loads(self.races_path.read_text())
        except Exception:
            return
        for entry in data:
            bid = entry.get("banner_id")
            if bid is None:
                continue
            try:
                bid = int(bid)
            except (TypeError, ValueError):
                continue
            self._races_by_banner.setdefault(bid, []).append(entry)

    @staticmethod
    def _read_bgr(path: Path) -> Optional[np.ndarray]:
        arr = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if arr is None:
            return None
        if arr.ndim == 3 and arr.shape[2] == 4:
            return arr[:, :, :3].copy()
        if arr.ndim == 3:
            return arr
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)

    # ---------------------------------------------------------------- public
    @property
    def template_count(self) -> int:
        return len(self._templates)

    def banner_ids(self) -> list[int]:
        return sorted(self._templates.keys())

    def match(
        self,
        img,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        threshold: Optional[float] = None,
    ) -> Optional[MatchResult]:
        """Match the given image region against all templates.

        ``img`` may be a PIL.Image or a numpy BGR/RGB array. The region is
        defined in pixel coordinates.
        """
        crop_bgr = self._crop_to_bgr(img, x1, y1, x2, y2)
        if crop_bgr is None or crop_bgr.size == 0:
            return None
        return self._match_crop(crop_bgr, threshold=threshold)

    def match_card(
        self,
        img,
        card_region: dict,
        *,
        x_range: Sequence[int] = DEFAULT_PLAQUE_X_RANGE,
        threshold: Optional[float] = None,
    ) -> Optional[MatchResult]:
        """Match the plaque within a card region.

        ``card_region`` is a dict with ``y_range=(y_min, y_max)`` like the
        ones used by ``auto_turn._ocr_race_list``.
        """
        y_min, y_max = card_region["y_range"]
        x1, x2 = int(x_range[0]), int(x_range[1])
        return self.match(img, x1, int(y_min), x2, int(y_max), threshold=threshold)

    # ---------------------------------------------------------------- match
    def _refined_candidates(
        self,
        crop_bgr: np.ndarray,
        top_k: int,
    ) -> list[tuple[int, float, tuple[float, float]]]:
        """Run coarse+refine pipeline and return top-K (bid, score, scale).

        Results are sorted best-first. ``top_k`` is capped at the matcher's
        internal ``_TOP_K`` ceiling to keep the BGR refinement cheap.
        """
        if crop_bgr.ndim != 3 or crop_bgr.shape[2] != 3:
            return []
        crop_gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)

        k = min(top_k, self._TOP_K)

        coarse: dict[int, float] = {}
        for bid, tpl in self._templates.items():
            coarse[bid] = _best_score_gray(crop_gray, tpl.gray, self._COARSE_SCALES)

        top = sorted(coarse.items(), key=lambda kv: kv[1], reverse=True)[:k]

        refined: list[tuple[int, float, tuple[float, float]]] = []
        for bid, _ in top:
            tpl = self._templates[bid]
            score, scale = _best_score_bgr_nonuniform(
                crop_bgr, tpl.bgr, self._REFINE_SCALES
            )
            refined.append((bid, score, scale))
        refined.sort(key=lambda kv: kv[1], reverse=True)
        return refined

    def _match_crop(
        self,
        crop_bgr: np.ndarray,
        *,
        threshold: Optional[float] = None,
    ) -> Optional[MatchResult]:
        refined = self._refined_candidates(crop_bgr, top_k=self._TOP_K)
        if not refined:
            return None

        best_bid, best_score, best_scale = refined[0]
        runner_bid: Optional[int] = None
        runner_score = 0.0
        if len(refined) >= 2:
            runner_bid, runner_score, _ = refined[1]

        thr = threshold if threshold is not None else self.DEFAULT_CONFIDENCE_THRESHOLD
        if best_score < thr:
            # Still return the best so callers can log/decide; but signal via
            # None when we'd rather fall back to OCR.
            return None

        tpl = self._templates[best_bid]
        return MatchResult(
            banner_id=best_bid,
            race_names=list(tpl.race_names),
            confidence=float(best_score),
            method="template_bgr",
            runner_up_banner_id=runner_bid,
            runner_up_confidence=float(runner_score),
            scale=best_scale,
        )

    # --------------------------------------------------------- resolve_race
    def resolve_race(
        self,
        img,
        card_region: dict,
        distance: int,
        surface: str,
        *,
        direction: str = "",
        track_desc: str = "",
        top_k: int = 10,
        x_range: Sequence[int] = DEFAULT_PLAQUE_X_RANGE,
    ) -> Optional[ResolvedRace]:
        """Resolve a card to a single race using plaque match + track features.

        Arguments:
            img: PIL.Image or numpy array of the full 1080x1920 screenshot.
            card_region: dict with ``y_range=(y_min, y_max)`` for the card.
            distance: OCR'd race distance in metres (0 if unknown).
            surface: "turf" or "dirt" ("" if unknown).
            direction: "left", "right", "line" or "" if unknown.
            track_desc: raw track description string (e.g.
                "Hanshin Turf 1600m (Mile) Right / Outer"). Used only for
                venue extraction.
            top_k: number of plaque candidates to score.

        Returns None if no candidate passes the hard distance/surface
        reject, otherwise the single best (combined_confidence) race.
        """
        y_min, y_max = card_region["y_range"]
        x1, x2 = int(x_range[0]), int(x_range[1])
        crop_bgr = self._crop_to_bgr(img, x1, int(y_min), x2, int(y_max))
        if crop_bgr is None or crop_bgr.size == 0:
            return None

        candidates = self._refined_candidates(crop_bgr, top_k=top_k)
        if not candidates:
            return None

        parsed_venue = _parse_venue(track_desc)
        norm_surface = (surface or "").strip().lower()
        norm_direction = (direction or "").strip().lower()

        best: Optional[ResolvedRace] = None
        for rank, (bid, plaque_score, _scale) in enumerate(candidates):
            race_variants = self._races_by_banner.get(bid, [])
            if not race_variants:
                # Plaque template with no matching row in gametora_races.
                continue
            for race in race_variants:
                resolved = self._score_variant(
                    race=race,
                    banner_id=bid,
                    plaque_score=float(plaque_score),
                    plaque_rank=rank,
                    distance=int(distance or 0),
                    surface=norm_surface,
                    direction=norm_direction,
                    venue=parsed_venue,
                )
                if resolved is None:
                    continue
                if best is None or resolved.combined_confidence > best.combined_confidence:
                    best = resolved
        return best

    def _score_variant(
        self,
        *,
        race: dict,
        banner_id: int,
        plaque_score: float,
        plaque_rank: int,
        distance: int,
        surface: str,
        direction: str,
        venue: str,
    ) -> Optional[ResolvedRace]:
        """Score one (banner_id × race variant) combination.

        Returns None if the candidate is hard-rejected by a distance or
        surface mismatch (those fields come straight from OCR of the race
        card text and are the most reliable signals available).
        """
        race_distance = int(race.get("distance") or 0)
        race_terrain = race.get("terrain")
        race_surface = _TERRAIN_TO_SURFACE.get(race_terrain, "")
        race_direction = _DIRECTION_MAP.get(race.get("direction"), "")
        race_venue = _TRACK_TO_VENUE.get(race.get("track"), "")

        # Hard reject on distance/surface mismatch.
        if distance > 0 and race_distance > 0 and distance != race_distance:
            return None
        if surface and race_surface and surface != race_surface:
            return None

        # Weighted feature score. Only fields with ground-truth OCR data
        # contribute to the denominator, so missing fields don't penalise.
        total_weight = 0.0
        hit_weight = 0.0

        if distance > 0 and race_distance > 0:
            w = self.FEATURE_SUB_WEIGHTS["distance"]
            total_weight += w
            if distance == race_distance:
                hit_weight += w

        if surface and race_surface:
            w = self.FEATURE_SUB_WEIGHTS["surface"]
            total_weight += w
            if surface == race_surface:
                hit_weight += w

        if direction and race_direction:
            w = self.FEATURE_SUB_WEIGHTS["direction"]
            total_weight += w
            if direction == race_direction:
                hit_weight += w

        if venue and race_venue:
            w = self.FEATURE_SUB_WEIGHTS["venue"]
            total_weight += w
            if venue.lower() == race_venue.lower():
                hit_weight += w

        if total_weight > 0:
            feature_score = hit_weight / total_weight
        else:
            # No ground-truth fields at all -- treat features as neutral.
            feature_score = 0.5

        combined = (
            self.PLAQUE_WEIGHT * float(plaque_score)
            + self.FEATURE_WEIGHT * float(feature_score)
        )

        return ResolvedRace(
            race_id=int(race.get("id") or 0),
            race_name=str(race.get("name_en") or ""),
            banner_id=int(banner_id),
            plaque_confidence=float(plaque_score),
            feature_score=float(feature_score),
            combined_confidence=float(combined),
            distance=race_distance,
            surface=race_surface,
            direction=race_direction,
            venue=race_venue,
            plaque_rank=plaque_rank,
        )

    # ------------------------------------------------------------- utilities
    @staticmethod
    def _crop_to_bgr(img, x1: int, y1: int, x2: int, y2: int) -> Optional[np.ndarray]:
        if isinstance(img, Image.Image):
            arr = np.asarray(img.convert("RGB"))
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        elif isinstance(img, np.ndarray):
            if img.ndim == 3 and img.shape[2] == 3:
                bgr = img
            elif img.ndim == 3 and img.shape[2] == 4:
                bgr = img[:, :, :3]
            elif img.ndim == 2:
                bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            else:
                return None
        else:
            # Try to coerce via PIL
            try:
                pil = Image.fromarray(np.asarray(img))
                bgr = cv2.cvtColor(np.asarray(pil.convert("RGB")), cv2.COLOR_RGB2BGR)
            except Exception:
                return None
        h, w = bgr.shape[:2]
        x1 = max(0, int(x1))
        y1 = max(0, int(y1))
        x2 = min(w, int(x2))
        y2 = min(h, int(y2))
        if x2 <= x1 or y2 <= y1:
            return None
        return bgr[y1:y2, x1:x2].copy()


# ---------------------------------------------------------------- helpers
_VENUE_TOKENS: tuple[str, ...] = (
    "Sapporo",
    "Hakodate",
    "Niigata",
    "Fukushima",
    "Nakayama",
    "Tokyo",
    "Chukyo",
    "Kyoto",
    "Hanshin",
    "Kokura",
    "Oi",
    "Kawasaki",
    "Funabashi",
    "Morioka",
    "Longchamp",
    "Santa Anita",
)


def _parse_venue(track_desc: str) -> str:
    """Extract a venue name from a track description string.

    Accepts strings like "Hanshin Turf 1600m (Mile) Right / Outer" and
    returns "Hanshin". Case-insensitive. Returns "" if no known venue token
    is present.
    """
    if not track_desc:
        return ""
    low = track_desc.lower()
    # Match longest token first so "Santa Anita" wins over a false "Anita".
    for token in sorted(_VENUE_TOKENS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(token.lower())}\b", low):
            return token
    return ""


# ---------------------------------------------------------------- fast paths
def _best_score_gray(crop_gray: np.ndarray, tpl_gray: np.ndarray, scales: Sequence[float]) -> float:
    best = -1.0
    ch, cw = crop_gray.shape[:2]
    th, tw = tpl_gray.shape[:2]
    for s in scales:
        nw = int(tw * s)
        nh = int(th * s)
        if nw < 10 or nh < 10 or nw > cw or nh > ch:
            continue
        ts = cv2.resize(tpl_gray, (nw, nh), interpolation=cv2.INTER_LINEAR)
        res = cv2.matchTemplate(crop_gray, ts, cv2.TM_CCOEFF_NORMED)
        _, mx, _, _ = cv2.minMaxLoc(res)
        if mx > best:
            best = float(mx)
    return best


def _best_score_bgr_nonuniform(
    crop_bgr: np.ndarray,
    tpl_bgr: np.ndarray,
    scales: Sequence[float],
) -> tuple[float, tuple[float, float]]:
    best = -1.0
    best_scale = (0.0, 0.0)
    ch, cw = crop_bgr.shape[:2]
    th, tw = tpl_bgr.shape[:2]
    for sw in scales:
        for sh in scales:
            nw = int(tw * sw)
            nh = int(th * sh)
            if nw < 10 or nh < 10 or nw > cw or nh > ch:
                continue
            ts = cv2.resize(tpl_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
            res = cv2.matchTemplate(crop_bgr, ts, cv2.TM_CCOEFF_NORMED)
            _, mx, _, _ = cv2.minMaxLoc(res)
            if mx > best:
                best = float(mx)
                best_scale = (float(sw), float(sh))
    return best, best_scale
