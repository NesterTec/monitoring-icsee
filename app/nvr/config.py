from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    admin_email: str = "ivanrank@yahoo.com.br"
    admin_password: str = "change-me"
    secret_key: str = "dev-secret-change-me"
    camera_secret_key: str = "dev-camera-key-32bytes-minimum!!"
    database_url: str = "sqlite:////data/nvr.db"
    default_storage_path: str = "/recordings"
    go2rtc_config_path: str = "/config/go2rtc.yaml"
    go2rtc_api: str = "http://127.0.0.1:1984"
    go2rtc_public_url: str = "http://127.0.0.1:1984"
    disk_reserve_bytes: int = 2 * 1024**3
    segment_seconds: int = 300
    quota_gb: float = Field(default=50.0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
