"""OCR engine: Apple Vision (primary) with EasyOCR fallback."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from uma_trainer.config import OCRConfig

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Directory for gain OCR sample collection
_GAIN_LOG_DIR = Path("data/gain_ocr_samples")


class OCREngine:
    """Unified OCR interface with primary/fallback strategy.

    Primary: Apple Vision framework (Neural Engine, macOS only, zero GPU cost)
    Fallback: EasyOCR (pure Python, works on any platform)
    """

    def __init__(self, config: OCRConfig) -> None:
        self.config = config
        self._primary: AppleVisionOCR | None = None
        self._fallback: EasyOCROCR | None = None
        self._template_reader = None
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

    def _to_pil(self, image: np.ndarray | Image.Image) -> Image.Image:
        if isinstance(image, np.ndarray):
            return Image.fromarray(image[:, :, ::-1])  # BGR→RGB
        return image

    def read_text(self, image: np.ndarray | Image.Image) -> str:
        """Extract all text from an image region."""
        self._ensure_initialized()
        pil_image = self._to_pil(image)

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

    def read_text_gain_hints(self, image: np.ndarray | Image.Image) -> str:
        """Extract text using gain-specific vocabulary hints (+1 through +50).

        Falls back to regular read_text if gain-hinted recognition fails.
        """
        self._ensure_initialized()
        pil_image = self._to_pil(image)

        if self._primary is not None:
            try:
                results = self._primary.recognize_gains(pil_image)
                text = " ".join(t for t, _ in results).strip()
                if text:
                    return text
            except Exception as e:
                logger.debug("Apple Vision gain-hinted OCR failed: %s", e)

        # Fall back to regular recognition
        return self.read_text(image)

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

    # Common OCR letter→digit substitutions (game font confusions)
    _OCR_DIGIT_MAP = str.maketrans("OoIlCcSsZz", "0011000522")

    def read_gain_number(self, image: np.ndarray | Image.Image) -> int | None:
        """Extract a stat gain value from a '+N' image.

        The game displays gains as '+N' where the '+' is a bold cross symbol.
        Apple Vision sometimes misreads the '+' as '4', '$', '6', or other
        characters. Digit glyphs can also be confused with letters (e.g.
        '10' → 'IC', '0' → 'O').

        Gains are always 1–99 in practice (up to ~50 base, higher with
        item boosts); values outside this range are treated as misreads.
        """
        text = self.read_text(image)
        return self._parse_gain_text(text)

    def _get_template_reader(self):
        """Lazy-load the template digit reader."""
        if self._template_reader is None:
            try:
                from uma_trainer.perception.template_digits import (
                    TemplateDigitReader,
                )
                self._template_reader = TemplateDigitReader()
                logger.info("Template digit reader loaded")
            except Exception as e:
                logger.warning("Template digit reader unavailable: %s", e)
                self._template_reader = False  # sentinel: tried and failed
        return self._template_reader if self._template_reader else None

    def read_gain_region(
        self, frame: np.ndarray, bbox: tuple[int, int, int, int]
    ) -> int | None:
        """Extract a stat gain value from a '+N' region of a frame.

        Strategy:
        1. Template matching against game sprite digits (fast, accurate)
        2. Gain-hinted OCR on 3x upscale (Apple Vision with "+1".."+50" hints)
        3. Regular OCR on 3x upscale
        4. Regular OCR on raw region
        Prefers whichever read detected a '+' symbol.

        Every read is logged to data/gain_ocr_samples/ with the crop image
        and all OCR variants for later review and training data collection.
        """
        x1, y1, x2, y2 = bbox
        region = frame[y1:y2, x1:x2]
        if region.size == 0:
            return None

        # Attempt 0: template matching (most reliable for gain digits)
        tmpl_reader = self._get_template_reader()
        if tmpl_reader is not None:
            tmpl_result = tmpl_reader.read_gain_region(frame, bbox)
            if tmpl_result is not None:
                self._log_gain_sample(region, bbox, "", "", "",
                                     tmpl_result, "template")
                return tmpl_result

        # Fall back to OCR-based approaches
        self._ensure_initialized()
        preprocessed = self._preprocess_number_region(region)

        # Attempt 1: gain-hinted OCR (custom vocabulary "+1".."+50")
        hinted_text = self.read_text_gain_hints(preprocessed).strip()
        hinted_result = self._parse_gain_text(hinted_text)

        # If hinted OCR found a clean "+N" match, trust it immediately
        if hinted_result is not None and re.search(r"[+＋]", hinted_text):
            self._log_gain_sample(region, bbox, hinted_text, "", "",
                                 hinted_result, "hinted")
            return hinted_result

        # Attempt 2 & 3: regular OCR on upscaled and raw
        up_text = self.read_text(preprocessed).strip()
        raw_text = self.read_text(region).strip()

        up_result = self._parse_gain_text(up_text)
        raw_result = self._parse_gain_text(raw_text)

        up_has_plus = bool(re.search(r"[+＋]", up_text))
        raw_has_plus = bool(re.search(r"[+＋]", raw_text))

        if raw_has_plus and not up_has_plus:
            result = raw_result if raw_result is not None else up_result
            self._log_gain_sample(region, bbox, hinted_text, up_text,
                                 raw_text, result, "raw_plus")
            return result
        if up_has_plus and not raw_has_plus:
            result = up_result if up_result is not None else raw_result
            self._log_gain_sample(region, bbox, hinted_text, up_text,
                                 raw_text, result, "up_plus")
            return result

        # Use hinted result as tiebreaker if available
        if hinted_result is not None:
            self._log_gain_sample(region, bbox, hinted_text, up_text,
                                 raw_text, hinted_result, "hinted_fallback")
            return hinted_result

        # Both or neither have '+'; prefer upscaled
        result = up_result if up_result is not None else raw_result
        if result is not None or up_text or raw_text:
            self._log_gain_sample(region, bbox, hinted_text, up_text,
                                 raw_text, result, "fallback")
        return result

    @staticmethod
    def _log_gain_sample(
        region: np.ndarray,
        bbox: tuple[int, int, int, int],
        hinted_text: str,
        up_text: str,
        raw_text: str,
        parsed: int | None,
        method: str,
    ) -> None:
        """Save a gain OCR sample for later review / training data."""
        try:
            import cv2

            _GAIN_LOG_DIR.mkdir(parents=True, exist_ok=True)
            ts = int(time.time() * 1000)
            img_name = f"{ts}_{bbox[0]}_{bbox[1]}.png"
            cv2.imwrite(str(_GAIN_LOG_DIR / img_name), region)

            entry = {
                "ts": ts,
                "bbox": list(bbox),
                "hinted": hinted_text,
                "upscaled": up_text,
                "raw": raw_text,
                "parsed": parsed,
                "method": method,
                "img": img_name,
            }
            log_path = _GAIN_LOG_DIR / "log.jsonl"
            with open(log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # never fail the OCR pipeline due to logging

    def _parse_gain_text(self, text: str) -> int | None:
        """Parse gain text using the same logic as read_gain_number but from a string.

        When training items boost gains, OCR may return two "+N" values
        (e.g. "+13 +33").  All "+N" patterns are found and summed to get
        the actual total gain.
        """
        cleaned = text.replace(",", "").replace(".", "").strip()
        if re.search(r"[\d+＋$]", cleaned):
            cleaned_digits = cleaned.translate(self._OCR_DIGIT_MAP)
        else:
            cleaned_digits = cleaned

        # Find ALL "+N" patterns and sum them (handles boosted gains)
        plus_matches = re.findall(r"[+＋]\s*(\d+)", cleaned_digits)
        if plus_matches:
            total = sum(int(m) for m in plus_matches)
            if 1 <= total <= 99:
                return total

        # "$N" misread of "+N"
        m = re.search(r"[$]\s*(\d+)", cleaned_digits)
        if m:
            return int(m.group(1))
        # "4N" misread of "+N"
        m = re.match(r"4(\d+)$", cleaned_digits)
        if m:
            return int(m.group(1))
        # Bare number fallback — no "+" found, so this is a single value.
        # Values > 50 with 2+ digits are likely misreads (e.g. "69" = "+9"),
        # so strip the leading digit.
        m = re.search(r"\d+", cleaned_digits)
        if m:
            val = int(m.group())
            digits = m.group()
            if val > 50 and len(digits) >= 2:
                stripped = int(digits[1:])
                if 1 <= stripped <= 50:
                    return stripped
                return val
            if val <= 50 and len(digits) >= 2:
                return val
        return None

    def read_number_region(
        self, frame: np.ndarray, bbox: tuple[int, int, int, int]
    ) -> int | None:
        """Extract a number from a specific region of a frame.

        Applies preprocessing (upscale + threshold) to improve OCR
        accuracy on the game's gradient/shadowed stat font.
        """
        x1, y1, x2, y2 = bbox
        region = frame[y1:y2, x1:x2]
        if region.size == 0:
            return None

        # Preprocess: upscale 3x and binarize for cleaner digit recognition
        preprocessed = self._preprocess_number_region(region)
        result = self.read_number(preprocessed)
        if result is not None:
            return result

        # Fallback: try raw region
        return self.read_number(region)

    @staticmethod
    def _preprocess_number_region(region: np.ndarray) -> np.ndarray:
        """Upscale a region for better digit OCR.

        The game uses gradient-colored stat numbers with shadows.
        Simple 3x upscale preserves the original colors which Apple Vision
        handles better than binarized versions.
        """
        import cv2

        h, w = region.shape[:2]
        return cv2.resize(region, (w * 3, h * 3), interpolation=cv2.INTER_LANCZOS4)


class AppleVisionOCR:
    """OCR using Apple's Vision framework (macOS, Neural Engine accelerated).

    Requires: pyobjc-framework-Vision
    """

    def __init__(self) -> None:
        import Vision  # pyobjc-framework-Vision

        self._Vision = Vision
        logger.debug("AppleVisionOCR ready")

    # Pre-built custom words for gain regions: "+1" through "+99"
    _GAIN_CUSTOM_WORDS = [f"+{i}" for i in range(1, 100)]

    def _pil_to_cgimage(self, pil_image: Image.Image):
        """Convert PIL image to CGImage for Vision framework."""
        import Quartz

        img_data = pil_image.tobytes("raw", "RGB")
        width, height = pil_image.size
        bytes_per_row = width * 3
        color_space = Quartz.CGColorSpaceCreateDeviceRGB()
        data_provider = Quartz.CGDataProviderCreateWithData(
            None, img_data, len(img_data), None
        )
        return Quartz.CGImageCreate(
            width, height, 8, 24, bytes_per_row,
            color_space,
            Quartz.kCGImageAlphaNone,
            data_provider, None, False,
            Quartz.kCGRenderingIntentDefault,
        )

    def _run_request(self, cg_image, custom_words=None):
        """Run a VNRecognizeTextRequest and return results."""
        Vision = self._Vision
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(
            Vision.VNRequestTextRecognitionLevelAccurate
        )
        request.setUsesLanguageCorrection_(False)

        if custom_words:
            request.setCustomWords_(custom_words)

        handler = (
            Vision.VNImageRequestHandler
            .alloc()
            .initWithCGImage_options_(cg_image, {})
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

    def recognize(self, pil_image: Image.Image) -> list[tuple[str, float]]:
        """Run text recognition.

        Returns:
            List of (text, confidence) tuples.
        """
        cg_image = self._pil_to_cgimage(pil_image)
        return self._run_request(cg_image)

    def recognize_gains(self, pil_image: Image.Image) -> list[tuple[str, float]]:
        """Run text recognition with gain-specific vocabulary hints.

        Provides "+1" through "+50" as custom words so Apple Vision
        is biased toward recognizing gain patterns like "+11", "+17", etc.
        """
        cg_image = self._pil_to_cgimage(pil_image)
        return self._run_request(cg_image, custom_words=self._GAIN_CUSTOM_WORDS)


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
