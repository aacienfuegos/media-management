import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from media_management.db import connect
from media_management.panel.app import create_app
from tests.conftest import PANEL_HEADERS, TRAEFIK, Env
from tests.fake_jellyfin import JPEG, FakeJellyfin, jellyfin  # noqa: F401
from tests.media_factory import make_video, needs_ffmpeg
from tests.test_manifest import cycle


@pytest.fixture
def panel_jf(env: Env, jellyfin: FakeJellyfin) -> Iterator[TestClient]:  # noqa: F811
    env.settings = env.settings.model_copy(update={"jellyfin_url": jellyfin.url})
    with TestClient(create_app(env.settings), client=(TRAEFIK, 1), headers=PANEL_HEADERS) as c:
        yield c


def file_id(env: Env, root: str, relpath: str) -> int:
    async def go() -> int:
        async with connect(env.settings.main_db) as conn:
            async with conn.execute("SELECT id FROM files WHERE root = ? AND relpath = ?",
                                    (root, relpath)) as cur:
                row = await cur.fetchone()
        assert row is not None
        return int(row[0])
    return asyncio.run(go())


@needs_ffmpeg
def test_thumbnails_are_cached_and_failures_are_not(env: Env, panel_jf: TestClient,
                                                    jellyfin: FakeJellyfin) -> None:  # noqa: F811
    name = "DJI_20310310104500_0001_D.MP4"
    make_video(env.root("buceo") / name, "2031-03-10T09:45:00Z")
    make_video(env.root("originales-120fps") / name, "2031-03-10T09:45:00Z")
    make_video(env.root("buceo") / "DJI_20310310120000_0002_D.MP4", "2031-03-10T11:00:00Z")
    (env.root("send") / "nota.txt").write_text("x")
    env.write_ids({f"/library/buceo/{name}": "a" * 32,
                   "/library/buceo/DJI_20310310120000_0002_D.MP4": "b" * 32})
    jellyfin.images["a" * 32] = JPEG
    jellyfin.fail_all = True
    cycle(env.settings)
    fid = file_id(env, "buceo", name)
    r = panel_jf.get(f"/thumb/{fid}")
    assert r.headers["content-type"].startswith("image/svg")
    jellyfin.fail_all = False
    r = panel_jf.get(f"/thumb/{fid}")
    assert r.content == JPEG
    hits = len(jellyfin.requests)
    assert panel_jf.get(f"/thumb/{fid}").content == JPEG
    assert len(jellyfin.requests) == hits
    master = file_id(env, "originales-120fps", name)
    assert panel_jf.get(f"/thumb/{master}").content == JPEG
    unknown = panel_jf.get(f"/thumb/{file_id(env, 'buceo', 'DJI_20310310120000_0002_D.MP4')}")
    assert unknown.headers["content-type"].startswith("image/svg")
    assert "sin miniatura" in unknown.text
    other = panel_jf.get(f"/thumb/{file_id(env, 'send', 'nota.txt')}")
    assert "fichero" in other.text


@needs_ffmpeg
def test_worker_prefetches_thumbnails(env: Env, panel_jf: TestClient, jellyfin: FakeJellyfin) -> None:  # noqa: F811
    name = "DJI_20310310104500_0001_D.MP4"
    make_video(env.root("buceo") / name, "2031-03-10T09:45:00Z")
    env.write_ids({f"/library/buceo/{name}": "a" * 32})
    jellyfin.images["a" * 32] = JPEG
    cycle(env.settings)
    assert (env.settings.cache_dir / "thumbs" / ("a" * 32 + ".jpg")).read_bytes() == JPEG


def test_file_names_are_escaped(env: Env, panel: TestClient) -> None:
    evil = '<img src=x onerror=alert(2)>"\' <script>.txt'
    (env.root("send") / evil).write_text("x")
    cycle(env.settings)
    page = panel.get("/roots/send").text
    assert "<img src=x" not in page and "<script>" not in page
    assert "&lt;img src=x onerror=alert(2)&gt;" in page
    detail = panel.get(f"/files/{file_id(env, 'send', evil)}").text
    assert "<img src=x" not in detail and "<script>" not in detail


def test_root_filters_and_views(env: Env, panel: TestClient) -> None:
    for n in ("alpha.txt", "beta.txt", "gamma.jpg"):
        (env.root("send") / n).write_text(n * 10)
    cycle(env.settings)
    assert "beta.txt" not in panel.get("/roots/send", params={"q": "alp"}).text
    only_other = panel.get("/roots/send", params={"kind": "photo"}).text
    assert "gamma.jpg" in only_other and "alpha.txt" not in only_other
    assert panel.get("/roots/nada").status_code == 404
    assert panel.get("/files/424242").status_code == 404
    assert panel.get("/manifest").status_code == 200
    assert panel.get("/manifest/compare").status_code == 200


def test_delete_is_blocked_where_second_copy_is_required(env: Env, panel: TestClient) -> None:
    (env.root("buceo") / "clip.MP4").write_bytes(b"x")
    (env.root("send") / "suelto.txt").write_text("x")
    cycle(env.settings)
    clip_id = file_id(env, "buceo", "clip.MP4")
    clip = panel.get(f"/files/{clip_id}").text
    assert "no consta una segunda copia" in clip and f'href="/files/{clip_id}/trash"' not in clip
    loose_id = file_id(env, "send", "suelto.txt")
    loose = panel.get(f"/files/{loose_id}").text
    assert f'href="/files/{loose_id}/trash"' in loose and "no consta una segunda copia" not in loose
