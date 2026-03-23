"""Frame preprocessing: resize, crop, normalize."""

from __future__ import annotations

import numpy as np
from PIL import Image


class FramePreprocessor:
    """Normalizes captured frames to a consistent resolution.

    Resizes to target_size (default 1280×720) and optionally crops to a
    sub-region of the emulator window before resizing.
    """

    def __init__(
        self,
        target_size: tuple[int, int] = (1280, 720),
        crop_region: tuple[int, int, int, int] | None = None,
    ) -> None:
        self.target_size = target_size  # (width, height)
        self.crop_region = crop_region  # (x, y, w, h)

    def preprocess(self, frame: np.ndarray) -> np.ndarray:
        """Apply crop (optional) then resize to target_size."""
        if self.crop_region is not None:
            frame = self._crop(frame, self.crop_region)

        h, w = frame.shape[:2]
        tw, th = self.target_size
        if w != tw or h != th:
            pil = Image.fromarray(frame[:, :, ::-1])  # BGR→RGB for PIL
            pil = pil.resize((tw, th), Image.LANCZOS)
            frame = np.array(pil)[:, :, ::-1]  # RGB→BGR

        return frame

    def _crop(self, frame: np.ndarray, region: tuple[int, int, int, int]) -> np.ndarray:
        x, y, w, h = region
        return frame[y : y + h, x : x + w]

    def extract_region(
        self, frame: np.ndarray, bbox: tuple[int, int, int, int]
    ) -> np.ndarray:
        """Extract a sub-region by bounding box (x1, y1, x2, y2)."""
        x1, y1, x2, y2 = bbox
        return frame[y1:y2, x1:x2]

    def to_pil(self, frame: np.ndarray) -> Image.Image:
        """Convert BGR numpy array to RGB PIL Image."""
        return Image.fromarray(frame[:, :, ::-1])

    def to_rgb(self, frame: np.ndarray) -> np.ndarray:
        """Convert BGR to RGB."""
        return frame[:, :, ::-1]
