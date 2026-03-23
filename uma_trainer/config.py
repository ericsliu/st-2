"""Configuration loading and validation using Pydantic."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings


class CaptureConfig(BaseModel):
    backend: Literal["scrcpy", "screencapturekit"] = "scrcpy"
    device_serial: str = ""
    window_title: str = "MuMuPlayer"
    fps_decision: float = 1.5
    fps_passive: float = 0.2
    crop_region: tuple[int, int, int, int] | None = None  # x, y, w, h


class YOLOConfig(BaseModel):
    model_path: str = "models/uma_yolo.mlpackage"
    confidence_threshold: float = 0.50
    use_coreml: bool = True
    device: str = "mps"

    @model_validator(mode="after")
    def check_model_exists(self) -> "YOLOConfig":
        path = Path(self.model_path)
        if not path.exists():
            # Not a hard error — model may not be trained yet
            import warnings
            warnings.warn(
                f"YOLO model not found at {self.model_path}. "
                "Run scripts/train_yolo.py first, or the bot will use stub detection."
            )
        return self


class OCRConfig(BaseModel):
    primary: Literal["apple_vision", "easyocr"] = "apple_vision"
    fallback_enabled: bool = True
    language: str = "en"


class LLMConfig(BaseModel):
    local_model: str = "phi4-mini:q4_K_M"
    ollama_host: str = "http://localhost:11434"
    claude_model: str = "claude-sonnet-4-6"
    claude_api_key: str = Field(default="", exclude=True)  # Never serialized
    claude_daily_limit: int = 5
    cache_ttl_hours: int = 168  # 1 week

    @model_validator(mode="after")
    def load_api_key_from_env(self) -> "LLMConfig":
        if not self.claude_api_key:
            self.claude_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        return self


class ScorerConfig(BaseModel):
    stat_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "speed": 1.2,
            "stamina": 1.0,
            "power": 0.9,
            "guts": 0.8,
            "wit": 0.7,
        }
    )
    rainbow_bonus: float = 2.0
    gold_bonus: float = 1.5
    hint_bonus: float = 1.2
    card_stack_per_card: float = 0.8
    energy_penalty_threshold: int = 30
    rest_energy_threshold: int = 20
    bond_priority_turns: int = 24


class AppConfig(BaseModel):
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    yolo: YOLOConfig = Field(default_factory=YOLOConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    scorer: ScorerConfig = Field(default_factory=ScorerConfig)
    db_path: str = "data/uma_trainer.db"
    log_level: str = "INFO"
    web_port: int = 8080
    headless: bool = False


def load_config(path: str = "config/default.yaml") -> AppConfig:
    """Load configuration from YAML file, with environment variable overrides."""
    config_path = Path(path)

    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    else:
        import warnings
        warnings.warn(f"Config file {path} not found, using defaults.")
        raw = {}

    # Apply env var overrides for top-level scalar fields
    if os.environ.get("LOG_LEVEL"):
        raw["log_level"] = os.environ["LOG_LEVEL"]
    if os.environ.get("ADB_DEVICE_SERIAL"):
        raw.setdefault("capture", {})["device_serial"] = os.environ["ADB_DEVICE_SERIAL"]
    if os.environ.get("OLLAMA_HOST"):
        raw.setdefault("llm", {})["ollama_host"] = os.environ["OLLAMA_HOST"]

    return AppConfig.model_validate(raw)
