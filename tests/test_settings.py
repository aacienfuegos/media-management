import json
import shutil
import subprocess
from pathlib import Path

import pytest

from media_management.panel.app import create_app as create_panel
from media_management.public.app import create_app as create_public
from media_management.settings import Settings
from tests.conftest import make_settings

REPO = Path(__file__).resolve().parent.parent


def test_empty_env_vars_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {"MM_ROOTS_FILE": "/r.toml", "MM_MEDIA_BASE": "/m", "MM_DATA_DIR": "/d",
                       "MM_MANIFEST_COMPARE_WITH": "",
                       "MM_JELLYFIN_IDS_FILE": "", "MM_MANIFEST_PATH": "",
                       "MM_PANEL_ALLOWED_USERS": '["andres"]'}.items():
        monkeypatch.setenv(key, value)
    s = Settings()  # type: ignore[call-arg]
    assert s.manifest_compare_with is None and s.jellyfin_ids_file is None and s.manifest_path is None
    assert s.panel_allowed_users == ["andres"]


def test_public_refuses_to_start_without_ticket_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="MM_TICKET_KEY"):
        create_public(make_settings(tmp_path, ticket_key=""))
    with pytest.raises(ValueError, match="MM_TICKET_KEY"):
        create_public(make_settings(tmp_path, ticket_key="corta"))


def test_panel_refuses_to_start_without_csrf_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="MM_CSRF_KEY"):
        create_panel(make_settings(tmp_path, csrf_key=""))


@pytest.mark.skipif(shutil.which("docker") is None, reason="sin docker")
def test_compose_gives_each_secret_only_to_its_service(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("APP_UID=1000\nAPP_GID=1000\nMEDIA_DIR=/m\nDATA_DIR=/d\nROOTS_FILE=/r.toml\n"
                   "MANIFEST_DIR=/man\nIDS_EXPORT_DIR=/ids\nTRAEFIK_IP=192.0.2.1\n"
                   "MM_TICKET_KEY=ticket-secreta\nMM_CSRF_KEY=csrf-secreta\nMM_NTFY_TOKEN=ntfy-secreto\n")
    out = subprocess.run(
        ["docker", "compose", "-f", str(REPO / "compose.yaml"), "--env-file", str(env),
         "config", "--format", "json"], capture_output=True, text=True, check=True).stdout
    services = json.loads(out)["services"]
    holders = {secret: sorted(name for name, svc in services.items()
                              if secret in json.dumps(svc.get("environment", {})))
               for secret in ("ticket-secreta", "csrf-secreta", "ntfy-secreto")}
    assert holders == {"ticket-secreta": ["public"], "csrf-secreta": ["panel"], "ntfy-secreto": ["public"]}
