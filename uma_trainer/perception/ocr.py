"""OCR engine: Apple Vision (primary) with EasyOCR fallback."""

from __future__ import annotations

import logging
import re
import sys
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from uma_trainer.config import OCRConfig

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class OCREngine:
    """Unified OCR interface with primary/fallback strategy.

    Primary: Apple Vision framework (Neural Engine, macOS only, zero GPU cost)
    Fallback: EasyOCR (pure Python, works on any platform)
    """

    def __init__(self, config: OCRConfig) -> None:
        self.config = config
        self._primary: AppleVisionOCR | None = None
        self._fallback: EasyOCROCR | None = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        if self.config.primary == "apple_vision" and sys.platform == "darwin":
            try:
                self._primary = AppleVisionOCR()
                logger.info("OCR: Apple Vision initialized")
            except ImportError as e:
                logger.warning("Apple Vision OCR unavailable: %s", e)

        if self._primary is None or self.config.fallback_enabled:
            try:
                self._fallback = EasyOCROCR([self.config.language])
                logger.info("OCR: EasyOCR initialized (lang=%s)", self.config.language)
            except ImportError:
                logger.warning("EasyOCR not available")

        self._initialized = True

    def read_text(self, image: np.ndarray | Image.Image) -> str:
        """Extract all text from an image region."""
        self._ensure_initialized()

        if isinstance(image, np.ndarray):
            pil_image = Image.fromarray(image[:, :, ::-1])  # BGR→RGB
        else:
            pil_image = image

        if self._primary is not None:
            try:
                results = self._primary.recognize(pil_image)
                text = " ".join(t for t, _ in results).strip()
                if text:
                    return text
            except Exception as e:
                logger.debug("Apple Vision OCR failed: %s", e)

        if self._fallback is not None:
            try:
                arr = np.array(pil_image)
                results = self._fallback.recognize(arr)
                return " ".join(t for t, _ in results).strip()
            except Exception as e:
                logger.debug("EasyOCR failed: %s", e)

        return ""

    def read_number(self, image: np.ndarray | Image.Image) -> int | None:
        """Extract an integer from an image (stat values, energy, etc.)."""
        text = self.read_text(image)
        # Strip non-numeric characters, take the first run of digits
        match = re.search(r"\d+", text.replace(",", "").replace(".", ""))
        if match:
            return int(match.group())
        return None

    def read_region(
        self, frame: np.ndarray, bbox: tuple[int, int, int, int]
    ) -> str:
        """Extract text from a specific region of a frame."""
        x1, y1, x2, y2 = bbox
        region = frame[y1:y2, x1:x2]
        if region.size == 0:
            return ""
        return self.read_text(region)

    def read_number_region(
        self, frame: np.ndarray, bbox: tuple[int, int, int, int]
    ) -> int | None:
        """Extract a number from a specific region of a frame."""
        x1, y1, x2, y2 = bbox
        region = frame[y1:y2, x1:x2]
        if region.size == 0:
            return None
        return self.read_number(region)


class AppleVisionOCR:
    """OCR using Apple's Vision framework (macOS, Neural Engine accelerated).

    Requires: pyobjc-framework-Vision
    """

    def __init__(self) -> None:
        import Vision  # pyobjc-framework-Vision

        self._Vision = Vision
        logger.debug("AppleVisionOCR ready")

    def recognize(self, pil_image: Image.Image) -> list[tuple[str, float]]:
        """Run text recognition.

        Returns:
            List of (text, confidence) tuples.
        """
        import Quartz

        Vision = self._Vision

        # Convert PIL to CGImage
        img_data = pil_image.tobytes("raw", "RGB")
        width, height = pil_image.size
        bytes_per_row = width * 3
        color_space = Quartz.CGColorSpaceCreateDeviceRGB()
        data_provider = Quartz.CGDataProviderCreateWithData(None, img_data, len(img_data), None)
        cg_image = Quartz.CGImageCreate(
            width, height, 8, 24, bytes_per_row,
            color_space,
            Quartz.kCGImageAlphaNone,
            data_provider, None, False,
            Quartz.kCGRenderingIntentDefault,
        )

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(False)

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
            cg_image, {}
        )
        handler.performRequests_error_([request], None)

        results = []
        observations = request.results() or []
        for obs in observations:
            candidate = obs.topCandidates_(1)
            if candidate:
                top = candidate[0]
                results.append((str(top.string()), float(top.confidence())))

        return results


class EasyOCROCR:
    """OCR using EasyOCR library (cross-platform, CPU/MPS)."""

    def __init__(self, languages: list[str]) -> None:
        import easyocr

        # gpu=False to avoid conflicts with YOLO on MPS; EasyOCR uses CPU here
        self._reader = easyocr.Reader(languages, gpu=False, verbose=False)
        logger.debug("EasyOCROCR ready (languages=%s)", languages)

    def recognize(self, image: np.ndarray) -> list[tuple[str, float]]:
        """Run text recognition.

        Returns:
            List of (text, confidence) tuples.
        """
        results = self._reader.readtext(image)
        return [(text, float(conf)) for (_bbox, text, conf) in results]
