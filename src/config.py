
"""Centralized application configuration."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load variables from the local .env file when present.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "25"))
    max_rows: int = int(os.getenv("MAX_ROWS", "500000"))
    query_timeout_seconds: int = int(
        os.getenv("QUERY_TIMEOUT_SECONDS", "15")
    )

    def validate(self) -> None:
        """Validate configuration values before using them."""

        if self.max_upload_mb < 1:
            raise ValueError("MAX_UPLOAD_MB must be at least 1.")

        if self.max_rows < 1:
            raise ValueError("MAX_ROWS must be at least 1.")

        if self.query_timeout_seconds < 1:
            raise ValueError(
                "QUERY_TIMEOUT_SECONDS must be at least 1."
            )

        if not self.openai_model.strip():
            raise ValueError("OPENAI_MODEL cannot be empty.")


settings = Settings()
settings.validate()
