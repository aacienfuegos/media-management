import asyncio
import datetime
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_management.db import connect, get_state, iso, utcnow
from media_management.jellyfin import compute_item_id, id_mismatches, load_ids_export
from media_management.manifest import Manifest, compare
from media_management.roots import load_roots
from media_management.settings import Settings
from media_management.worker import run_cycle
from tests.conftest import Env
from tests.media_factory import make_photo, make_video, needs_ffmpeg

JF = "/library/buceo"


def cycle(settings: Settings) -> None:
    async def go() -> None:
        async with connect(settings.main_db) as conn:
            await run_cycle(conn, settings, load_roots(settings))
    asyncio.run(go())


def state(settings: Settings, key: str) -> str | None:
    async def go() -> str | None:
        async with connect(settings.main_db) as conn:
            return await get_state(conn, key)
    return asyncio.run(go())


def library(env: Env) -> dict[str, str]:
    """Una inmersión sintética: dos vídeos con fecha, una foto del mismo día, una foto sin
    vídeos de su día (usa el más cercano), un vídeo sin fecha y un fichero ajeno."""
    b = env.root("buceo")
    make_video(b / "DJI_20310310104500_0001_D.MP4", "2031-03-10T09:45:00Z")
    make_video(b / "DJI_20310310120000_0002_D.MP4", "2031-03-10T11:00:00Z")
    make_photo(b / "DJI_20310310110000_0003_D.JPG", datetime.datetime(2031, 3, 10, 11, 0))
    make_photo(b / "DJI_20310312090000_0004_D.JPG", datetime.datetime(2031, 3, 12, 9, 0))
    make_video(b / "DJI_20310310130000_0005_D.MP4", None)
    (b / "leeme.txt").write_text("no es media")
    (b / ".oculto.MP4").write_bytes(b"x")
    return {f"{JF}/DJI_20310310104500_0001_D.MP4": "a" * 32,
            f"{JF}/DJI_20310310110000_0003_D.JPG": "b" * 32}


@needs_ffmpeg
def test_manifest_matches_contract_and_rules(env: Env, panel: TestClient) -> None:
    env.write_ids(library(env))
    cycle(env.settings)
    assert env.settings.manifest_path is not None
    doc = json.loads(env.settings.manifest_path.read_text())
    Manifest.model_validate(doc)
    assert doc["version"] == 1
    clips = {c["name"]: c for c in doc["clips"]}
    assert list(clips) == sorted(clips)
    assert set(clips) == {"DJI_20310310104500_0001_D.MP4", "DJI_20310310120000_0002_D.MP4",
                          "DJI_20310310110000_0003_D.JPG", "DJI_20310312090000_0004_D.JPG"}
    first = clips["DJI_20310310104500_0001_D.MP4"]
    assert first["jellyfin_path"] == f"{JF}/DJI_20310310104500_0001_D.MP4"
    assert first["jellyfin_item_id"] == "a" * 32
    assert first["captured_at_utc"] == "2031-03-10T09:45:00Z"
    assert first["kind"] == "video" and first["captured_at_source"] == "mp4_mvhd"
    assert clips["DJI_20310310120000_0002_D.MP4"]["jellyfin_item_id"] is None
    photo = clips["DJI_20310310110000_0003_D.JPG"]
    assert (photo["kind"], photo["captured_at_utc"]) == ("photo", "2031-03-10T10:00:00Z")
    far = clips["DJI_20310312090000_0004_D.JPG"]
    assert (far["captured_at_utc"], far["captured_at_source"]) == ("2031-03-12T08:00:00Z",
                                                                  "exif+desfase_de_2031-03-10")
    status = json.loads(state(env.settings, "manifest_status") or "{}")
    assert status["written"] and status["without_date_names"] == ["DJI_20310310130000_0005_D.MP4"]
    assert status["without_id"] == 2


