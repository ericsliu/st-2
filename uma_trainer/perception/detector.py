"""YOLO-based object detection for game UI elements."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from uma_trainer.config import YOLOConfig
from uma_trainer.perception.class_map import (
    CLASS_NAMES,
    SCREEN_ANCHOR_CLASSES,
)
from uma_trainer.types import ScreenState

logger = logging.getLogger(__name__)


@dataclass
class Detection:
    """A single YOLO detection result."""

    class_name: str
    class_id: int
    confidence: float
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    @property
    def width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> int:
        return self.bbox[3] - self.bbox[1]

    def overlaps(self, other: "Detection", iou_threshold: float = 0.1) -> bool:
        """Check if this detection overlaps with another."""
        ax1, ay1, ax2, ay2 = self.bbox
        bx1, by1, bx2, by2 = other.bbox
        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)
        if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
            return False
        inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
        area_a = self.width * self.height
        area_b = other.width * other.height
        union_area = area_a + area_b - inter_area
        return (inter_area / union_area) >= iou_threshold

    def contains_point(self, x: int, y: int) -> bool:
        x1, y1, x2, y2 = self.bbox
        return x1 <= x <= x2 and y1 <= y <= y2


class YOLODetector:
    """Runs YOLO inference on game frames.

    Uses ultralytics YOLO with optional CoreML backend for M1 GPU acceleration.
    Falls back to a stub mode when the model file is not present, returning
    empty detections (allows the rest of the pipeline to function during dev).
    """

    def __init__(self, config: YOLOConfig) -> None:
        self.config = config
        self._model = None
        self._stub_mode = False

    def load_model(self) -> None:
        """Load the YOLO model. Called once at startup."""
        model_path = Path(self.config.model_path)

        if not model_path.exists():
            logger.warning(
                "YOLO model not found at %s — running in stub mode (no detections). "
                "Train the model first: python scripts/train_yolo.py",
                model_path,
            )
            self._stub_mode = True
            return

        try:
            from ultralytics import YOLO

            self._model = YOLO(str(model_path))
            logger.info("YOLO model loaded from %s", model_path)
        except ImportError:
            logger.error("ultralytics not installed. Run: pip install ultralytics")
            self._stub_mode = True

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Run inference on a BGR frame and return detections.

        Args:
            frame: BGR numpy array (H, W, 3)

        Returns:
            List of Detection objects above the confidence threshold.
        """
        if self._stub_mode or self._model is None:
            return []

        try:
            results = self._model.predict(
                source=frame,
                conf=self.config.confidence_threshold,
                device=self.config.device,
                verbose=False,
            )
        except Exception as e:
            logger.error("YOLO inference failed: %s", e)
            return []

        detections: list[Detection] = []
        for result in results:
            if result.boxes is None:
                continue
            boxes = result.boxes.xyxy.cpu().numpy().astype(int)
            confs = result.boxes.conf.cpu().numpy()
            cls_ids = result.boxes.cls.cpu().numpy().astype(int)

            for (x1, y1, x2, y2), conf, cls_id in zip(boxes, confs, cls_ids):
                class_name = (
                    CLASS_NAMES[cls_id]
                    if cls_id < len(CLASS_NAMES)
                    else f"class_{cls_id}"
                )
                detections.append(
                    Detection(
                        class_name=class_name,
                        class_id=int(cls_id),
                        confidence=float(conf),
                        bbox=(int(x1), int(y1), int(x2), int(y2)),
                    )
                )

        logger.debug("Detected %d objects", len(detections))
        return detections

    def detect_screen_state(self, detections: list[Detection]) -> ScreenState:
        """Infer the current game screen from anchor class detections.

        Uses the highest-confidence anchor class detection to determine which
        screen is currently displayed.
        """
        best_screen = ScreenState.UNKNOWN
        best_conf = 0.0

        for det in detections:
            if det.class_name in SCREEN_ANCHOR_CLASSES:
                if det.confidence > best_conf:
                    best_conf = det.confidence
                    best_screen = SCREEN_ANCHOR_CLASSES[det.class_name]

        return best_screen

    def filter_by_class(
        self, detections: list[Detection], class_name: str
    ) -> list[Detection]:
        """Return only detections of a specific class."""
        return [d for d in detections if d.class_name == class_name]

    def filter_by_classes(
        self, detections: list[Detection], class_names: set[str]
    ) -> list[Detection]:
        """Return detections matching any of the given class names."""
        return [d for d in detections if d.class_name in class_names]

    def get_best(
        self, detections: list[Detection], class_name: str
    ) -> Detection | None:
        """Return the highest-confidence detection of a given class."""
        matches = self.filter_by_class(detections, class_name)
        if not matches:
            return None
        return max(matches, key=lambda d: d.confidence)
