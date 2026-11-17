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
    # When behind a reverse proxy path, e.g. "/adm/house/monitor"
    public_base_path: str = ""
    # OIDC SSO (NesterAdmin). Client secret only via .env — never commit.
    oidc_issuer: str = ""
    oidc_client_id: str = "icsee"
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""
    oidc_scopes: str = "openid email profile"
    sso_required: bool = False
    disk_reserve_bytes: int = 2 * 1024**3
    segment_seconds: int = 300
    quota_gb: float = Field(default=50.0)
    # WireGuard multi-site (VPS)
    wg_server_public_key: str = ""
    wg_server_endpoint: str = ""  # host:port e.g. 191.252.208.229:51820
    wg_server_tunnel_ip: str = "10.10.10.1"
    wg_tunnel_network: str = "10.10.10.0/24"
    wg_vpn_base: str = "10.20.0.0/16"
    wg_peers_dir: str = "/data/wireguard/peers"
    wg_apply_cmd: str = ""  # optional host script path


@lru_cache
def get_settings() -> Settings:
    return Settings()