@pytest.mark.parametrize("problem", ["missing", "empty", "stale", "garbage"])
@needs_ffmpeg
def test_no_manifest_without_trustworthy_ids(env: Env, panel: TestClient, problem: str) -> None:
    """Si no se sabe qué tiene indexado Jellyfin, se conserva el manifiesto anterior."""
    ids = library(env)
    env.write_ids(ids)
    cycle(env.settings)
    assert env.settings.manifest_path is not None
    before = env.settings.manifest_path.read_bytes()
    assert env.settings.jellyfin_ids_file is not None
    if problem == "missing":
        env.settings.jellyfin_ids_file.unlink()
    elif problem == "empty":
        env.write_ids({})
    elif problem == "stale":
        env.write_ids(ids, iso(utcnow() - datetime.timedelta(hours=2)))
    else:
        env.settings.jellyfin_ids_file.write_text("{no json")
    make_video(env.root("buceo") / "DJI_20310310140000_0006_D.MP4", "2031-03-10T13:00:00Z")
    cycle(env.settings)
    assert env.settings.manifest_path.read_bytes() == before
    status = json.loads(state(env.settings, "manifest_status") or "{}")
    assert status["written"] is False and status["problem"]
    assert status["last_written_at"]


@needs_ffmpeg
def test_catalog_keeps_known_ids_when_export_is_unusable(env: Env, panel: TestClient) -> None:
    env.write_ids(library(env))
    cycle(env.settings)
    assert env.settings.jellyfin_ids_file is not None
    env.settings.jellyfin_ids_file.unlink()
    cycle(env.settings)
    files = panel.get("/roots/buceo").text
    assert files.count("indexado</span>") == 2


def test_manifest_needs_files(env: Env, panel: TestClient) -> None:
    env.write_ids({f"{JF}/x.MP4": "c" * 32})
    cycle(env.settings)
    status = json.loads(state(env.settings, "manifest_status") or "{}")
    assert status["written"] is False and "ni un fichero" in status["problem"]
    assert env.settings.manifest_path is not None and not env.settings.manifest_path.exists()


@needs_ffmpeg
def test_unmounted_media_is_not_an_empty_library(env: Env, panel: TestClient) -> None:
    env.write_ids(library(env))
    cycle(env.settings)
    assert env.settings.manifest_path is not None
    before = env.settings.manifest_path.read_bytes()
    env.settings.sentinel.unlink()
    for f in env.root("buceo").iterdir():
        f.unlink()
    cycle(env.settings)
    assert env.settings.manifest_path.read_bytes() == before
    assert "DJI_20310310104500_0001_D.MP4" in panel.get("/roots/buceo").text
    assert state(env.settings, "media_problem")


@needs_ffmpeg
def test_scan_tracks_changes_and_skips_symlinks_and_hidden(env: Env, panel: TestClient) -> None:
    s = env.root("send")
    (s / "viaje").mkdir()
    make_video(s / "viaje" / "clip.mp4", "2031-05-01T10:00:00Z")
    (s / "notas.txt").write_text("hola")
    (s / ".borrador").write_text("x")
    os.symlink(env.base.parent / "roots.toml", s / "enlace.toml")
    cycle(env.settings)
    root_page = panel.get("/roots/send").text
    assert "notas.txt" in root_page and "enlace.toml" not in root_page and ".borrador" not in root_page
    assert "clip.mp4" not in root_page and "viaje" in root_page
    assert "clip.mp4" in panel.get("/roots/send", params={"dir": "viaje"}).text
    (s / "notas.txt").unlink()
    cycle(env.settings)
    scan = json.loads(state(env.settings, "last_scan") or "{}")
    assert scan["roots"]["send"]["gone"] == 1
    assert "notas.txt" not in panel.get("/roots/send").text


