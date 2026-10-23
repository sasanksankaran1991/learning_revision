from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "app.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = f"sqlite:///{DEFAULT_DB}"
    jwt_secret: str = "dev-only-change-me"
    fernet_keys: str = ""
    cors_origins: str = "http://localhost:5173"
    public_base_url: str = "http://localhost:8000"
    usd_inr_rate: float = 84.0
    cookie_secure: bool = False
    access_token_minutes: int = 15
    refresh_token_days: int = 180
    login_max_failures: int = 10
    login_lock_minutes: int = 15
    login_ip_limit: int = 5
    login_ip_window_seconds: int = 60
    admin_username: str = ""
    admin_password: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def fernet_key_list(self) -> list[str]:
        return [item.strip() for item in self.fernet_keys.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
