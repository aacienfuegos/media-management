"""Pruebas contra datos reales que no se commitean. Se saltan si no están.

- `fixtures/local/buceo.json`: copia de un manifiesto real. El ID calculado tiene
  que coincidir con el de Jellyfin en todos los clips, y el comparador tiene que ver
  cualquier cambio.
- `MM_LEGACY_MANIFEST_SCRIPT`: ruta al generador de manifiesto anterior. Se ejecuta
  de verdad sobre la biblioteca sintética (con un `pct` falso que devuelve los IDs) y
  su salida tiene que coincidir clip a clip, diagnóstico incluido, con la de esta app.
"""
import copy
import datetime
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_management.jellyfin import compute_item_id
from media_management.manifest import Manifest, compare
from tests.conftest import Env
from tests.media_factory import make_photo, make_video, needs_ffmpeg
from tests.test_manifest import cycle

pytestmark = pytest.mark.local
FIXTURE = Path(__file__).parent.parent / "fixtures" / "local" / "buceo.json"
LEGACY = os.environ.get("MM_LEGACY_MANIFEST_SCRIPT")


@pytest.fixture
def real_manifest() -> dict[str, object]:
    if not FIXTURE.exists():
        pytest.skip("no hay fixtures/local/buceo.json (datos reales, fuera de git)")
    doc: dict[str, object] = json.loads(FIXTURE.read_text())
    return doc


def test_real_manifest_validates_against_contract(real_manifest: dict[str, object]) -> None:
    Manifest.model_validate(real_manifest)


def test_item_id_formula_matches_every_real_clip(real_manifest: dict[str, object]) -> None:
    clips = real_manifest["clips"]
    assert isinstance(clips, list) and clips
    matched = sum(compute_item_id(c["kind"], c["jellyfin_path"]) == c["jellyfin_item_id"] for c in clips)
    assert matched == len(clips), f"{matched}/{len(clips)}"


def test_comparator_on_real_manifest(real_manifest: dict[str, object]) -> None:
    assert compare(real_manifest, real_manifest) == []
    changed = copy.deepcopy(real_manifest)
    clips = changed["clips"]
    assert isinstance(clips, list)
    clips[3]["captured_at_utc"] = "2000-01-01T00:00:00Z"
    clips[7]["jellyfin_item_id"] = None
    del clips[11]
    diffs = compare(changed, real_manifest)
    assert len(diffs) == 3


FAKE_PCT = """
import os, sys
sys.stdout.write(open(os.environ["FAKE_JELLYFIN_ROWS"]).read())
"""


@needs_ffmpeg
def test_parity_with_legacy_generator(env: Env, panel: TestClient, tmp_path: Path) -> None:
    if not LEGACY or not Path(LEGACY).is_file():
        pytest.skip("MM_LEGACY_MANIFEST_SCRIPT no apunta al generador anterior")
    b = env.root("buceo")
    make_video(b / "DJI_20310310104500_0001_D.MP4", "2031-03-10T09:45:00Z", seconds=2)
    make_video(b / "DJI_20310310120000_0002_D.MP4", "2031-03-10T11:00:03Z", size="128x72")
    make_video(b / "DJI_20310310121500_0003_D.MP4", "2031-03-10T11:15:01Z")
    make_photo(b / "DJI_20310310110000_0004_D.JPG", datetime.datetime(2031, 3, 10, 11, 0))
    make_photo(b / "DJI_20310315080000_0005_D.JPG", datetime.datetime(2031, 3, 15, 8, 0))
    make_video(b / "DJI_20310720090000_0006_D.MP4", "2031-07-20T07:00:00Z")
    make_photo(b / "DJI_20310720093000_0007_D.JPG", datetime.datetime(2031, 7, 20, 9, 30))
    make_video(b / "DJI_20310720100000_0008_D.MP4", None)
    make_photo(b / "sin_exif.jpg", None)
    make_video(b / "otra_camara.mov", "2031-08-01T12:00:00Z")
    (b / "subcarpeta").mkdir()
    make_video(b / "subcarpeta" / "DJI_20310720110000_0009_D.MP4", "2031-07-20T09:00:00Z")
    (b / "notas.txt").write_text("x")
    names = sorted(p.name for p in b.iterdir() if p.is_file() and p.suffix.lower() in (".mp4", ".jpg", ".mov"))
    ids = {}
    for i, name in enumerate(names):
        if i % 3 == 2:
            continue
        kind = "photo" if name.lower().endswith(".jpg") else "video"
        ids[f"/library/buceo/{name}"] = compute_item_id(kind, f"/library/buceo/{name}") or ""
    env.write_ids(ids)
    cycle(env.settings)
    assert env.settings.manifest_path is not None
    ours = json.loads(env.settings.manifest_path.read_text())

    rows = tmp_path / "rows.txt"
    rows.write_text("".join(f"{uuid.UUID(hex=i)}|{p}\n" for p, i in ids.items()))
    fake = tmp_path / "fakepct.py"
    fake.write_text(FAKE_PCT)
    out = tmp_path / "legacy.json"
    result = subprocess.run(
        [sys.executable, LEGACY], capture_output=True, text=True,
        env={**os.environ, "BUCEO_ORIGEN": str(b), "BUCEO_MANIFIESTO": str(out), "BUCEO_MONTAJE": "/",
             "BUCEO_JELLY_DIR": "/library/buceo", "BUCEO_PCT": f"{sys.executable} {fake}",
             "FAKE_JELLYFIN_ROWS": str(rows)})
    assert result.returncode == 0, result.stdout + result.stderr
    legacy = json.loads(out.read_text())
    assert compare(ours, legacy) == []
    assert ours["clips"] == legacy["clips"]
    assert len(ours["clips"]) >= 8
