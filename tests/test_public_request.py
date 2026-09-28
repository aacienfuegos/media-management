import re
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from media_management.public.app import create_app as create_public
from tests.conftest import NGINX, Env
from tests.fake_jellyfin import JPEG, FakeJellyfin, jellyfin  # noqa: F401
from tests.helpers import CSRF, create_link, link_file_ids, send_files, sql, ticket_url
from tests.media_factory import make_video, needs_ffmpeg

FILES = {"a.txt": b"A" * 100, "b.txt": b"B" * 100}


class FakeNtfy:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, str], bytes]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                fake.posts.append((self.path, dict(self.headers), body))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def ntfy() -> Iterator[FakeNtfy]:
    fake = FakeNtfy()
    yield fake
    fake.server.shutdown()


@pytest.fixture
def pub(env: Env, panel: TestClient, ntfy: FakeNtfy) -> Iterator[TestClient]:
    settings = env.settings.model_copy(update={"ntfy_url": ntfy.url, "ntfy_topic": "solicitudes",
                                               "ntfy_token": "tk_prueba"})
    with TestClient(create_public(settings), client=(NGINX, 1), headers={"X-Real-IP": "198.51.100.7"}) as c:
        yield c


def ask(client: TestClient, token: str, name: str, note: str = "") -> str:
    r = client.post("/api/request", json={"token": token, "name": name, "note": note})
    assert r.status_code == 200, r.text
    code: str = r.json()["code"]
    assert re.fullmatch(r"[A-HJKMNP-Z2-9]{5}-[A-HJKMNP-Z2-9]{5}", code)
    return code


def request_id(env: Env, name: str) -> int:
    return int(sql(env, "SELECT id FROM access_requests WHERE name = ?", (name,), db="public")[0][0])


def approve(panel: TestClient, env: Env, name: str, days: int = 7) -> None:
    r = panel.post(f"/requests/{request_id(env, name)}/approve", data={"csrf": CSRF, "days": days})
    assert r.status_code == 200, r.text


def test_request_flow_end_to_end(env: Env, panel: TestClient, pub: TestClient, ntfy: FakeNtfy) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, list(ids.values()), mode="request", title="Fotos del grupo")
    assert pub.post("/api/share", json={"token": token}).json() == {"need": "code"}
    code = ask(pub, token, "Juan Pérez", "Soy el del barco\nsegunda línea")
    assert code not in str(sql(env, "SELECT * FROM access_requests", db="public"))
    pending = pub.post("/api/share", json={"token": token, "code": code})
    assert (pending.status_code, pending.json()) == (202, {"status": "pending"})
    assert pub.post("/api/ticket", json={"token": token, "file": 1, "code": code}).status_code == 202
    path, headers, body = ntfy.posts[0]
    assert path == "/solicitudes" and headers["Authorization"] == "Bearer tk_prueba"
    assert headers["Markdown"] == "no" and headers["Click"].endswith(f"/links/{link_id}")
    assert "Juan Pérez" in body.decode() and "Fotos del grupo" in body.decode()
    assert "Juan" not in headers["Title"]
    approve(panel, env, "Juan Pérez")
    other_device = TestClient(pub.app, client=(NGINX, 2), headers={"X-Real-IP": "203.0.113.50",
                                                                  "User-Agent": "OtroMovil/1.0"})
    fid = link_file_ids(other_device, token, code=code.lower().replace("-", " "))[0]
    assert other_device.get(ticket_url(other_device, token, fid, code=code)).status_code == 200
    assert pub.get(ticket_url(pub, token, fid, code=code)).status_code == 200
    page = panel.get(f"/links/{link_id}").text
    assert "Juan Pérez" in page and "203.0.113.50" in page and "¿código reenviado?" in page