@needs_ffmpeg
def test_comparator_against_current_manifest(env: Env, tmp_path: Path) -> None:
    current = tmp_path / "manifiesto" / "buceo.json"
    settings = env.settings.model_copy(update={"manifest_compare_with": current})
    ids = library(env)
    env.write_ids(ids)
    from media_management.panel.app import create_app
    with TestClient(create_app(settings)):
        pass
    cycle(settings)
    assert settings.manifest_path is not None
    current.write_bytes(settings.manifest_path.read_bytes())
    cycle(settings)
    result = json.loads(state(settings, "manifest_comparison") or "{}")
    assert result["identical"] is True
    doc = json.loads(current.read_text())
    doc["clips"][0]["size_bytes"] += 1
    doc["clips"].pop()
    current.write_text(json.dumps(doc))
    cycle(settings)
    result = json.loads(state(settings, "manifest_comparison") or "{}")
    problems = sorted(d["problem"] for d in result["differences"])
    assert problems == ["size_bytes distinto", "solo en el candidato"]


def test_compare_by_identity() -> None:
    a = {"clips": [{"jellyfin_path": "/x/1", "jellyfin_item_id": None, "captured_at_utc": "t",
                    "size_bytes": 1, "kind": "video", "name": "ignorado"}]}
    b = {"clips": [{"jellyfin_path": "/x/1", "jellyfin_item_id": "i", "captured_at_utc": "t",
                    "size_bytes": 1, "kind": "video", "name": "otro"}]}
    assert compare(a, a) == []
    assert compare(a, b) == [{"jellyfin_path": "/x/1", "problem": "jellyfin_item_id distinto",
                              "candidate": None, "current": "i"}]


def test_item_id_formula_is_stable_and_well_formed() -> None:
    vid = compute_item_id("video", "/library/buceo/DJI_20310310104500_0001_D.MP4")
    assert vid == compute_item_id("video", "/library/buceo/DJI_20310310104500_0001_D.MP4")
    assert vid is not None and len(vid) == 32 and all(c in "0123456789abcdef" for c in vid)
    assert vid != compute_item_id("video", "/library/buceo/dji_20310310104500_0001_d.mp4")
    assert vid != compute_item_id("photo", "/library/buceo/DJI_20310310104500_0001_D.MP4")
    assert compute_item_id("other", "/x.txt") is None


def test_id_mismatch_detection() -> None:
    good = "/library/buceo/DJI_20310310104500_0001_D.MP4"
    ids = {good: compute_item_id("video", good) or "", "/library/buceo/DJI_2_D.MP4": "f" * 32}
    assert id_mismatches(ids) == ["/library/buceo/DJI_2_D.MP4"]


def test_export_normalizes_ids(tmp_path: Path) -> None:
    f = tmp_path / "ids.json"
    f.write_text(json.dumps({"generated_at": iso(utcnow()),
                             "items": {"/a.MP4": "0A1B2C3D-4E5F-6071-8293-A4B5C6D7E8F9"}}))
    assert load_ids_export(f, 3600).ids == {"/a.MP4": "0a1b2c3d4e5f60718293a4b5c6d7e8f9"}


@needs_ffmpeg
def test_api_manifest_etag(env: Env, panel: TestClient, api: TestClient) -> None:
    env.write_ids(library(env))
    cycle(env.settings)
    from tests.test_api import make_token
    token = make_token(panel)
    auth = {"Authorization": f"Bearer {token}"}
    r = api.get("/api/v1/manifest", headers=auth)
    assert r.status_code == 200
    assert env.settings.manifest_path is not None
    assert r.content == env.settings.manifest_path.read_bytes()
    etag = r.headers["etag"]
    assert api.get("/api/v1/manifest", headers={**auth, "If-None-Match": etag}).status_code == 304
    make_video(env.root("buceo") / "DJI_20310310150000_0007_D.MP4", "2031-03-10T14:00:00Z")
    cycle(env.settings)
    changed = api.get("/api/v1/manifest", headers={**auth, "If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag
