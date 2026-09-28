"""Modo demo: la siembra y el manifiesto que sale de los fixtures versionados."""
import asyncio
import json
from pathlib import Path

import pytest

from media_management.db import init_main
from media_management.demo.seed import NotDemo, seed
from media_management.manifest import compare
from media_management.settings import Settings
from tests.test_manifest import cycle, state

DEMO = Path(__file__).parent.parent / "deploy" / "demo"


def demo_settings(tmp_path: Path) -> Settings:
    base = tmp_path / "demo" / "media"
    roots = tmp_path / "roots.toml"
    roots.write_text((DEMO / "roots.toml").read_text().replace('"/media/', f'"{base}/'))
    return Settings(roots_file=roots, media_base=base, data_dir=tmp_path / "demo" / "data",
                    zips_dir=tmp_path / "demo" / "zips",
                    manifest_path=tmp_path / "demo" / "manifest" / "buceo.json",
                    jellyfin_ids_file=tmp_path / "demo" / "ids" / "jellyfin-ids.json",
                    jellyfin_ids_max_age_s=3600)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return demo_settings(tmp_path)


def test_manifest_matches_expected(settings: Settings, tmp_path: Path) -> None:
    """El mismo manifiesto que comprueba la CI contra el stack de verdad."""
    assert seed(settings, tmp_path / "demo" / "jellyfin")
    asyncio.run(init_main(settings))
    cycle(settings)
    assert settings.manifest_path is not None
    doc = json.loads(settings.manifest_path.read_text())
    assert compare(doc, json.loads((DEMO / "manifest.expected.json").read_text())) == []
    status = json.loads(state(settings, "manifest_status") or "{}")
    assert status["without_date_names"] == ["DJI_20250119120000_0009_D.MP4"]
    assert status["odd_names"] == ["DJI_20250118101500_0001_D(1).MP4", "GOPR0042.MP4"]
    assert status["without_id"] == 1
    assert status["id_mismatches"] == 0
    sources = {c["name"]: c["captured_at_source"] for c in doc["clips"]}
    assert sources["DJI_20250120090000_0007_D.JPG"] == "exif+desfase_de_2025-01-19"


def test_thumbnail_per_indexed_item(settings: Settings, tmp_path: Path) -> None:
    images = tmp_path / "demo" / "jellyfin"
    seed(settings, images)
    assert settings.jellyfin_ids_file is not None
    items = json.loads(settings.jellyfin_ids_file.read_text())["items"]
    assert {p.name for p in images.glob("*.jpg")} == {f"{i}.jpg" for i in items.values()}


def test_second_seed_keeps_what_was_tried(settings: Settings, tmp_path: Path) -> None:
    images = tmp_path / "demo" / "jellyfin"
    seed(settings, images)
    renamed = settings.media_base / "send" / "renombrado.mp4"
    (settings.media_base / "send" / "resumen-viaje.mp4").rename(renamed)
    assert not seed(settings, images)
    assert renamed.exists()
    assert not (settings.media_base / "send" / "resumen-viaje.mp4").exists()


def test_refuses_a_directory_that_is_not_demo(settings: Settings, tmp_path: Path) -> None:
    """Con un .env equivocado, la siembra no toca nada, tampoco los demás directorios."""
    settings.media_base.mkdir(parents=True)
    real = settings.media_base / "buceo"
    real.mkdir()
    (real / "DJI_real.MP4").write_bytes(b"x")
    with pytest.raises(NotDemo):
        seed(settings, tmp_path / "demo" / "jellyfin")
    assert (real / "DJI_real.MP4").read_bytes() == b"x"
    assert not settings.data_dir.exists()
    assert not (tmp_path / "demo" / "jellyfin").exists()
