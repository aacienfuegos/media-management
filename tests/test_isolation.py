import re
import subprocess
import sys
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from media_management.api.app import create_app as create_api
from media_management.panel.app import create_app as create_panel
from media_management.public.app import create_app as create_public
from tests.conftest import TRAEFIK, USER, Env

SHARED = {"/healthz", "/"}


def routes(app: FastAPI) -> set[tuple[str, str]]:
    out = set()
    for method, path in app_routes(app):
        if path not in SHARED:
            out.add((method, re.sub(r"\{[^}]+\}", "1", path)))
    return out


def app_routes(app: FastAPI) -> set[tuple[str, str]]:
    return {(m, r["path"]) for r in _walk(app.routes, "") for m in r["methods"]}


def _walk(routes: list[Any], prefix: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in routes:
        if isinstance(r, APIRoute):
            out.append({"path": prefix + r.path, "methods": r.methods})
        elif hasattr(r, "original_router"):
            ctx = getattr(r, "include_context", None)
            sub_prefix = getattr(ctx, "prefix", "") if ctx is not None else ""
            out.extend(_walk(r.original_router.routes, prefix + sub_prefix))
    return out


@pytest.mark.parametrize("owner,other", [
    ("panel", "public"), ("panel", "api"), ("api", "panel"), ("api", "public")])
def test_each_process_404s_the_routes_of_the_others(env: Env, owner: str, other: str) -> None:
    apps = {"panel": create_panel(env.settings), "public": create_public(env.settings),
            "api": create_api(env.settings)}
    foreign = routes(apps[owner])
    assert foreign, f"{owner} debería tener rutas propias"
    with TestClient(apps[other], client=(TRAEFIK, 1)) as client:
        for method, path in foreign:
            r = client.request(method, path, headers={"X-authentik-username": USER})
            assert r.status_code in (404, 405), (other, method, path, r.status_code)
            if r.status_code == 405:
                assert (method, path) not in routes(apps[other])


def test_own_routes_are_reachable(panel: TestClient, api: TestClient, public: TestClient) -> None:
    assert panel.get("/").status_code == 200
    assert api.get("/api/v1/roots").status_code == 401
    assert public.get("/").status_code == 200


@pytest.mark.parametrize("module,forbidden", [
    ("media_management.api.app", ("media_management.panel", "media_management.public")),
    ("media_management.public.app", ("media_management.panel", "media_management.api")),
    ("media_management.panel.app", ("media_management.public", "media_management.api")),
])
def test_process_code_is_not_loaded_in_the_others(module: str, forbidden: tuple[str, ...]) -> None:
    code = (f"import sys, {module}; "
            f"bad = [m for m in sys.modules if m.startswith({forbidden!r})]; "
            "print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_api_ignores_forged_authentik_header(api: TestClient) -> None:
    for path in ("/api/v1/roots", "/api/v1/manifest", "/api/v1/roots/buceo/files"):
        r = api.get(path, headers={"X-authentik-username": USER, "X-authentik-groups": "admins"})
        assert r.status_code == 401


def test_panel_requires_traefik_origin(env: Env) -> None:
    app = create_panel(env.settings)
    with TestClient(app, client=("192.0.2.99", 1)) as direct:
        assert direct.get("/", headers={"X-authentik-username": USER}).status_code == 403
    with TestClient(app, client=(TRAEFIK, 1)) as via_traefik:
        assert via_traefik.get("/").status_code == 403
        assert via_traefik.get("/", headers={"X-authentik-username": "otra"}).status_code == 403
        assert via_traefik.get("/", headers={"X-authentik-username": USER}).status_code == 200


def test_panel_denies_everyone_when_no_user_is_configured(env: Env) -> None:
    settings = env.settings.model_copy(update={"panel_allowed_users": []})
    with TestClient(create_panel(settings), client=(TRAEFIK, 1)) as c:
        assert c.get("/", headers={"X-authentik-username": USER}).status_code == 403
