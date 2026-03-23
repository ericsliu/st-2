"""Screen capture backends."""

from uma_trainer.capture.base import CaptureBackend
from uma_trainer.config import CaptureConfig


def get_capture_backend(config: CaptureConfig) -> CaptureBackend:
    """Factory: return the configured capture backend."""
    if config.backend == "scrcpy":
        from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
        return ScrcpyCapture(config)
    elif config.backend == "screencapturekit":
        from uma_trainer.capture.screencapturekit import ScreenCaptureKitCapture
        return ScreenCaptureKitCapture(config)
    else:
        raise ValueError(f"Unknown capture backend: {config.backend}")


__all__ = ["CaptureBackend", "get_capture_backend"]
