"""Low-level ADB wrapper for Android emulator control."""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)


class ADBClient:
    """Thin wrapper around ADB shell commands.

    Uses subprocess rather than adb-shell library so it works with any ADB
    version (including the one bundled with MuMuPlayer).
    """

    def __init__(self, device_serial: str = "") -> None:
        self.device_serial = device_serial.strip()
        self._prefix = self._build_prefix()

    def _build_prefix(self) -> list[str]:
        if self.device_serial:
            return ["adb", "-s", self.device_serial]
        return ["adb"]

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to ADB device. Auto-connects TCP devices if needed."""
        # If device_serial looks like a TCP address, try adb connect first
        if self.device_serial and ":" in self.device_serial:
            try:
                result = subprocess.run(
                    ["adb", "connect", self.device_serial],
                    capture_output=True, text=True, timeout=10,
                )
                out = result.stdout.strip()
                if "connected" in out or "already connected" in out:
                    logger.info("ADB auto-connected to %s", self.device_serial)
                else:
                    logger.warning("ADB connect attempt: %s", out)
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                logger.error("ADB connect command failed: %s", e)
                return False

        # Verify device is available
        try:
            result = self._run(["get-state"], timeout=5)
            if "device" in result.stdout:
                logger.info("ADB connected (device=%s)", self.device_serial or "default")
                return True
            logger.warning("ADB device not ready: %s", result.stdout.strip())
            return False
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.error("ADB connect failed: %s", e)
            return False

    def is_connected(self) -> bool:
        """Quick check if device is still responsive."""
        try:
            result = self._run(["get-state"], timeout=3)
            return result.returncode == 0 and "device" in result.stdout
        except Exception:
            return False

    def wait_for_device(self, timeout: float = 30.0) -> bool:
        """Block until device becomes available or timeout expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_connected():
                return True
            time.sleep(2.0)
        return False

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def tap(self, x: int, y: int) -> None:
        """Send a single tap at (x, y)."""
        self._run(["shell", "input", "tap", str(x), str(y)])
        logger.debug("ADB tap(%d, %d)", x, y)

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 200,
    ) -> None:
        """Send a swipe gesture."""
        self._run(
            ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)]
        )
        logger.debug("ADB swipe(%d,%d → %d,%d, %dms)", x1, y1, x2, y2, duration_ms)

    def key_event(self, keycode: int) -> None:
        """Send a key event (Android KeyEvent constants)."""
        self._run(["shell", "input", "keyevent", str(keycode)])

    def back(self) -> None:
        """Send the Android back button (KEYCODE_BACK = 4)."""
        self.key_event(4)

    def home(self) -> None:
        """Send the Android home button (KEYCODE_HOME = 3)."""
        self.key_event(3)

    # ------------------------------------------------------------------
    # Screen capture (used by ScrcpyCapture)
    # ------------------------------------------------------------------

    def screenshot(self) -> bytes:
        """Return raw PNG bytes of the current screen."""
        result = self._run(["exec-out", "screencap", "-p"], timeout=10, text=False)
        return result.stdout

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(
        self,
        args: list[str],
        timeout: float = 10.0,
        text: bool = True,
    ) -> subprocess.CompletedProcess:
        cmd = self._prefix + args
        return subprocess.run(
            cmd,
            capture_output=True,
            text=text,
            timeout=timeout,
        )
