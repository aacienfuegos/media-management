import asyncio
import logging
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_management import zips
from media_management.roots import load_roots
from tests.conftest import Env
from tests.helpers import CSRF, create_link, link_file_ids, reconcile, send_files, sql, ticket_url, zip_url

FILES = {"a.txt": b"A" * 1000, "b.txt": b"B" * 2000, "otra/A.txt": b"otra", "c.bin": os.urandom(5000)}


def zip_files(env: Env) -> list[str]:
    assert env.settings.zips_dir is not None
    return sorted(p.name for p in env.settings.zips_dir.iterdir() if p.name != zips.MARKER)


def share(public: TestClient, token: str) -> dict[str, object]:
    r = public.post("/api/share", json={"token": token})
    assert r.status_code == 200
    data: dict[str, object] = r.json()
    return data


def on_disk(env: Env, url: str, public: TestClient) -> Path:
    r = public.get(url)
    assert r.status_code == 200, r.text
    accel = r.headers["x-accel-redirect"]
    assert accel.startswith("/_zips/")
    assert env.settings.zips_dir is not None
    return env.settings.zips_dir / accel.removeprefix("/_zips/")


def test_zip_is_built_by_the_worker_and_served_by_nginx(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, list(ids.values()), title="Viaje/Asturias", zip=True)
    assert share(public, token)["zip"] == {"state": "pending"}
    assert public.post("/api/zip", json={"token": token}).status_code == 404
    reconcile(env)
    data = share(public, token)
    path = on_disk(env, zip_url(public, token), public)
    assert data["zip"] == {"state": "ready", "size": path.stat().st_size}
    r = public.get(zip_url(public, token))
    assert r.content == b"" and "filename*=UTF-8''ViajeAsturias.zip" in r.headers["content-disposition"]
    with zipfile.ZipFile(path) as zf:
        assert {i.compress_type for i in zf.infolist()} == {zipfile.ZIP_STORED}
        assert zf.testzip() is None
        contents = {n: zf.read(n) for n in zf.namelist()}
    assert contents == {"a.txt": FILES["a.txt"], "b.txt": FILES["b.txt"], "A (2).txt": b"otra",
                        "c.bin": FILES["c.bin"]}
    if shutil.which("unzip"):
        assert subprocess.run(["unzip", "-t", str(path)], capture_output=True).returncode == 0
    assert zip_files(env) == [f"{link_id}-1.zip"]
    assert sql(env, "SELECT link_file_id FROM tickets", db="public")[-1] == (0,)
    reconcile(env)
    assert zip_files(env) == [f"{link_id}-1.zip"]


