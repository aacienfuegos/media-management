import datetime
import os
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_management.trash import rename_noreplace, valid_new_name
from tests.conftest import Env
from tests.helpers import CSRF, create_link, link_file_ids, send_files, sql, ticket_url
from tests.test_manifest import cycle

SRC = Path(__file__).parent.parent / "src"
PURGE = Path(__file__).parent.parent / "deploy" / "host" / "media-trash-purge.sh"


def today() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")


def test_app_never_unlinks() -> None:
    forbidden = re.compile(r"\b(unlink|rmtree|os\.remove|os\.rmdir|shutil\.move|shutil\.rmtree)\s*\(")
    offenders = [f"{p}:{i}" for p in SRC.rglob("*.py")
                 for i, line in enumerate(p.read_text().splitlines(), 1) if forbidden.search(line)]
    assert offenders == []


def test_rename_noreplace_never_overwrites(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("A")
    (tmp_path / "b").write_text("B")
    with pytest.raises(FileExistsError):
        rename_noreplace(tmp_path / "a", tmp_path / "b")
    assert (tmp_path / "a").read_text() == "A" and (tmp_path / "b").read_text() == "B"
    rename_noreplace(tmp_path / "a", tmp_path / "c")
    assert (tmp_path / "c").read_text() == "A" and not (tmp_path / "a").exists()


def test_trash_moves_out_of_the_library_and_stops_serving(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, {"viaje/nota.txt": b"hola", "otra.txt": b"x"})
    token, _ = create_link(panel, [ids["viaje/nota.txt"]])
    url = ticket_url(public, token, link_file_ids(public, token)[0])
    confirm = panel.get(f"/files/{ids['viaje/nota.txt']}/trash").text
    assert "Sí, mover a la papelera" in confirm
    r = panel.post(f"/files/{ids['viaje/nota.txt']}/trash", data={"csrf": CSRF}, follow_redirects=False)
    assert r.status_code == 303
    moved = env.settings.trash_dir / "send" / today() / "viaje" / "nota.txt"
    assert moved.read_bytes() == b"hola" and not (env.root("send") / "viaje" / "nota.txt").exists()
    assert public.get(url).status_code == 404
    assert public.post("/api/share", json={"token": token}).json()["files"] == []
    assert "nota.txt" not in panel.get("/roots/send", params={"dir": "viaje"}).text
    assert "viaje/nota.txt" in panel.get("/trash").text
    cycle(env.settings)
    assert "nota.txt" not in panel.get("/roots/send", params={"dir": "viaje"}).text
    assert "file_trash" in panel.get("/audit").text


def test_trash_is_refused_in_the_route_where_a_second_copy_is_required(env: Env, panel: TestClient) -> None:
    (env.root("buceo") / "unico.MP4").write_bytes(b"irrepetible")
    cycle(env.settings)
    fid = sql(env, "SELECT id FROM files WHERE relpath = 'unico.MP4'")[0][0]
    r = panel.post(f"/files/{fid}/trash", data={"csrf": CSRF})
    assert r.status_code == 409 and "no consta una segunda copia" in r.text
    assert (env.root("buceo") / "unico.MP4").read_bytes() == b"irrepetible"
    assert sql(env, "SELECT COUNT(*) FROM trash_entries")[0][0] == 0


def test_same_name_twice_the_same_day(env: Env, panel: TestClient) -> None:
    for _ in range(2):
        ids = send_files(env, {"a.txt": b"v"})
        panel.post(f"/files/{ids['a.txt']}/trash", data={"csrf": CSRF})
    day = env.settings.trash_dir / "send" / today()
    assert sorted(p.name for p in day.iterdir()) == ["a (2).txt", "a.txt"]


def test_restore_puts_it_back_but_never_over_another(env: Env, panel: TestClient) -> None:
    ids = send_files(env, {"a.txt": b"original"})
    panel.post(f"/files/{ids['a.txt']}/trash", data={"csrf": CSRF})
    (env.root("send") / "a.txt").write_bytes(b"nuevo")
    entry = sql(env, "SELECT id FROM trash_entries")[0][0]
    r = panel.post(f"/trash/{entry}/restore", data={"csrf": CSRF})
    assert r.status_code == 409 and "ya existe" in r.text
    assert (env.root("send") / "a.txt").read_bytes() == b"nuevo"
    (env.root("send") / "a.txt").rename(env.base.parent / "apartado.txt")
    r = panel.post(f"/trash/{entry}/restore", data={"csrf": CSRF}, follow_redirects=False)
    assert r.status_code == 303
    assert (env.root("send") / "a.txt").read_bytes() == b"original"
    assert "restaurado" in panel.get("/trash").text
    assert panel.post(f"/trash/{entry}/restore", data={"csrf": CSRF}).status_code == 404


def test_restore_refuses_a_parent_swapped_for_a_symlink(env: Env, panel: TestClient) -> None:
    ids = send_files(env, {"dir/a.txt": b"x"})
    panel.post(f"/files/{ids['dir/a.txt']}/trash", data={"csrf": CSRF})
    (env.root("send") / "dir").rmdir()
    outside = env.base.parent / "fuera"
    outside.mkdir()
    os.symlink(outside, env.root("send") / "dir")
    entry = sql(env, "SELECT id FROM trash_entries")[0][0]
    assert panel.post(f"/trash/{entry}/restore", data={"csrf": CSRF}).status_code == 409
    assert list(outside.iterdir()) == []


def test_trash_refuses_a_file_swapped_for_a_symlink(env: Env, panel: TestClient) -> None:
    ids = send_files(env, {"a.txt": b"x"})
    (env.root("send") / "a.txt").unlink()
    (env.root("send") / "b.txt").write_text("otro")
    os.symlink(env.root("send") / "b.txt", env.root("send") / "a.txt")
    r = panel.post(f"/files/{ids['a.txt']}/trash", data={"csrf": CSRF})
    assert r.status_code == 409
    assert (env.root("send") / "b.txt").read_text() == "otro"


def test_rename_only_where_allowed(env: Env, panel: TestClient, public: TestClient) -> None:
    ids = send_files(env, {"a.txt": b"A", "b.txt": b"B"})
    (env.root("buceo") / "DJI_1.MP4").write_bytes(b"clip")
    cycle(env.settings)
    clip = sql(env, "SELECT id FROM files WHERE relpath = 'DJI_1.MP4'")[0][0]
    assert panel.get(f"/files/{clip}/rename").status_code == 403
    assert panel.post(f"/files/{clip}/rename", data={"csrf": CSRF, "new_name": "otro.MP4"}).status_code == 403
    assert (env.root("buceo") / "DJI_1.MP4").exists()
    assert f'/files/{clip}/rename' not in panel.get(f"/files/{clip}").text
    token, _ = create_link(panel, [ids["a.txt"]])
    for bad in ("../fuera.txt", ".oculto", "x/y", "", "a\x00b"):
        assert panel.post(f"/files/{ids['a.txt']}/rename", data={"csrf": CSRF, "new_name": bad}).status_code == 400
    clash = panel.post(f"/files/{ids['a.txt']}/rename", data={"csrf": CSRF, "new_name": "b.txt"})
    assert clash.status_code == 409 and (env.root("send") / "b.txt").read_bytes() == b"B"
    ok = panel.post(f"/files/{ids['a.txt']}/rename", data={"csrf": CSRF, "new_name": "informe final.txt"},
                    follow_redirects=False)
    assert ok.status_code == 303 and (env.root("send") / "informe final.txt").read_bytes() == b"A"
    url = ticket_url(public, token, link_file_ids(public, token)[0])
    assert public.get(url).headers["x-accel-redirect"].endswith("/informe%20final.txt")
    assert public.post("/api/share", json={"token": token}).json()["files"][0]["name"] == "informe final.txt"


def test_valid_new_name() -> None:
    assert valid_new_name("  foto 1.jpg ") == "foto 1.jpg"
    assert valid_new_name("x" * 256) is None
    assert valid_new_name("ñ" * 200) is None


def test_destructive_actions_refuse_without_media(env: Env, panel: TestClient) -> None:
    ids = send_files(env, {"a.txt": b"x"})
    env.settings.sentinel.unlink()
    assert panel.post(f"/files/{ids['a.txt']}/trash", data={"csrf": CSRF}).status_code == 503


def test_worker_notices_purged_entries(env: Env, panel: TestClient) -> None:
    ids = send_files(env, {"a.txt": b"x"})
    panel.post(f"/files/{ids['a.txt']}/trash", data={"csrf": CSRF})
    (env.settings.trash_dir / "send" / today() / "a.txt").unlink()
    cycle(env.settings)
    assert "vaciado" in panel.get("/trash").text


def run_purge(trash: Path, *args: str, days: str = "30") -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(PURGE), *args], capture_output=True, text=True,
                          env={**os.environ, "TRASH_DIR": str(trash), "RETENTION_DAYS": days})


