"""Configuration loaded from environment variables."""
import os


class Settings:
    # LiteLLM endpoint
    LITELLM_BASE_URL: str = os.getenv("LITELLM_BASE_URL", "http://litellm:4000")
    LITELLM_MASTER_KEY: str = os.getenv("LITELLM_MASTER_KEY", "")

    # SQLite path (mounted as Docker volume)
    DB_PATH: str = os.getenv("DB_PATH", "/data/usage.db")

    # Fetch loop
    FETCH_INTERVAL_SECONDS: int = int(os.getenv("FETCH_INTERVAL_SECONDS", "60"))
    FETCH_PAGE_SIZE: int = int(os.getenv("FETCH_PAGE_SIZE", "1000"))

    # Server
    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT: int = int(os.getenv("APP_PORT", "8000"))


settings = Settings()
