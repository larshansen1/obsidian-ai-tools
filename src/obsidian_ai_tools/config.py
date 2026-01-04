"""Configuration management using pydantic-settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_env_file() -> Path | None:
    """Find .env file in current or parent directories."""
    current = Path.cwd()
    root = Path("/")

    while current != root:
        env_path = current / ".env"
        if env_path.exists():
            return env_path
        current = current.parent

    # Check home directory as fallback
    home_env = Path.home() / ".kai" / ".env"
    if home_env.exists():
        return home_env

    return None


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=find_env_file() or ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # OpenRouter Configuration
    openrouter_api_key: str = Field(..., description="OpenRouter API key for LLM access")

    # Obsidian Vault Configuration
    obsidian_vault_path: Path = Field(..., description="Absolute path to Obsidian vault")
    obsidian_inbox_folder: str = Field(
        default="inbox", description="Folder within vault for new notes"
    )

    # LLM Configuration
    llm_model: str = Field(
        default="anthropic/claude-3.5-sonnet",
        description="OpenRouter model identifier",
    )
    max_transcript_length: int = Field(
        default=50000, description="Maximum transcript length in characters"
    )

    # YouTube API Configuration
    youtube_api_key: str | None = Field(
        default=None, description="YouTube Data API v3 key (optional)"
    )
    decodo_api_key: str | None = Field(
        default=None, description="Decodo API key for transcript scraping (optional)"
    )
    supadata_key: str | None = Field(
        default=None, description="Supadata API key for YouTube transcripts (optional)"
    )
    youtube_transcript_provider_order: str = Field(
        default="direct,supadata,decodo",
        description="Comma-separated list of transcript providers (direct,supadata,decodo)",
    )

    @field_validator("youtube_transcript_provider_order")
    @classmethod
    def validate_provider_order(cls, v: str) -> str:
        """Validate provider order contains only valid provider names."""
        valid_providers = {"direct", "supadata", "decodo"}
        providers = [p.strip() for p in v.split(",")]
        invalid = set(providers) - valid_providers
        if invalid:
            raise ValueError(
                f"Invalid provider name(s): {invalid}. Valid options: {valid_providers}"
            )
        if not providers:
            raise ValueError("Provider order cannot be empty")
        return v


    # Proxy Configuration (for youtube-transcript-api)
    proxy_host: str | None = Field(default=None, description="Proxy host (e.g., gate.decodo.com)")
    proxy_port: int | None = Field(default=None, description="Proxy port (e.g., 7000)")
    proxy_username: str | None = Field(default=None, description="Proxy username")
    proxy_password: str | None = Field(default=None, description="Proxy password")

    # Cache Configuration
    cache_dir: Path = Field(default=Path(".cache"), description="Directory for cache files")
    cache_ttl_hours: int = Field(
        default=168, description="Cache time-to-live in hours (default: 7 days)"
    )

    # Circuit Breaker Configuration
    circuit_breaker_threshold: int = Field(default=3, description="Failures before quarantine")
    circuit_breaker_timeout_hours: int = Field(
        default=2, description="Quarantine duration in hours"
    )

    # PDF Configuration
    max_pdf_pages: int = Field(default=50, description="Maximum PDF pages to process")
    max_pdf_size_mb: int = Field(default=20, description="Maximum PDF file size in MB")

    @field_validator("obsidian_vault_path")
    @classmethod
    def validate_vault_path(cls, v: Path) -> Path:
        """Ensure vault path exists."""
        if not v.exists():
            raise ValueError(f"Vault path does not exist: {v}")
        if not v.is_dir():
            raise ValueError(f"Vault path is not a directory: {v}")
        return v.resolve()


@lru_cache
def get_settings() -> Settings:
    """Get application settings (cached singleton).

    Raises:
        RuntimeError: If .env file is not found or configuration is invalid
    """
    # First check if .env file exists
    env_file = find_env_file()

    if env_file is None:
        raise RuntimeError(
            "❌ Could not find .env file.\n\n"
            "The kai command requires a .env file with your configuration.\n\n"
            "Options:\n"
            "1. Run kai from your project directory (where .env is located)\n"
            "2. Create a global config at ~/.kai/.env\n"
            "3. Use command-line flags like --vault to override settings\n\n"
            "See README.md for .env file setup instructions."
        )

    try:
        return Settings()  # type: ignore[call-arg]
    except Exception as e:
        # Provide helpful context for configuration errors
        error_msg = str(e)
        if "validation error" in error_msg.lower():
            raise RuntimeError(
                f"❌ Configuration error in {env_file}\n\n"
                f"{error_msg}\n\n"
                "Please check that your .env file contains all required settings:\n"
                "- OBSIDIAN_VAULT_PATH=/path/to/your/vault\n"
                "- OPENROUTER_API_KEY=your_key_here\n\n"
                "See README.md for complete .env file template."
            ) from e
        raise
