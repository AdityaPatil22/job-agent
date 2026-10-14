"""Configuration file loading and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import CandidateProfile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
PROFILE_PATH = CONFIG_DIR / "profile.json"
FIELD_MAPPINGS_PATH = CONFIG_DIR / "field_mappings.json"


class ConfigError(RuntimeError):
    """Raised when local configuration is missing or invalid."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(
            f"Configuration file not found: {path}. Copy the matching .example.json file "
            "and edit it with your details."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path} at line {exc.lineno}: {exc.msg}") from exc

    if not isinstance(value, dict):
        raise ConfigError(f"Expected a JSON object in {path}")
    return value


def load_profile(path: str | Path | None = None) -> CandidateProfile:
    """Load and validate the candidate profile."""
    profile_path = Path(path).expanduser() if path else PROFILE_PATH
    data = _read_json(profile_path)
    try:
        return CandidateProfile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Invalid candidate profile in {profile_path}: {exc}") from exc


def load_field_mappings(path: str | Path | None = None) -> dict[str, list[str]]:
    """Load case-insensitive form-label aliases."""
    mappings_path = Path(path).expanduser() if path else FIELD_MAPPINGS_PATH
    raw = _read_json(mappings_path)
    normalized: dict[str, list[str]] = {}

    for key, aliases in raw.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if isinstance(aliases, str):
            aliases = [aliases]
        if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
            raise ConfigError(f"Aliases for {key!r} must be a list of strings")
        normalized[key.strip().lower()] = [
            item.strip().lower() for item in aliases if item.strip()
        ]

    return normalized
