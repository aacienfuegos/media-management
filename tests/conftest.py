import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_management.api.app import create_app as create_api
from media_management.db import now_iso
from media_management.panel.app import create_app as create_panel
from media_management.public.app import create_app as create_public
from media_management.settings import SENTINEL_NAME, Settings

TRAEFIK = "192.0.2.10"
NGINX = "192.0.2.20"
USER = "andres-test"
SECRET = "k" * 48

ROOTS_TOML = """
[[roots]]
name = "buceo"
path = "{base}/buceo"
shareable = true
deletable = true
renamable = false
synced = true
requires_second_copy = true
indexed_by_jellyfin = true
jellyfin_path = "/library/buceo"
in_manifest = true
metadata_profile = "dji"

[[roots]]
name = "originales-120fps"
path = "{base}/originales-120fps"
shareable = true
deletable = true
renamable = false
synced = true
requires_second_copy = true
metadata_profile = "dji"
thumbnail_from = "buceo"

[[roots]]
name = "send"
path = "{base}/send"
shareable = true
deletable = true
renamable = true
metadata_profile = "generic"
"""


@dataclass
class Env:
    base: Path
    data: Path
    settings: Settings

    def root(self, name: str) -> Path:
        return self.base / name

    def write_ids(self, items: dict[str, str], generated_at: str | None = None) -> None:
        assert self.settings.jellyfin_ids_file is not None
        self.settings.jellyfin_ids_file.write_text(
            json.dumps({"generated_at": generated_at or now_iso(), "items": items}))


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    base = tmp_path / "media"
    for name in ("buceo", "originales-120fps", "send"):
        (base / name).mkdir(parents=True, exist_ok=True)
    (base / SENTINEL_NAME).write_text("")
    roots_file = tmp_path / "roots.toml"
    roots_file.write_text(ROOTS_TOML.format(base=base))
    (tmp_path / "manifiesto").mkdir(exist_ok=True)
    values: dict[str, object] = {
        "roots_file": roots_file, "media_base": base, "data_dir": tmp_path / "data",
        "secret_key": SECRET, "jellyfin_url": "",
        "jellyfin_ids_file": tmp_path / "jellyfin-ids.json",
        "manifest_path": tmp_path / "manifiesto" / "buceo.candidate.json",
        "trusted_proxies": [f"{TRAEFIK}/32"], "trusted_nginx": [f"{NGINX}/32"],
        "panel_allowed_users": [USER], "public_url": "https://share.example.org",
        "panel_url": "https://panel.example.org",
        "auth_delay_step_s": 0.0, "auth_delay_max_s": 0.0,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    settings = make_settings(tmp_path)
    return Env(settings.media_base, settings.data_dir, settings)


PANEL_HEADERS = {"X-authentik-username": USER, "Host": "testserver"}


@pytest.fixture
def panel(env: Env) -> Iterator[TestClient]:
    with TestClient(create_panel(env.settings), client=(TRAEFIK, 40000),
                    headers=PANEL_HEADERS) as c:
        yield c


@pytest.fixture
def api(env: Env) -> Iterator[TestClient]:
    with TestClient(create_api(env.settings), client=(TRAEFIK, 40001)) as c:
        yield c


@pytest.fixture
def public(env: Env, panel: TestClient) -> Iterator[TestClient]:
    with TestClient(create_public(env.settings), client=(NGINX, 40002),
                    headers={"X-Real-IP": "198.51.100.7"}) as c:
        yield c
