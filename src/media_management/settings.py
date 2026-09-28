from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DISPLAY_TZ = "Europe/Madrid"
SENTINEL_NAME = ".media-root"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MM_", env_file=None, env_ignore_empty=True)

    roots_file: Path
    media_base: Path
    data_dir: Path

    # Una clave por proceso y cada uno recibe solo la suya: el público firma tickets,
    # el panel firma tokens CSRF. Comprometer el público no da nada contra el panel.
    ticket_key: str = ""
    csrf_key: str = ""

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
    # Quién comparte, tal como lo verá quien abre el enlace ("Ana te ha compartido…").
    share_sender: str = ""
    panel_url: str = "https://panel.example.org"

    ntfy_url: str = ""
    ntfy_topic: str = ""
    ntfy_token: str = ""

    # Solo el worker escribe aquí; nginx lo lee. Sin él, no se generan zips.
    zips_dir: Path | None = None
    zip_min_free_gb: int = 20
    zip_max_gb: int = 0
    zip_retry_s: int = 300
    zip_max_attempts: int = 5

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


def require_key(value: str, name: str) -> str:
    if len(value) < 32:
        raise ValueError(f"{name} tiene que tener al menos 32 caracteres aleatorios")
    return value


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
