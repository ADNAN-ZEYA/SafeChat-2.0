# backend/core/config.py
"""Application configuration loaded from environment variables.

Settings are loaded once and cached via lru_cache so the entire app shares a
single Settings instance. Required values raise at startup if missing — fail
fast rather than discover a misconfiguration mid-request.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
_ENV_FILE = _BACKEND_DIR / ".env"

Environment = Literal["development", "staging", "production"]

_DEFAULT_CORS_ORIGINS: list[str] = [
    "http://localhost:8081",
    "http://localhost:19006",
    "http://localhost:3000",
]


class Settings(BaseSettings):
    """Validated application settings sourced from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Required ---------------------------------------------------------
    firebase_admin_key_path: Path = Field(
        ..., description="Filesystem path to the Firebase Admin SDK JSON key."
    )
    gcp_project_id: str = Field(
        ..., min_length=1, description="GCP / Firebase project ID."
    )

    # ---- Optional now, required in Phase 2 --------------------------------
    # OpenAI Moderation API key. Becomes required once the moderation cascade
    # is wired up in Phase 2.
    openai_api_key: str | None = Field(
        default=None, description="OpenAI Moderation API key (required in Phase 2)."
    )

    # ---- With defaults ----------------------------------------------------
    environment: Environment = Field(
        default="development", description="Runtime environment."
    )
    log_level: str = Field(default="INFO", description="Python logging level.")
    port: int = Field(default=8080, ge=1, le=65535)
    backend_cors_origins: str = ""
    firebase_storage_bucket: str | None = Field(
        default=None,
        description="Storage bucket. Defaults to {gcp_project_id}.firebasestorage.app.",
    )
    rate_limit_enabled: bool | None = Field(
        default=None,
        description=(
            "Force rate limiting on (true) or off (false). Unset: enabled in "
            "every environment except development, so local dev and the test "
            "suite are never throttled."
        ),
    )
    # ---- Moderation tuning (HC-05 / HC-06) --------------------------------
    openai_moderation_model: str = Field(
        default="omni-moderation-latest",
        description="OpenAI moderation model identifier.",
    )
    openai_timeout_seconds: float = Field(
        default=3.0, gt=0, description="OpenAI moderation request timeout."
    )
    tfidf_flag_threshold: float = Field(
        default=0.55,
        ge=0.0,
        le=1.0,
        description=(
            "Layer-2 TF-IDF toxicity probability at/above which text is "
            "flagged. Clean text scores ~0.35 and clearly toxic phrasing "
            "~0.6+ on the current seed model."
        ),
    )

    @field_validator("firebase_admin_key_path")
    @classmethod
    def _resolve_and_check_key_path(cls, value: Path) -> Path:
        # Absolute path: use as-is. This is the expected form in containers
        # (where the credentials folder is mounted at a known mount point).
        if value.is_absolute():
            if not value.is_file():
                raise ValueError(
                    f"FIREBASE_ADMIN_KEY_PATH does not point to a file: {value}"
                )
            return value

        # Relative path: try CWD, then backend/, then repo root.
        # Lets the same .env work from local dev (any CWD), pytest, or
        # explicit `uvicorn main:app` runs from inside backend/.
        candidates = [
            (Path.cwd() / value).resolve(),
            (_BACKEND_DIR / value).resolve(),
            (_REPO_ROOT / value).resolve(),
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate

        attempted = "\n  ".join(str(c) for c in candidates)
        raise ValueError(
            "FIREBASE_ADMIN_KEY_PATH does not point to a file. Tried:\n  "
            + attempted
        )

    @property
    def cors_origins(self) -> list[str]:
        if not self.backend_cors_origins.strip():
            return list(_DEFAULT_CORS_ORIGINS)
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]

    @property
    def storage_bucket_name(self) -> str:
        return self.firebase_storage_bucket or f"{self.gcp_project_id}.firebasestorage.app"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def rate_limit_effective(self) -> bool:
        """Rate limiting on/off after applying the environment default."""
        if self.rate_limit_enabled is not None:
            return self.rate_limit_enabled
        return self.environment != "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached singleton Settings instance."""
    return Settings()  # type: ignore[call-arg]
