from fastapi.testclient import TestClient

from media_management.security import csrf_token
from tests.conftest import SECRET, USER, Env


def test_panel_healthz_reports_what_is_wrong(env: Env, panel: TestClient) -> None:
    r = panel.get("/healthz")
    assert r.status_code == 503
    body = r.json()
    assert body["media"]["ok"] is True
    assert body["jellyfin_ids_export"]["ok"] is False
    assert body["jellyfin"]["ok"] is False
    env.settings.sentinel.unlink()
    body = panel.get("/healthz").json()
    assert body["media"]["ok"] is False and "centinela" in body["media"]["problem"]


def test_public_healthz_says_only_yes_or_no(env: Env, public: TestClient) -> None:
    r = public.get("/healthz")
    assert (r.status_code, r.text) == (200, "ok")
    env.settings.sentinel.unlink()
    r = public.get("/healthz")
    assert (r.status_code, r.text) == (503, "unavailable")


def test_home_shows_unmounted_banner(env: Env, panel: TestClient) -> None:
    env.settings.sentinel.unlink()
    assert "La biblioteca no está disponible" in panel.get("/").text


def test_state_changes_need_csrf_token(panel: TestClient) -> None:
    assert panel.post("/scan", data={}).status_code == 403
    assert panel.post("/scan", data={"csrf": "x"}).status_code == 403
    token = csrf_token(SECRET, USER)
    cross = panel.post("/scan", data={"csrf": token}, headers={"Sec-Fetch-Site": "cross-site"},
                       follow_redirects=False)
    assert cross.status_code == 403
    other_origin = panel.post("/scan", data={"csrf": token}, headers={"Origin": "https://evil.example"},
                              follow_redirects=False)
    assert other_origin.status_code == 403
    ok = panel.post("/scan", data={"csrf": token}, headers={"Sec-Fetch-Site": "same-origin"},
                    follow_redirects=False)
    assert ok.status_code == 303


def test_security_headers_everywhere(panel: TestClient, public: TestClient, api: TestClient) -> None:
    for r in (panel.get("/"), public.get("/"), api.get("/healthz")):
        assert "default-src 'none'" in r.headers["content-security-policy"]
        assert "unsafe-inline" not in r.headers["content-security-policy"]
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-frame-options"] == "DENY"
    page = public.get("/")
    assert page.headers["cache-control"] == "no-store"
    assert '<meta name="robots" content="noindex' in page.text
