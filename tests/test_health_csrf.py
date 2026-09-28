from fastapi.testclient import TestClient

from media_management.security import csrf_token
from tests.conftest import CSRF_KEY, USER, Env
from tests.helpers import CSRF, create_link, send_files


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
    token = csrf_token(CSRF_KEY, USER)
    cross = panel.post("/scan", data={"csrf": token}, headers={"Sec-Fetch-Site": "cross-site"},
                       follow_redirects=False)
    assert cross.status_code == 403
    other_origin = panel.post("/scan", data={"csrf": token}, headers={"Origin": "https://evil.example"},
                              follow_redirects=False)
    assert other_origin.status_code == 403
    null_origin = panel.post("/scan", data={"csrf": token}, headers={"Origin": "null"}, follow_redirects=False)
    assert null_origin.status_code == 403
    ok = panel.post("/scan", data={"csrf": token}, headers={"Sec-Fetch-Site": "same-origin"},
                    follow_redirects=False)
    assert ok.status_code == 303
    browser = panel.post("/scan", data={"csrf": token}, follow_redirects=False,
                         headers={"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"})
    assert browser.status_code == 303


def test_security_headers_everywhere(panel: TestClient, public: TestClient, api: TestClient) -> None:
    for r in (panel.get("/"), public.get("/"), api.get("/healthz")):
        assert "default-src 'none'" in r.headers["content-security-policy"]
        assert "unsafe-inline" not in r.headers["content-security-policy"]
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
    assert public.get("/").headers["referrer-policy"] == "no-referrer"
    assert panel.get("/").headers["referrer-policy"] == "same-origin"
    page = public.get("/")
    assert page.headers["cache-control"] == "no-store"
    assert '<meta name="robots" content="noindex' in page.text


def test_action_notice_comes_from_a_fixed_key(env: Env, panel: TestClient) -> None:
    ids = send_files(env, {"a.txt": b"A"})
    _, link_id = create_link(panel, list(ids.values()))
    r = panel.post(f"/links/{link_id}/revoke", data={"csrf": CSRF}, follow_redirects=False)
    assert r.headers["location"] == f"/links/{link_id}?hecho=revoked"
    assert "Enlace revocado." in panel.get(r.headers["location"]).text
    page = panel.get(f"/links/{link_id}?hecho=%3Cscript%3Ealert(1)%3C/script%3E").text
    assert "alert(1)" not in page and 'role="status"' not in page


def test_audit_reads_in_spanish(env: Env, panel: TestClient) -> None:
    ids = send_files(env, {"a.txt": b"A"})
    create_link(panel, list(ids.values()))
    page = panel.get("/audit").text
    assert '<optgroup label="Enlaces">' in page and ">Enlace creado<" in page
    assert '<span class="muted">modo</span> abierto' in page and "link_id" not in page


def test_not_found_uses_panel_page_only_for_allowed_users(panel: TestClient) -> None:
    missing = panel.get("/links/999999")
    assert missing.status_code == 404 and "Esta página no existe" in missing.text
    stranger = panel.get("/links/999999", headers={"X-Authentik-Username": "otra"})
    assert stranger.status_code == 403 and "Biblioteca" not in stranger.text
