"""Compass settings.

The vault path comes from kai's own configuration (same OBSIDIAN_VAULT_PATH
key, same .env lookup), so one vault setting drives both tools. Compass owns
a separate DuckDB file so it never contends for kai's write lock (ADR 0006).
"""

from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from obsidian_ai_tools import config as kai_config


def _default_db_path(data: dict[str, Any]) -> Path:
    vault_path: Path = data["obsidian_vault_path"]
    return vault_path / ".kai" / "compass.duckdb"


def _default_topics_path(data: dict[str, Any]) -> Path:
    vault_path: Path = data["obsidian_vault_path"]
    return vault_path / ".kai" / "topics.yaml"


class CompassSettings(BaseSettings):
    """Settings for `compass serve`, read from the environment and kai's .env."""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    obsidian_vault_path: Path = Field(..., description="Absolute path to Obsidian vault")
    # Declared after obsidian_vault_path: the factory reads the validated vault path.
    compass_db_path: Path = Field(
        default_factory=_default_db_path,
        description="Compass DuckDB file; defaults to {vault}/.kai/compass.duckdb",
    )
    compass_topics_path: Path = Field(
        default_factory=_default_topics_path,
        description="Topic definitions; defaults to {vault}/.kai/topics.yaml",
    )

    @field_validator("obsidian_vault_path")
    @classmethod
    def validate_vault_path(cls, v: Path) -> Path:
        """Ensure vault path exists and is a directory."""
        if not v.is_dir():
            raise ValueError(f"Vault path is not an existing directory: {v}")
        return v.resolve()


def get_compass_settings() -> CompassSettings:
    """Load settings, reading the same .env file kai would use (if any)."""
    # Looked up through the module so tests that patch kai's lookup apply here too.
    env_file = kai_config.find_env_file()
    return CompassSettings(_env_file=env_file)  # type: ignore[call-arg]