def test_revoking_one_person_keeps_the_others(env: Env, panel: TestClient, pub: TestClient) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, [ids["a.txt"]], mode="request")
    code_ana, code_luis = ask(pub, token, "Ana"), ask(pub, token, "Luis")
    approve(panel, env, "Ana")
    approve(panel, env, "Luis")
    fid = link_file_ids(pub, token, code=code_ana)[0]
    url_ana = ticket_url(pub, token, fid, code=code_ana)
    grant_ana = sql(env, "SELECT id FROM grants WHERE name = 'Ana'")[0][0]
    panel.post(f"/grants/{grant_ana}/revoke", data={"csrf": CSRF})
    bad = pub.post("/api/share", json={"token": token, "code": code_ana})
    assert (bad.status_code, bad.json()) == (401, {"need": "code", "error": "bad_credentials"})
    assert pub.get(url_ana, headers={"Range": "bytes=10-"}).status_code == 404
    assert pub.post("/api/share", json={"token": token, "code": code_luis}).status_code == 200


@pytest.mark.parametrize("case", ["wrong", "other_link", "rejected", "expired_grant", "stale_request"])
def test_uniform_answer_for_invalid_codes(env: Env, panel: TestClient, pub: TestClient, case: str) -> None:
    ids = send_files(env, FILES)
    token, _ = create_link(panel, [ids["a.txt"]], mode="request")
    other_token, _ = create_link(panel, [ids["b.txt"]], mode="request")
    code = ask(pub, token, "Eva")
    if case == "wrong":
        code = "ABCDE-FGHJK"
    elif case == "other_link":
        approve(panel, env, "Eva")
        token = other_token
    elif case == "rejected":
        panel.post(f"/requests/{request_id(env, 'Eva')}/reject", data={"csrf": CSRF})
    elif case == "expired_grant":
        approve(panel, env, "Eva")
        sql(env, "UPDATE grants SET expires_at = '2000-01-01T00:00:00Z'")
    else:
        sql(env, "UPDATE access_requests SET created_at = '2000-01-01T00:00:00Z'", db="public")
    r = pub.post("/api/share", json={"token": token, "code": code})
    assert (r.status_code, r.json()) == (401, {"need": "code", "error": "bad_credentials"})
    assert pub.post("/api/ticket", json={"token": token, "file": 1, "code": code}).status_code == 401


def test_request_abuse_limits(env: Env, panel: TestClient, pub: TestClient) -> None:
    ids = send_files(env, FILES)
    token, _ = create_link(panel, [ids["a.txt"]], mode="request")
    for i in range(5):
        ask(pub, token, f"Persona {i}")
    r = pub.post("/api/request", json={"token": token, "name": "Sexta"})
    assert (r.status_code, r.json()) == (429, {"error": "rate_limited"})
    settings = env.settings.model_copy(update={"max_pending_requests_per_link": 7})
    with TestClient(create_public(settings), client=(NGINX, 1)) as c:
        for i in range(2):
            c.headers["X-Real-IP"] = f"203.0.113.{i + 10}"
            ask(c, token, f"Otra {i}")
        c.headers["X-Real-IP"] = "203.0.113.99"
        r = c.post("/api/request", json={"token": token, "name": "Octava"})
        assert (r.status_code, r.json()) == (429, {"error": "queue_full"})
    open_token, _ = create_link(panel, [ids["a.txt"]])
    assert pub.post("/api/request", json={"token": open_token, "name": "X"}).status_code == 404
    assert pub.post("/api/request", json={"token": "z" * 43, "name": "X"}).status_code == 404
    too_long = pub.post("/api/request", json={"token": token, "name": "n" * 201})
    assert too_long.status_code == 400


def test_visitor_text_is_plain_and_escaped_in_panel(env: Env, panel: TestClient, pub: TestClient,
                                                    ntfy: FakeNtfy) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, [ids["a.txt"]], mode="request")
    ask(pub, token, "<script>alert('x')</script>\x00\x1b[31m", "<img src=x onerror=alert(1)>\n**negrita** [l](http://x)")
    stored = sql(env, "SELECT name, note FROM access_requests", db="public")[0]
    assert "\x00" not in stored[0] and "\x1b" not in stored[0]
    for url in ("/requests", f"/requests/{request_id(env, stored[0])}", f"/links/{link_id}"):
        page = panel.get(url).text
        assert "<script>alert" not in page and "<img src=x" not in page
        assert "&lt;script&gt;" in page
    body = ntfy.posts[0][2].decode()
    assert "\n" not in body


