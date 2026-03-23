"""Screen capture via ADB screencap (works with scrcpy/MuMuPlayer)."""

from __future__ import annotations

import io
import logging
import subprocess

import numpy as np
from PIL import Image

from uma_trainer.capture.base import CaptureBackend, CaptureError
from uma_trainer.capture.frame_preprocessor import FramePreprocessor
from uma_trainer.config import CaptureConfig

logger = logging.getLogger(__name__)


class ScrcpyCapture(CaptureBackend):
    """Captures frames via `adb exec-out screencap -p`.

    This is compatible with MuMuPlayer (Android emulator) and real Android
    devices connected via USB or TCP. No scrcpy process needs to be running —
    the name is historical (scrcpy sets up the ADB connection).
    """

    def __init__(self, config: CaptureConfig) -> None:
        self.config = config
        self.preprocessor = FramePreprocessor(crop_region=config.crop_region)
        self._adb_prefix: list[str] = []
        self._connected = False

    def start(self) -> None:
        serial = self.config.device_serial.strip()
        if serial:
            self._adb_prefix = ["adb", "-s", serial]
        else:
            self._adb_prefix = ["adb"]

        # Verify ADB connection
        try:
            result = subprocess.run(
                self._adb_prefix + ["get-state"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0 or "device" not in result.stdout:
                raise CaptureError(
                    f"ADB device not ready: {result.stdout.strip()} {result.stderr.strip()}"
                )
            self._connected = True
            logger.info("ADB capture backend connected (device: %s)", serial or "default")
        except FileNotFoundError:
            raise CaptureError(
                "adb command not found. Install Android platform-tools and ensure "
                "adb is in your PATH."
            )
        except subprocess.TimeoutExpired:
            raise CaptureError("ADB connection timed out. Is the emulator running?")

    def stop(self) -> None:
        self._connected = False
        logger.debug("ScrcpyCapture stopped")

    def grab_frame(self) -> np.ndarray:
        if not self._connected:
            raise CaptureError("Not connected. Call start() first.")

        try:
            result = subprocess.run(
                self._adb_prefix + ["exec-out", "screencap", "-p"],
                capture_output=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            raise CaptureError("screencap timed out")

        if result.returncode != 0:
            raise CaptureError(f"screencap failed: {result.stderr.decode()[:200]}")

        raw_bytes = result.stdout

        # ADB on Windows emits \r\n line endings in binary streams — strip them
        if b"\r\n" in raw_bytes[:32]:
            raw_bytes = raw_bytes.replace(b"\r\n", b"\n")

        try:
            img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
        except Exception as e:
            raise CaptureError(f"Failed to decode screencap PNG: {e}")

        # PIL is RGB, OpenCV/numpy convention is BGR
        frame_bgr = np.array(img)[:, :, ::-1]
        return self.preprocessor.preprocess(frame_bgr)

    def is_connected(self) -> bool:
        """Check if ADB device is still responsive."""
        try:
            result = subprocess.run(
                self._adb_prefix + ["get-state"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            return result.returncode == 0 and "device" in result.stdout
        except Exception:
            return False
