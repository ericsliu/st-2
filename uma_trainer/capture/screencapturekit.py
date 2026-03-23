"""Screen capture via macOS APIs (Quartz/ScreenCaptureKit).

Falls back to `screencapture` CLI if PyObjC Quartz bindings aren't available.
"""

from __future__ import annotations

import io
import logging
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from uma_trainer.capture.base import CaptureBackend, CaptureError
from uma_trainer.capture.frame_preprocessor import FramePreprocessor
from uma_trainer.config import CaptureConfig

logger = logging.getLogger(__name__)


class ScreenCaptureKitCapture(CaptureBackend):
    """Captures the MuMuPlayer window using macOS Quartz APIs.

    Advantages over ADB screencap:
    - No ADB latency (~5ms vs ~200ms)
    - Works even if ADB is not set up
    - Captures at native display resolution

    Requires: pyobjc-framework-Quartz  (installed on macOS by requirements.txt)
    """

    def __init__(self, config: CaptureConfig) -> None:
        self.config = config
        self.preprocessor = FramePreprocessor(crop_region=config.crop_region)
        self._window_id: int | None = None
        self._use_cli_fallback = False

    def start(self) -> None:
        try:
            import Quartz  # noqa: F401
            logger.info("ScreenCaptureKit backend: using Quartz API")
        except ImportError:
            logger.warning(
                "pyobjc-framework-Quartz not available, falling back to screencapture CLI"
            )
            self._use_cli_fallback = True

        self._window_id = self._find_window_id(self.config.window_title)
        if self._window_id is None:
            logger.warning(
                "Window '%s' not found. Will capture full screen.",
                self.config.window_title,
            )

    def stop(self) -> None:
        logger.debug("ScreenCaptureKitCapture stopped")

    def grab_frame(self) -> np.ndarray:
        if self._use_cli_fallback:
            return self._grab_via_cli()
        return self._grab_via_quartz()

    def _find_window_id(self, title: str) -> int | None:
        """Find the window ID of a window whose title contains `title`."""
        try:
            import Quartz

            window_list = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID
            )
            for window in window_list:
                owner = window.get("kCGWindowOwnerName", "")
                name = window.get("kCGWindowName", "")
                if title.lower() in owner.lower() or title.lower() in name.lower():
                    wid = window.get("kCGWindowNumber")
                    logger.debug("Found window: owner=%s name=%s id=%s", owner, name, wid)
                    return wid
        except Exception as e:
            logger.debug("Window lookup failed: %s", e)
        return None

    def _grab_via_quartz(self) -> np.ndarray:
        """Capture using Quartz CGWindowListCreateImage."""
        try:
            import Quartz

            if self._window_id is not None:
                image = Quartz.CGWindowListCreateImage(
                    Quartz.CGRectNull,
                    Quartz.kCGWindowListOptionIncludingWindow,
                    self._window_id,
                    Quartz.kCGWindowImageBoundsIgnoreFraming,
                )
            else:
                # Full screen capture
                screen_bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
                image = Quartz.CGWindowListCreateImage(
                    screen_bounds,
                    Quartz.kCGWindowListOptionAll,
                    Quartz.kCGNullWindowID,
                    Quartz.kCGWindowImageDefault,
                )

            if image is None:
                raise CaptureError("CGWindowListCreateImage returned None")

            width = Quartz.CGImageGetWidth(image)
            height = Quartz.CGImageGetHeight(image)
            bpr = Quartz.CGImageGetBytesPerRow(image)

            data_provider = Quartz.CGImageGetDataProvider(image)
            raw_data = Quartz.CGDataProviderCopyData(data_provider)

            arr = np.frombuffer(raw_data, dtype=np.uint8).reshape(height, bpr // 4, 4)
            # Quartz gives BGRA, convert to BGR
            frame_bgr = arr[:, :width, :3]
            return self.preprocessor.preprocess(frame_bgr)

        except Exception as e:
            if not isinstance(e, CaptureError):
                logger.warning("Quartz capture failed (%s), trying CLI fallback", e)
                self._use_cli_fallback = True
                return self._grab_via_cli()
            raise

    def _grab_via_cli(self) -> np.ndarray:
        """Fallback: use the `screencapture` macOS CLI tool."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        cmd = ["screencapture", "-x"]
        if self._window_id is not None:
            cmd += ["-l", str(self._window_id)]
        cmd.append(tmp_path)

        try:
            subprocess.run(cmd, check=True, timeout=5)
            img = Image.open(tmp_path).convert("RGB")
            frame_bgr = np.array(img)[:, :, ::-1]
            return self.preprocessor.preprocess(frame_bgr)
        except subprocess.TimeoutExpired:
            raise CaptureError("screencapture CLI timed out")
        except Exception as e:
            raise CaptureError(f"screencapture CLI failed: {e}")
        finally:
            Path(tmp_path).unlink(missing_ok=True)
