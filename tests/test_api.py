import re

from fastapi.testclient import TestClient

from media_management.security import csrf_token
from tests.conftest import SECRET, USER, Env
from tests.test_manifest import cycle


def make_token(panel: TestClient, name: str = "integracion") -> str:
    r = panel.post("/tokens", data={"csrf": csrf_token(SECRET, USER), "name": name})
    assert r.status_code == 200
    m = re.search(r'id="new-token">([^<]+)<', r.text)
    assert m, "el token se muestra una vez al crearlo"
    return m.group(1)


def test_token_lifecycle(panel: TestClient, api: TestClient) -> None:
    token = make_token(panel)
    assert token not in panel.get("/tokens").text
    auth = {"Authorization": f"Bearer {token}"}
    assert api.get("/api/v1/roots", headers=auth).status_code == 200
    assert api.get("/api/v1/roots", headers={"Authorization": "Bearer otro"}).status_code == 401
    token_id = re.search(r'action="/tokens/(\d+)/revoke"', panel.get("/tokens").text)
    assert token_id
    panel.post(f"/tokens/{token_id.group(1)}/revoke", data={"csrf": csrf_token(SECRET, USER)})
    assert api.get("/api/v1/roots", headers=auth).status_code == 401
    assert "revocado" in panel.get("/tokens").text
    audit = panel.get("/audit").text
    assert "api_token_created" in audit and "api_token_revoked" in audit
    assert token not in audit


def test_catalog_endpoints(env: Env, panel: TestClient, api: TestClient) -> None:
    (env.root("send") / "a.txt").write_text("hola")
    (env.root("send") / "b.txt").write_text("adios")
    cycle(env.settings)
    auth = {"Authorization": f"Bearer {make_token(panel)}"}
    roots = {r["name"]: r for r in api.get("/api/v1/roots", headers=auth).json()}
    assert roots["send"]["files"] == 2 and roots["buceo"]["in_manifest"] is True
    page = api.get("/api/v1/roots/send/files", params={"page_size": 1}, headers=auth).json()
    assert page["total"] == 2 and [f["name"] for f in page["items"]] == ["a.txt"]
    second = api.get("/api/v1/roots/send/files", params={"page_size": 1, "page": 2}, headers=auth).json()
    assert [f["name"] for f in second["items"]] == ["b.txt"]
    fid = page["items"][0]["id"]
    assert api.get(f"/api/v1/files/{fid}", headers=auth).json()["relpath"] == "a.txt"
    assert api.get("/api/v1/roots/nada/files", headers=auth).status_code == 404
    assert api.get("/api/v1/files/999999", headers=auth).status_code == 404
    assert api.get("/api/v1/manifest", headers=auth).status_code == 503


def test_openapi_is_published_without_external_assets(api: TestClient) -> None:
    spec = api.get("/api/v1/openapi.json").json()
    manifest_schema = spec["components"]["schemas"]["ManifestClip"]
    assert set(manifest_schema["required"]) == {"jellyfin_path", "jellyfin_item_id", "kind",
                                                 "captured_at_utc", "size_bytes"}
    assert api.get("/api/v1/docs").status_code == 404