def test_approve_requires_active_link_and_pending_request(env: Env, panel: TestClient, pub: TestClient) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, [ids["a.txt"]], mode="request")
    ask(pub, token, "Rosa")
    rid = request_id(env, "Rosa")
    assert panel.post(f"/requests/{rid}/approve", data={"days": 7}).status_code == 403
    panel.post(f"/links/{link_id}/revoke", data={"csrf": CSRF})
    assert panel.post(f"/requests/{rid}/approve", data={"csrf": CSRF, "days": 7}).status_code == 409
    panel.post(f"/requests/{rid}/reject", data={"csrf": CSRF})
    assert panel.post(f"/requests/{rid}/approve", data={"csrf": CSRF, "days": 7}).status_code == 404
    assert "Rosa" in panel.get("/requests").text


@needs_ffmpeg
def test_nothing_from_jellyfin_reaches_the_public(env: Env, jellyfin: FakeJellyfin) -> None:  # noqa: F811
    from media_management.panel.app import create_app as create_panel
    from tests.conftest import PANEL_HEADERS, TRAEFIK
    from tests.test_manifest import cycle
    settings = env.settings.model_copy(update={"jellyfin_url": jellyfin.url})
    env.settings = settings
    name = "DJI_20310310104500_0001_D.MP4"
    make_video(env.root("buceo") / name, "2031-03-10T09:45:00Z")
    make_video(env.root("originales-120fps") / name, "2031-03-10T09:45:00Z")
    item = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"
    env.write_ids({f"/library/buceo/{name}": item})
    comment = f"jellyfin {item}".encode()
    jellyfin.images[item] = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
                             + b"\xff\xfe" + (len(comment) + 2).to_bytes(2, "big") + comment
                             + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\x12" * 64 + b"\xff\xd9")
    with TestClient(create_panel(settings), client=(TRAEFIK, 1), headers=PANEL_HEADERS) as panel:
        cycle(settings)
        fids = [int(r[0]) for r in sql(env, "SELECT id FROM files ORDER BY root")]
        token, _ = create_link(panel, fids, mode="password", password="buceo-en-grupo")
        with TestClient(create_public(settings), client=(NGINX, 1)) as pub:
            seen = [pub.get("/"), pub.get("/static/share.js"), pub.get("/healthz"),
                    pub.post("/api/share", json={"token": token}),
                    pub.post("/api/share", json={"token": token, "password": "buceo-en-grupo"})]
            files = seen[-1].json()["files"]
            assert all(f["thumb"] and f["thumb"].startswith("data:image/jpeg;base64,") for f in files), [(f["name"], bool(f["thumb"])) for f in files]
            for f in files:
                t = pub.post("/api/ticket", json={"token": token, "file": f["id"], "password": "buceo-en-grupo"})
                seen += [t, pub.get(t.json()["url"]), pub.get(t.json()["url"], headers={"Range": "bytes=5-"})]
            seen += [pub.get("/download/roto"), pub.post("/api/ticket", json={"token": token, "file": 999,
                                                                              "password": "buceo-en-grupo"})]
    host = jellyfin.url.split("//")[1]
    import base64
    for r in seen:
        blob = r.text + str(dict(r.headers))
        for f in files:
            blob += base64.b64decode(f["thumb"].split(",", 1)[1]).decode("latin1")
        assert item not in blob and item.upper() not in blob
        assert host not in blob and "/Items/" not in blob and "jellyfin" not in blob.lower()
        assert "/library/" not in blob


def test_double_approval_is_a_clean_conflict(env: Env, panel: TestClient, pub: TestClient) -> None:
    ids = send_files(env, FILES)
    token, _ = create_link(panel, [ids["a.txt"]], mode="request")
    ask(pub, token, "Doble")
    rid = request_id(env, "Doble")
    approve(panel, env, "Doble")
    sql(env, "UPDATE access_requests SET status = 'pending' WHERE id = ?", (rid,), db="public")
    r = panel.post(f"/requests/{rid}/approve", data={"csrf": CSRF, "days": 7})
    assert r.status_code == 409
    assert sql(env, "SELECT COUNT(*) FROM grants")[0][0] == 1