def test_host_purge_only_removes_old_day_folders(tmp_path: Path) -> None:
    trash = tmp_path / "media" / ".trash"
    old = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
    recent = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    for root, day in (("send", old), ("send", recent), ("buceo", old)):
        (trash / root / day).mkdir(parents=True)
        (trash / root / day / "f.bin").write_bytes(b"x")
    (trash / "send" / "no-es-fecha").mkdir()
    outside = tmp_path / "importante"
    (outside / "2000-01-01").mkdir(parents=True)
    (outside / "2000-01-01" / "irrepetible.MP4").write_bytes(b"x")
    os.symlink(outside, trash / "send" / "2000-01-01")
    os.symlink(outside, trash / "raiz-plantada")
    dry = run_purge(trash, "-n")
    assert dry.returncode == 0 and (trash / "send" / old).exists()
    r = run_purge(trash)
    assert r.returncode == 0, r.stderr
    assert not (trash / "send" / old).exists() and not (trash / "buceo" / old).exists()
    assert (trash / "send" / recent / "f.bin").exists() and (trash / "send" / "no-es-fecha").exists()
    assert (outside / "2000-01-01" / "irrepetible.MP4").exists()
    assert (trash / "send" / "2000-01-01").is_symlink() and (trash / "raiz-plantada").is_symlink()


def test_host_purge_refuses_wrong_dirs(tmp_path: Path) -> None:
    assert run_purge(tmp_path / "media").returncode == 2
    assert run_purge(tmp_path / "media" / ".trash").returncode == 1
    (tmp_path / "media" / ".trash").mkdir(parents=True)
    assert run_purge(tmp_path / "media" / ".trash", days="treinta").returncode == 2
