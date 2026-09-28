import asyncio
import os
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from media_management.public.app import MainReader, create_app
from media_management.security import verify_ticket
from tests.conftest import NGINX, SECRET, Env
from tests.helpers import CSRF, create_link, link_file_ids, send_files, sql, ticket_url

FILES = {"a.txt": b"A" * 1000, "b.txt": b"B" * 1000, "carpeta/informe ñ «final».pdf": b"%PDF" + b"x" * 500}


def count(env: Env, table: str) -> int:
    return int(sql(env, f"SELECT COUNT(*) FROM {table}", db="public")[0][0])


def test_page_and_share_never_consume(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, _ = create_link(panel, list(ids.values()))
    for _ in range(5):
        assert public.get("/").status_code == 200
        assert public.post("/api/share", json={"token": token}).status_code == 200
    assert count(env, "tickets") == 0 and count(env, "downloads") == 0
    ticket_url(public, token, link_file_ids(public, token)[0])
    assert count(env, "tickets") == 1


def test_share_lists_files_and_download_goes_through_nginx(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, _ = create_link(panel, [ids["carpeta/informe ñ «final».pdf"], ids["a.txt"]], title="Para Juan")
    data = public.post("/api/share", json={"token": token}).json()
    assert data["title"] == "Para Juan"
    assert [f["name"] for f in data["files"]] == ["informe ñ «final».pdf", "a.txt"]
    assert set(data["files"][0]) == {"id", "name", "size", "kind", "thumb"}
    r = public.get(ticket_url(public, token, data["files"][0]["id"]))
    assert r.status_code == 200 and r.content == b""
    assert unquote(r.headers["x-accel-redirect"]) == "/_protected/send/carpeta/informe ñ «final».pdf"
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;") and "filename*=UTF-8''informe%20%C3%B1%20%C2%ABfinal%C2%BB.pdf" in cd


@pytest.mark.parametrize("state", ["missing", "expired", "revoked"])
def test_uniform_answer_for_unusable_links(env: Env, panel: TestClient, public: TestClient, state: str) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, [ids["a.txt"]])
    reference = public.post("/api/share", json={"token": "x" * 43})
    if state == "expired":
        sql(env, "UPDATE links SET expires_at = '2000-01-01T00:00:00Z' WHERE id = ?", (link_id,))
    elif state == "revoked":
        sql(env, "UPDATE links SET revoked_at = '2000-01-01T00:00:00Z' WHERE id = ?", (link_id,))
    else:
        token = "y" * 43
    r = public.post("/api/share", json={"token": token})
    assert (r.status_code, r.json()) == (reference.status_code, reference.json()) == (404, {"error": "not_found"})
    t = public.post("/api/ticket", json={"token": token, "file": 1})
    assert (t.status_code, t.json()) == (404, {"error": "not_found"})


def test_revoking_cuts_pending_resumes(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, [ids["a.txt"]])
    url = ticket_url(public, token, link_file_ids(public, token)[0])
    assert public.get(url, headers={"Range": "bytes=0-99"}).status_code == 200
    panel.post(f"/links/{link_id}/revoke", data={"csrf": CSRF})
    assert public.get(url, headers={"Range": "bytes=100-"}).status_code == 404


def test_range_requests_do_not_issue_tickets(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, _ = create_link(panel, [ids["a.txt"]])
    url = ticket_url(public, token, link_file_ids(public, token)[0])
    for start in (0, 100, 500, 900):
        assert public.get(url, headers={"Range": f"bytes={start}-"}).status_code == 200
    assert count(env, "tickets") == 1 and count(env, "downloads") == 4


def test_ticket_is_bound_to_file_link_and_time(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token_a, _ = create_link(panel, [ids["a.txt"], ids["b.txt"]])
    token_b, _ = create_link(panel, [ids["b.txt"]])
    fa, fb = link_file_ids(public, token_a)
    url = ticket_url(public, token_a, fa)
    raw = url.rsplit("/", 1)[1]
    t = verify_ticket(SECRET, raw)
    assert t is not None and t.link_file_id == fa
    assert public.get(url).headers["x-accel-redirect"].endswith("/a.txt")
    body, sig = raw.split(".")
    import base64
    import json
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    for tampered in ([payload[0], payload[1], fb, None, payload[4]],
                     [payload[0], payload[1] + 1, payload[2], None, payload[4]],
                     [payload[0], payload[1], payload[2], None, payload[4] + 10**6]):
        forged = base64.urlsafe_b64encode(json.dumps(tampered, separators=(",", ":")).encode()).decode().rstrip("=")
        assert public.get(f"/download/{forged}.{sig}").status_code == 404
    other_link_file = link_file_ids(public, token_b)[0]
    r = public.post("/api/ticket", json={"token": token_a, "file": other_link_file})
    assert r.status_code == 404
    assert verify_ticket(SECRET, raw, now=t.expires + 1) is None
    sql(env, "UPDATE links SET expires_at = '2000-01-01T00:00:00Z' WHERE token_hash IS NOT NULL")
    assert public.get(url).status_code == 404


def test_ticket_lifetime_capped_by_link_expiry(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, [ids["a.txt"]], days=1)
    url = ticket_url(public, token, link_file_ids(public, token)[0])
    t = verify_ticket(SECRET, url.rsplit("/", 1)[1])
    assert t is not None
    import time
    assert 3 * 3600 < t.expires - time.time() <= 4 * 3600 + 5


@pytest.mark.parametrize("path", [
    "/download/..%2F..%2Fetc%2Fpasswd", "/download/%2e%2e%2f%2e%2e%2froots.toml", "/download/%2e%2e",
    "/_protected/send/a.txt", "/static/..%2F..%2Fsettings.py", "/static/../app.py",
])
def test_traversal_attempts_in_public_urls(env: Env, panel: TestClient, public: TestClient, path: str) -> None:
    send_files(env, FILES)
    r = public.get(path)
    assert r.status_code == 404
    assert "x-accel-redirect" not in r.headers


def test_file_moved_away_or_replaced_by_symlink_is_not_served(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, _ = create_link(panel, [ids["a.txt"], ids["b.txt"]])
    fa, fb = link_file_ids(public, token)
    url_a, url_b = ticket_url(public, token, fa), ticket_url(public, token, fb)
    secret = env.base.parent / "secreto.txt"
    secret.write_text("no")
    (env.root("send") / "a.txt").unlink()
    os.symlink(secret, env.root("send") / "a.txt")
    assert public.get(url_a).status_code == 404
    (env.root("send") / "b.txt").unlink()
    assert public.get(url_b).status_code == 404
    from tests.test_manifest import cycle
    cycle(env.settings)
    assert [f["name"] for f in public.post("/api/share", json={"token": token}).json()["files"]] == []


def test_link_token_is_never_accepted_in_the_url(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, _ = create_link(panel, [ids["a.txt"]])
    for method, path in (("GET", f"/?token={token}"), ("GET", f"/{token}"), ("GET", f"/s/{token}"),
                         ("POST", f"/api/share?token={token}"), ("GET", f"/api/share?token={token}"),
                         ("GET", f"/download/{token}"), ("POST", f"/api/ticket?token={token}&file=1")):
        r = public.request(method, path)
        assert "a.txt" not in r.text, (method, path)
        assert r.status_code in (200, 400, 404, 405)
        if r.status_code == 200:
            assert r.text == public.get("/").text
    assert public.post("/api/share", json={"token": token}).status_code == 200


def test_validation_errors_do_not_echo_input(public: TestClient) -> None:
    r = public.post("/api/share", json={"token": "secreto-que-no-vale!!", "password": "p" * 5000})
    assert r.status_code == 400 and r.json() == {"error": "bad_request"}
    assert "secreto" not in r.text


def test_password_mode_and_bruteforce_protection(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    r = panel.post("/links", data={"csrf": CSRF,
                                   "file_id": [ids["a.txt"]], "mode": "password", "password": "corta", "days": 7})
    assert r.status_code == 400 and "al menos 8" in r.text
    token, link_id = create_link(panel, [ids["a.txt"]], mode="password", password="buceo-en-grupo")
    assert public.post("/api/share", json={"token": token}).json() == {"need": "password"}
    assert public.post("/api/ticket", json={"token": token, "file": 1}).status_code == 401
    attacker = TestClient(public.app, client=(NGINX, 1), headers={"X-Real-IP": "203.0.113.66"})
    for _ in range(10):
        r = attacker.post("/api/share", json={"token": token, "password": "adivina"})
        assert r.status_code == 401 and r.json() == {"need": "password", "error": "bad_credentials"}
    blocked = attacker.post("/api/share", json={"token": token, "password": "buceo-en-grupo"})
    assert blocked.status_code == 429
    ok = public.post("/api/share", json={"token": token, "password": "buceo-en-grupo"})
    assert ok.status_code == 200
    fid = ok.json()["files"][0]["id"]
    assert public.get(ticket_url(public, token, fid, password="buceo-en-grupo")).status_code == 200
    assert "Credenciales fallidas</th><td>10" in panel.get(f"/links/{link_id}").text


def test_link_delay_grows_with_failures(env: Env, panel: TestClient) -> None:
    import time
    settings = env.settings.model_copy(update={"auth_delay_step_s": 0.05, "auth_delay_max_s": 0.2})
    ids = send_files(env, FILES)
    token, _ = create_link(panel, [ids["a.txt"]], mode="password", password="buceo-en-grupo")
    with TestClient(create_app(settings), client=(NGINX, 1)) as c:
        timings = []
        for i in range(6):
            c.headers["X-Real-IP"] = f"198.51.100.{i + 1}"
            t0 = time.monotonic()
            c.post("/api/share", json={"token": token, "password": "mal"})
            timings.append(time.monotonic() - t0)
        assert timings[-1] >= 0.2 > timings[0]
        c.headers["X-Real-IP"] = "198.51.100.99"
        assert c.post("/api/share", json={"token": token, "password": "buceo-en-grupo"}).status_code == 200


def test_real_ip_is_trusted_only_from_nginx(env: Env, panel: TestClient) -> None:
    ids = send_files(env, FILES)
    token, _ = create_link(panel, [ids["a.txt"]])
    app = create_app(env.settings)
    with TestClient(app, client=("192.0.2.200", 1), headers={"X-Real-IP": "203.0.113.1"}) as direct:
        ticket_url(direct, token, link_file_ids(direct, token)[0])
    with TestClient(app, client=(NGINX, 1), headers={"X-Real-IP": "203.0.113.2",
                                                     "X-Forwarded-For": "203.0.113.3"}) as via:
        ticket_url(via, token, link_file_ids(via, token)[0])
    with TestClient(app, client=(NGINX, 1), headers={"X-Real-IP": "no-es-una-ip"}) as junk:
        ticket_url(junk, token, link_file_ids(junk, token)[0])
    ips = [r[0] for r in sql(env, "SELECT ip FROM tickets ORDER BY issued_at, rowid", db="public")]
    assert ips == ["192.0.2.200", "203.0.113.2", NGINX]


def test_no_cookies_and_strict_headers(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, _ = create_link(panel, [ids["a.txt"]], mode="password", password="buceo-en-grupo")
    responses = [public.get("/"), public.get("/static/share.js"), public.get("/healthz"),
                 public.post("/api/share", json={"token": token}),
                 public.post("/api/share", json={"token": token, "password": "mal"}),
                 public.post("/api/share", json={"token": token, "password": "buceo-en-grupo"})]
    fid = responses[-1].json()["files"][0]["id"]
    url = ticket_url(public, token, fid, password="buceo-en-grupo")
    responses += [public.get(url), public.get("/download/nada"), public.get("/no-existe")]
    for r in responses:
        assert "set-cookie" not in r.headers
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "unsafe-inline" not in r.headers["content-security-policy"]


def test_public_cannot_write_main_db(env: Env, panel: TestClient) -> None:
    async def go() -> None:
        reader = MainReader(env.settings.main_db)
        conn = await reader.get()
        assert conn is not None
        async with conn.execute("SELECT COUNT(*) FROM links") as cur:
            assert (await cur.fetchone()) is not None
        for stmt in ("INSERT INTO links (token_hash, title, mode, created_at, created_by, expires_at) "
                     "VALUES ('h', 't', 'open', 'a', 'b', '2999-01-01T00:00:00Z')",
                     "UPDATE grants SET revoked_at = NULL", "DELETE FROM audit", "CREATE TABLE x (a)"):
            with pytest.raises(Exception, match="readonly"):
                await conn.execute(stmt)
        await reader.close()
    asyncio.run(go())


def test_link_created_once_and_panel_views(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, [ids["a.txt"], ids["b.txt"]], title="Viaje")
    detail = panel.get(f"/links/{link_id}").text
    assert token not in detail and token not in panel.get("/links").text
    assert token not in panel.get("/audit").text
    r = panel.post("/links", data={"csrf": CSRF, "file_id": [ids["a.txt"]], "days": 91})
    assert r.status_code == 400
    panel.post(f"/links/{link_id}/renew", data={"csrf": CSRF, "days": 90})
    assert panel.post(f"/links/{link_id}/renew", data={"csrf": CSRF, "days": 91}).status_code == 400
    ticket_url(public, token, link_file_ids(public, token)[0])
    detail = panel.get(f"/links/{link_id}").text
    assert "198.51.100.7" in detail and "a.txt" in detail
    assert panel.get("/links/99999").status_code == 404
