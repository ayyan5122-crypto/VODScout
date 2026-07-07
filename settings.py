from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)

import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # Running as a PyInstaller executable
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    # Running from source
    PROJECT_ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = PROJECT_ROOT / "settings.json"

DOWNLOADS_DIR = PROJECT_ROOT / "downloads"
TRANSCRIPTS_DIR = PROJECT_ROOT / "transcripts"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
REPORTS_DIR = PROJECT_ROOT / "reports"
CLIPS_DIR = PROJECT_ROOT / "clips"
TEMP_DIR = PROJECT_ROOT / "temp"
ASSETS_DIR = PROJECT_ROOT / "assets"

APP_DIRECTORIES = (
    DOWNLOADS_DIR,
    TRANSCRIPTS_DIR,
    PROMPTS_DIR,
    REPORTS_DIR,
    CLIPS_DIR,
    TEMP_DIR,
    ASSETS_DIR,
)

VALID_THEMES = ("System", "Light", "Dark")
VALID_CLIP_LENGTHS = (60, 75, 90)
VALID_CHUNK_SIZES = (10, 15, 20)
VALID_WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3")
VALID_CONTENT_PROFILES = ("Horror", "FPS", "Variety", "Other")
DEFAULT_WINDOW_SIZE = "1100x760"


@dataclass(slots=True)
class AppSettings:
    last_url: str = ""
    last_selected_file: str = ""
    window_size: str = DEFAULT_WINDOW_SIZE
    theme: str = "System"
    content_profile: str = "Horror"
    clip_length: int = 60
    whisper_model: str = "base"
    chunk_size: int = 10

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSettings":
        settings = cls()
        settings.last_url = _clean_text(data.get("last_url"), settings.last_url)
        settings.last_selected_file = _clean_text(
            data.get("last_selected_file"),
            settings.last_selected_file,
        )
        settings.window_size = _clean_window_size(
            str(data.get("window_size", settings.window_size))
        )

        theme = str(data.get("theme", settings.theme))
        settings.theme = theme if theme in VALID_THEMES else settings.theme
        profile = str(data.get("content_profile", settings.content_profile))
        settings.content_profile = (
            profile if profile in VALID_CONTENT_PROFILES else settings.content_profile
        )

        settings.clip_length = _clean_choice(
            data.get("clip_length", settings.clip_length),
            VALID_CLIP_LENGTHS,
            settings.clip_length,
        )
        settings.whisper_model = _clean_model(
            str(data.get("whisper_model", settings.whisper_model)),
            settings.whisper_model,
        )
        settings.chunk_size = _clean_choice(
            data.get("chunk_size", settings.chunk_size),
            VALID_CHUNK_SIZES,
            settings.chunk_size,
        )
        return settings

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SettingsManager:
    def __init__(self, settings_file: Path = SETTINGS_FILE) -> None:
        self.settings_file = settings_file

    def load(self) -> AppSettings:
        ensure_project_directories()
        if not self.settings_file.exists():
            settings = AppSettings()
            self.save(settings)
            return settings

        try:
            with self.settings_file.open("r", encoding="utf-8") as file:
                raw_settings = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Settings could not be loaded; defaults will be used: %s", exc)
            settings = AppSettings()
            self.save(settings)
            return settings

        if not isinstance(raw_settings, dict):
            LOGGER.warning("Settings file did not contain an object; defaults will be used.")
            settings = AppSettings()
            self.save(settings)
            return settings

        settings = AppSettings.from_dict(raw_settings)
        self.save(settings)
        return settings

    def save(self, settings: AppSettings) -> None:
        ensure_project_directories()
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.settings_file.parent,
                delete=False,
                suffix=".tmp",
            ) as temp_file:
                json.dump(settings.to_dict(), temp_file, indent=2)
                temp_file.write("\n")
                temp_path = Path(temp_file.name)
            temp_path.replace(self.settings_file)
        except OSError:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise


def ensure_project_directories() -> None:
    for directory in APP_DIRECTORIES:
        directory.mkdir(parents=True, exist_ok=True)


def _clean_choice(value: Any, allowed_values: tuple[int, ...], default: int) -> int:
    try:
        clean_value = int(value)
    except (TypeError, ValueError):
        return default
    return clean_value if clean_value in allowed_values else default


def _clean_text(value: Any, default: str) -> str:
    if value is None:
        return default
    return str(value).strip()


def _clean_model(value: str, default: str) -> str:
    clean_value = value.strip()
    return clean_value if clean_value in VALID_WHISPER_MODELS else default


def _clean_window_size(value: str) -> str:
    clean_value = value.strip().lower()
    if "x" not in clean_value:
        return DEFAULT_WINDOW_SIZE

    width_text, height_text = clean_value.split("x", 1)
    try:
        width = max(800, int(width_text))
        height = max(600, int(height_text))
    except ValueError:
        return DEFAULT_WINDOW_SIZE
    return f"{width}x{height}"