def test_zip64_entries_open_everywhere(env: Env, panel: TestClient, public: TestClient,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Un fichero de más de 4 GB no cabe en el CI: se baja el límite de zip64 para
    que las entradas pequeñas lleven los mismos registros que llevaría uno grande."""
    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 1000)
    ids = send_files(env, FILES)
    token, _ = create_link(panel, list(ids.values()), zip=True)
    reconcile(env)
    path = on_disk(env, zip_url(public, token), public)
    raw = path.read_bytes()
    assert b"PK\x06\x06" in raw
    monkeypatch.undo()
    with zipfile.ZipFile(path) as zf:
        assert zf.read("c.bin") == FILES["c.bin"]
    if shutil.which("unzip"):
        assert subprocess.run(["unzip", "-t", str(path)], capture_output=True).returncode == 0


def test_only_with_two_or_more_files_and_when_enabled(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    single, _ = create_link(panel, [ids["a.txt"]], zip=True)
    off, off_id = create_link(panel, [ids["a.txt"], ids["b.txt"]])
    reconcile(env, 2)
    assert share(public, single)["zip"] is None and share(public, off)["zip"] is None
    assert zip_files(env) == []
    assert "Ofrecer un ZIP con todo" in panel.get(f"/links/{off_id}").text
    panel.post(f"/links/{off_id}/zip", data={"csrf": CSRF, "action": "enable"})
    reconcile(env)
    assert zip_files(env) == [f"{off_id}-1.zip"]
    panel.post(f"/links/{off_id}/zip", data={"csrf": CSRF, "action": "disable"})
    reconcile(env)
    assert zip_files(env) == [] and share(public, off)["zip"] is None


def test_trash_invalidates_at_once_and_restore_brings_it_back(env: Env, panel: TestClient,
                                                              public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, list(ids.values()), zip=True)
    reconcile(env)
    old_url = zip_url(public, token)
    assert public.get(old_url, headers={"Range": "bytes=0-99"}).status_code == 200
    panel.post(f"/files/{ids['b.txt']}/trash", data={"csrf": CSRF})
    assert share(public, token)["zip"] == {"state": "pending"}
    assert public.get(old_url, headers={"Range": "bytes=100-"}).status_code == 404
    reconcile(env)
    assert zip_files(env) == [f"{link_id}-2.zip"]
    with zipfile.ZipFile(on_disk(env, zip_url(public, token), public)) as zf:
        assert "b.txt" not in zf.namelist()
    entry = sql(env, "SELECT id FROM trash_entries")[0][0]
    panel.post(f"/trash/{entry}/restore", data={"csrf": CSRF})
    reconcile(env)
    assert zip_files(env) == [f"{link_id}-3.zip"]
    with zipfile.ZipFile(on_disk(env, zip_url(public, token), public)) as zf:
        assert "b.txt" in zf.namelist()


def test_rename_rebuilds_with_the_new_name(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, _ = create_link(panel, list(ids.values()), zip=True)
    reconcile(env)
    panel.post(f"/files/{ids['a.txt']}/rename", data={"csrf": CSRF, "new_name": "nuevo.txt"})
    assert share(public, token)["zip"] == {"state": "pending"}
    reconcile(env)
    with zipfile.ZipFile(on_disk(env, zip_url(public, token), public)) as zf:
        assert "nuevo.txt" in zf.namelist() and "a.txt" not in zf.namelist()


def test_revoke_expiry_and_renewal(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, list(ids.values()), zip=True)
    reconcile(env)
    url = zip_url(public, token)
    sql(env, "UPDATE links SET expires_at = '2000-01-01T00:00:00Z' WHERE id = ?", (link_id,))
    assert public.get(url).status_code == 404
    reconcile(env)
    assert zip_files(env) == []
    panel.post(f"/links/{link_id}/renew", data={"csrf": CSRF, "days": 3})
    reconcile(env)
    assert zip_files(env) == [f"{link_id}-2.zip"]
    url = zip_url(public, token)
    assert public.get(url, headers={"Range": "bytes=0-9"}).status_code == 200
    panel.post(f"/links/{link_id}/revoke", data={"csrf": CSRF})
    assert public.get(url, headers={"Range": "bytes=10-"}).status_code == 404
    reconcile(env)
    assert zip_files(env) == []


def test_zip_ticket_needs_credentials(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, _ = create_link(panel, list(ids.values()), mode="password", password="buceo-en-grupo", zip=True)
    reconcile(env)
    assert public.post("/api/zip", json={"token": token}).json() == {"need": "password"}
    assert public.post("/api/zip", json={"token": token, "password": "adivina"}).status_code == 401
    assert public.get(zip_url(public, token, password="buceo-en-grupo")).status_code == 200


@pytest.mark.parametrize("limits", [{"zip_min_free_gb": 10**6}, {"zip_max_gb": 1}])
def test_skipped_without_room_and_single_files_still_work(env: Env, panel: TestClient, public: TestClient,
                                                          limits: dict[str, int],
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, list(ids.values()), zip=True)
    if "zip_max_gb" in limits:
        monkeypatch.setattr(zips, "GB", 1000)
    env.settings = env.settings.model_copy(update=limits)
    reconcile(env)
    assert zip_files(env) == []
    assert share(public, token)["zip"] is None
    assert public.post("/api/zip", json={"token": token}).status_code == 404
    assert "no se genera" in panel.get(f"/links/{link_id}").text
    assert public.get(ticket_url(public, token, link_file_ids(public, token)[0])).status_code == 200


def test_failures_back_off_and_stop(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, list(ids.values()), zip=True)
    (env.root("send") / "b.txt").chmod(0)
    try:
        reconcile(env, 3)
        row = sql(env, "SELECT failures, problem FROM link_zips WHERE link_id = ?", (link_id,))[0]
        assert row[0] == 1 and "error al generarlo" in row[1]
        assert share(public, token)["zip"] is None and zip_files(env) == []
        sql(env, "UPDATE link_zips SET failures = 5, failed_at = '2000-01-01T00:00:00Z'")
        reconcile(env)
        assert sql(env, "SELECT failures FROM link_zips")[0][0] == 5
        assert "ha fallado" in panel.get(f"/links/{link_id}").text
    finally:
        (env.root("send") / "b.txt").chmod(0o644)
    panel.post(f"/links/{link_id}/zip", data={"csrf": CSRF, "action": "regenerate"})
    reconcile(env)
    assert zip_files(env) == [f"{link_id}-1.zip"]
    assert share(public, token)["zip"]["state"] == "ready"  # type: ignore[index]


def test_hostile_names_stay_inside(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    sql(env, "UPDATE files SET name = ? WHERE id = ?", ("../../x\x01\x1b[31m", ids["a.txt"]))
    sql(env, "UPDATE files SET name = ? WHERE id = ?", ("..", ids["b.txt"]))
    token, link_id = create_link(panel, [ids["a.txt"], ids["b.txt"]], zip=True)
    reconcile(env)
    with zipfile.ZipFile(on_disk(env, zip_url(public, token), public)) as zf:
        names = zf.namelist()
    assert names == ["_.._x[31m", "fichero"]
    assert zip_files(env) == [f"{link_id}-1.zip"]


def test_without_marker_nothing_is_written_or_deleted(env: Env, panel: TestClient, public: TestClient,
                                                      caplog: pytest.LogCaptureFixture) -> None:
    assert env.settings.zips_dir is not None
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, list(ids.values()), zip=True)
    reconcile(env)
    built = f"{link_id}-1.zip"
    (env.settings.zips_dir / zips.MARKER).unlink()
    (env.settings.zips_dir / "99-1.zip").write_bytes(b"huerfano")
    panel.post(f"/links/{link_id}/zip", data={"csrf": CSRF, "action": "regenerate"})
    reconcile(env, 2)
    assert zip_files(env) == sorted(["99-1.zip", built])
    assert share(public, token)["zip"] is None
    assert zips.NOT_MOUNTED in panel.get(f"/links/{link_id}").text

    async def one_turn() -> None:
        task = asyncio.create_task(zips.zip_loop(env.settings, load_roots(env.settings)))
        await asyncio.sleep(0.5)
        task.cancel()
    with caplog.at_level(logging.ERROR, logger="media_management.zips"):
        asyncio.run(one_turn())
    assert "falta el marcador" in caplog.text
    (env.settings.zips_dir / zips.MARKER).write_text("")
    reconcile(env)
    assert zip_files(env) == [f"{link_id}-2.zip"]


def test_worker_dying_mid_build_leaves_nothing_behind(env: Env, panel: TestClient, public: TestClient) -> None:
    assert env.settings.zips_dir is not None
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, list(ids.values()), zip=True)
    stale = env.settings.zips_dir / f".{link_id}-1.zip.4242.tmp"
    stale.write_bytes(b"a medias")
    sql(env, "UPDATE link_zips SET building_at = '2000-01-01T00:00:00Z'")
    assert share(public, token)["zip"] == {"state": "pending"}

    async def one_turn() -> None:
        task = asyncio.create_task(zips.zip_loop(env.settings, load_roots(env.settings)))
        await asyncio.sleep(1)
        task.cancel()
    asyncio.run(one_turn())
    assert not stale.exists() and zip_files(env) == [f"{link_id}-1.zip"]
    assert share(public, token)["zip"]["state"] == "ready"  # type: ignore[index]


def test_changes_during_the_build_discard_it(env: Env, panel: TestClient, public: TestClient,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, list(ids.values()), zip=True)
    real_write = zips._write

    def write_then_change(zdir: Path, name: str, entries: zips.Entries) -> int:
        size = real_write(zdir, name, entries)
        (env.root("send") / "a.txt").write_bytes(b"cambiado")
        return size
    monkeypatch.setattr(zips, "_write", write_then_change)
    reconcile(env)
    assert zip_files(env) == [] and share(public, token)["zip"] == {"state": "pending"}
    monkeypatch.undo()
    reconcile(env)
    with zipfile.ZipFile(on_disk(env, zip_url(public, token), public)) as zf:
        assert zf.read("a.txt") == b"cambiado"


def test_sweep_only_touches_its_own_names(env: Env, panel: TestClient) -> None:
    assert env.settings.zips_dir is not None
    zdir = env.settings.zips_dir
    for name in ("notas.txt", "1.zip", "a-1.zip", ".1-1.zip.tmp", "1-1.zip.bak"):
        (zdir / name).write_text("x")
    outside = env.base.parent / "fuera.zip"
    outside.write_text("x")
    os.symlink(outside, zdir / "7-1.zip")
    (zdir / "5-1.zip").write_text("x")
    (zdir / ".5-2.zip.99.tmp").write_text("x")
    reconcile(env)
    assert sorted(os.listdir(zdir)) == sorted([".zips-root", "notas.txt", "1.zip", "a-1.zip", ".1-1.zip.tmp",
                                              "1-1.zip.bak", "7-1.zip"])
    assert outside.exists()


def test_zip_logs_never_carry_the_ticket(env: Env, panel: TestClient, public: TestClient,
                                         caplog: pytest.LogCaptureFixture) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, list(ids.values()), zip=True)
    with caplog.at_level(logging.DEBUG):
        reconcile(env)
        url = zip_url(public, token)
        assert public.get(url).status_code == 200
        assert f"/links/{link_id}" in panel.get(f"/links/{link_id}").request.url.path
    ours = "\n".join(r.getMessage() + str(getattr(r, "fields", ""))
                     for r in caplog.records if r.name.startswith("media_management"))
    ticket = url.rsplit("/", 1)[1]
    assert ours and ticket not in ours and ticket.split(".")[0] not in ours and token not in ours


def test_panel_shows_what_each_zip_download_contained(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, FILES)
    token, link_id = create_link(panel, list(ids.values()), zip=True)
    assert "en cola" in panel.get(f"/links/{link_id}").text
    reconcile(env)
    public.get(zip_url(public, token))
    assert "listo" in panel.get(f"/links/{link_id}").text
    panel.post(f"/files/{ids['c.bin']}/trash", data={"csrf": CSRF})
    reconcile(env)
    public.get(zip_url(public, token))
    page = panel.get(f"/links/{link_id}").text
    assert '<span class="tag">ZIP</span> a.txt, b.txt, A (2).txt<' in page
    assert '<span class="tag">ZIP</span> a.txt, b.txt, A (2).txt, c.bin<' in page


def test_new_link_form_offers_zip_only_for_several_files(env: Env, panel: TestClient) -> None:
    ids = send_files(env, FILES)
    one = panel.get("/links/new", params={"file_id": [ids["a.txt"]]}).text
    two = panel.get("/links/new", params={"file_id": [ids["a.txt"], ids["b.txt"]]}).text
    assert 'name="zip"' not in one
    assert 'name="zip" value="true" checked' in two

