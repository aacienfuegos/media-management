from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DISPLAY_TZ = "Europe/Madrid"
SENTINEL_NAME = ".media-root"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MM_", env_file=None)

    roots_file: Path
    media_base: Path
    data_dir: Path

    secret_key: str = Field(min_length=32)

    jellyfin_url: str = ""
    jellyfin_ids_file: Path | None = None
    jellyfin_ids_max_age_s: int = 3600
    jellyfin_timeout_s: float = 5.0

    manifest_path: Path | None = None
    manifest_compare_with: Path | None = None
    scan_interval_s: int = 900

    trusted_proxies: list[str] = []
    trusted_nginx: list[str] = []
    panel_allowed_users: list[str] = []

    public_url: str = "https://share.example.org"
    panel_url: str = "https://panel.example.org"

    ntfy_url: str = ""
    ntfy_topic: str = ""
    ntfy_token: str = ""

    ticket_ttl_s: int = 4 * 3600
    link_default_days: int = 7
    link_max_days: int = 90
    request_ttl_days: int = 7
    max_pending_requests_per_link: int = 20
    max_requests_per_ip_per_hour: int = 5
    auth_window_s: int = 900
    auth_max_failures_per_ip: int = 10
    auth_delay_step_s: float = 0.5
    auth_delay_max_s: float = 10.0

    @property
    def main_db(self) -> Path:
        return self.data_dir / "main" / "main.db"

    @property
    def public_db(self) -> Path:
        return self.data_dir / "public" / "public.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def trash_dir(self) -> Path:
        return self.media_base / ".trash"

    @property
    def sentinel(self) -> Path:
        return self.media_base / SENTINEL_NAME


def networks(cidrs: list[str]) -> list[IPv4Network | IPv6Network]:
    return [ip_network(c, strict=False) for c in cidrs]


def ip_in(host: str | None, cidrs: list[str]) -> bool:
    if not host:
        return False
    try:
        addr = ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in networks(cidrs))


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
