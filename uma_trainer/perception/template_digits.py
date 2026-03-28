"""Gain digit reader using template matching against game sprite assets.

Uses the actual game sprites (extracted from the APK) as templates,
matching against HSV-isolated orange pixels in gain regions. This is
far more reliable than OCR for the stylized orange gradient digits.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Default path to digit template sprites
_DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "digit_templates"

# Normalize all glyphs to this height for IoU comparison
_TARGET_H = 48

# Minimum IoU score to accept a glyph match
_MIN_IOU = 0.30

# Minimum glyph height as fraction of region height
_MIN_HEIGHT_RATIO = 0.25


class TemplateDigitReader:
    """Read gain numbers (+N) by matching game sprite templates."""

    def __init__(self, template_dir: Path | None = None) -> None:
        self._template_dir = template_dir or _DEFAULT_TEMPLATE_DIR
        self._templates: dict[str, np.ndarray] | None = None

    def _ensure_loaded(self) -> None:
        if self._templates is not None:
            return
        self._templates = {}
        for name in ["plus"] + [str(i) for i in range(10)]:
            path = self._template_dir / f"digit_{name}.png"
            rgba = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if rgba is None:
                logger.warning("Missing template: %s", path)
                continue

            # Extract orange fill from the sprite (matches what we see on screen)
            bgr = rgba[:, :, :3]
            alpha = rgba[:, :, 3]
            hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
            orange = cv2.inRange(hsv, (5, 50, 100), (40, 255, 255))
            white = cv2.inRange(hsv, (0, 0, 200), (180, 60, 255))
            mask = cv2.bitwise_or(orange, white)
            mask = cv2.bitwise_and(mask, alpha)
            _, mask = cv2.threshold(mask, 50, 255, cv2.THRESH_BINARY)

            # Normalize to target height
            h, w = mask.shape
            scale = _TARGET_H / h
            new_w = max(1, int(w * scale))
            resized = cv2.resize(mask, (new_w, _TARGET_H),
                                 interpolation=cv2.INTER_AREA)
            _, resized = cv2.threshold(resized, 128, 255, cv2.THRESH_BINARY)

            label = "+" if name == "plus" else name
            self._templates[label] = resized

        logger.info("Loaded %d digit templates", len(self._templates))

    def read_gain_region(
        self,
        frame: np.ndarray,
        bbox: tuple[int, int, int, int],
    ) -> int | None:
        """Read a gain value (+N) from a region of a frame.

        Args:
            frame: Full frame (BGR, 1080x1920).
            bbox: (x1, y1, x2, y2) region containing the gain number.

        Returns:
            The gain value (1-50), or None if no gain detected.
        """
        x1, y1, x2, y2 = bbox
        region = frame[y1:y2, x1:x2]
        if region.size == 0:
            return None
        return self.read_gain(region)

    def read_gain(self, region: np.ndarray) -> int | None:
        """Read a gain value from a BGR image region.

        Args:
            region: BGR image containing a "+N" gain number.

        Returns:
            The gain value (1-50), or None if no gain detected.
        """
        self._ensure_loaded()
        if not self._templates:
            return None

        glyphs = self._segment_glyphs(region)
        if not glyphs:
            return None

        results = []
        for x, _y, _w, _h, mask in glyphs:
            label, score = self._match_glyph(mask)
            if score >= _MIN_IOU:
                results.append((x, label, score))

        if not results:
            return None

        text = "".join(r[1] for r in results)
        return self._parse_gain_text(text)

    @staticmethod
    def _get_orange_mask(bgr: np.ndarray) -> np.ndarray:
        """Isolate orange/yellow gain digit pixels via HSV filtering."""
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        orange = cv2.inRange(hsv, (5, 50, 120), (38, 255, 255))
        return orange

    def _segment_glyphs(
        self, region: np.ndarray
    ) -> list[tuple[int, int, int, int, np.ndarray]]:
        """Find individual glyph bounding boxes using vertical projection.

        Uses only orange pixels (not white) to avoid connecting adjacent
        glyphs through their outlines.

        Returns:
            List of (x, y, w, h, mask) tuples sorted left-to-right.
        """
        orange = self._get_orange_mask(region)
        h_region = region.shape[0]

        # Vertical projection: count orange pixels per column
        col_sums = np.sum(orange > 0, axis=0)

        # Find runs of columns with orange pixels.
        # Use higher threshold (>3 pixels) to reject sparse background noise.
        runs: list[list[int]] = []
        in_run = False
        for x in range(len(col_sums)):
            if col_sums[x] > 3:
                if not in_run:
                    runs.append([x, x])
                    in_run = True
                else:
                    runs[-1][1] = x
            elif in_run:
                in_run = False

        # Merge runs with gaps < 4px (likely same glyph)
        merged_runs: list[list[int]] = []
        for run in runs:
            if merged_runs and run[0] - merged_runs[-1][1] < 4:
                merged_runs[-1][1] = run[1]
            else:
                merged_runs.append(run)

        # Max expected glyph width at 1080p (~35px for widest digits)
        max_glyph_w = 40
        # Min pixel density: real digits have solid fill, noise is sparse.
        # At 1080p, a gain digit glyph has ~30-40% of its bounding box
        # filled with orange pixels.
        min_density = 0.20

        glyphs = []
        for start, end in merged_runs:
            w = end - start + 1
            strip = orange[:, start:end + 1]
            row_sums = np.sum(strip > 0, axis=1)
            ys = np.where(row_sums > 0)[0]
            if len(ys) == 0:
                continue
            y_top = ys[0]
            y_bot = ys[-1] + 1
            h = y_bot - y_top

            if h < h_region * _MIN_HEIGHT_RATIO or w <= 3:
                continue

            candidates = []
            if w <= max_glyph_w:
                candidates.append((start, y_top, w, h))
            else:
                # Oversized blob: split by finding column valleys
                sub_runs = self._split_wide_blob(
                    col_sums[start:end + 1], max_glyph_w
                )
                for sub_start, sub_end in sub_runs:
                    sw = sub_end - sub_start + 1
                    sub_strip = orange[:, start + sub_start:start + sub_end + 1]
                    sub_row_sums = np.sum(sub_strip > 0, axis=1)
                    sub_ys = np.where(sub_row_sums > 0)[0]
                    if len(sub_ys) == 0:
                        continue
                    sy_top = sub_ys[0]
                    sy_bot = sub_ys[-1] + 1
                    sh = sy_bot - sy_top
                    if sh > h_region * _MIN_HEIGHT_RATIO and sw > 3:
                        candidates.append(
                            (start + sub_start, sy_top, sw, sh)
                        )

            for gx, gy, gw, gh in candidates:
                mask = orange[gy:gy + gh, gx:gx + gw]
                density = np.count_nonzero(mask) / (gw * gh)
                if density >= min_density:
                    glyphs.append((gx, gy, gw, gh, mask))

        return glyphs

    @staticmethod
    def _split_wide_blob(
        col_sums: np.ndarray, max_w: int
    ) -> list[tuple[int, int]]:
        """Split an oversized column-sum array into sub-glyphs.

        Finds local minima in the column projection to split at.
        """
        w = len(col_sums)
        if w <= max_w:
            return [(0, w - 1)]

        # Find the minimum column value in each potential split zone
        # Look for valleys (low points) to split at
        min_val = col_sums.min()
        threshold = min_val + (col_sums.max() - min_val) * 0.2

        # Find split points: columns with low density
        splits = []
        in_glyph = False
        glyph_start = 0
        for x in range(w):
            if col_sums[x] > threshold:
                if not in_glyph:
                    glyph_start = x
                    in_glyph = True
            else:
                if in_glyph:
                    splits.append((glyph_start, x - 1))
                    in_glyph = False
        if in_glyph:
            splits.append((glyph_start, w - 1))

        # Merge splits that are still too close
        merged = []
        for s in splits:
            if merged and s[0] - merged[-1][1] < 3:
                merged[-1] = (merged[-1][0], s[1])
            else:
                merged.append(s)

        return merged if merged else [(0, w - 1)]

    def _match_glyph(self, glyph_mask: np.ndarray) -> tuple[str, float]:
        """Match a single glyph against all templates using IoU.

        Returns:
            (label, iou_score) tuple.
        """
        h, w = glyph_mask.shape
        if h < 5 or w < 3:
            return "?", 0.0

        scale = _TARGET_H / h
        new_w = max(1, int(w * scale))
        resized = cv2.resize(glyph_mask, (new_w, _TARGET_H),
                             interpolation=cv2.INTER_AREA)
        _, resized = cv2.threshold(resized, 128, 255, cv2.THRESH_BINARY)

        best_label = "?"
        best_score = 0.0

        for label, tmpl in self._templates.items():
            # Pad both to same width, centered
            max_w = max(resized.shape[1], tmpl.shape[1]) + 8
            glyph_padded = np.zeros((_TARGET_H, max_w), dtype=np.uint8)
            tmpl_padded = np.zeros((_TARGET_H, max_w), dtype=np.uint8)

            gx = (max_w - resized.shape[1]) // 2
            glyph_padded[:, gx:gx + resized.shape[1]] = resized
            tx = (max_w - tmpl.shape[1]) // 2
            tmpl_padded[:, tx:tx + tmpl.shape[1]] = tmpl

            # Try a few horizontal offsets for alignment tolerance
            best_iou = 0.0
            for offset in range(-2, 3):
                shifted = np.zeros_like(glyph_padded)
                src_start = max(0, -offset)
                dst_start = max(0, offset)
                copy_w = min(max_w - dst_start, max_w - src_start)
                shifted[:, dst_start:dst_start + copy_w] = (
                    glyph_padded[:, src_start:src_start + copy_w]
                )

                intersection = np.count_nonzero(shifted & tmpl_padded)
                union = np.count_nonzero(shifted | tmpl_padded)
                if union > 0:
                    best_iou = max(best_iou, intersection / union)

            if best_iou > best_score:
                best_score = best_iou
                best_label = label

        return best_label, best_score

    @staticmethod
    def _parse_gain_text(text: str) -> int | None:
        """Parse recognized text into a gain value.

        Expects patterns like "+13", "+5", "13", "5".
        Only accepts values 1-50.
        """
        # Strip leading "+" if present
        if text.startswith("+"):
            text = text[1:]

        # Extract digits
        digits = "".join(c for c in text if c.isdigit())
        if not digits:
            return None

        val = int(digits)
        if 1 <= val <= 50:
            return val
        return None
