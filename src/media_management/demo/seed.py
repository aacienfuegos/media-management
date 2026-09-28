"""Siembra de la biblioteca del modo demo con los fixtures del paquete.

Solo escribe en directorios vacíos o marcados con `.demo-root`, y lo comprueba en
todos antes de tocar ninguno: con un `.env` equivocado, el demo apunta a una
biblioteca real, y lo que tiene que pasar es que no arranque.

Nunca borra: volver a sembrar desde cero es vaciar los directorios desde fuera
(`./demo.sh reset`). La app no desenlaza nada, tampoco en modo demo."""
import datetime
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from media_management.catalog import walk
from media_management.db import now_iso
from media_management.jellyfin import compute_item_id
from media_management.manifest import manifest_kind
from media_management.roots import Root, load_roots
from media_management.settings import Settings
from media_management.zips import MARKER as ZIPS_MARKER

log = logging.getLogger(__name__)
MARKER = ".demo-root"
FIXTURES = Path(__file__).parent / "fixtures"
# Hora a la que "se descargó la tarjeta": la mtime de todos los ficheros, para que la
# fecha de los que no traen otra no dependa de cuándo se sembró.
COPIED_AT = datetime.datetime(2025, 1, 21, 20, 0, tzinfo=datetime.UTC).timestamp()
# Jellyfin aún no lo ha indexado: sale en el manifiesto con jellyfin_item_id null.
NOT_INDEXED = frozenset({"DJI_20250119113000_0008_D.MP4"})


class NotDemo(Exception):
    pass


@dataclass(frozen=True)
class DemoDirs:
    media: Path
    data: Path
    zips: Path
    manifest: Path
    ids: Path
    images: Path

    def all(self) -> list[Path]:
        return [self.media, self.data, self.zips, self.manifest, self.ids, self.images]


def dirs_of(settings: Settings, images: Path) -> DemoDirs:
    if settings.zips_dir is None or settings.manifest_path is None or settings.jellyfin_ids_file is None:
        raise NotDemo("el demo necesita MM_ZIPS_DIR, MM_MANIFEST_PATH y MM_JELLYFIN_IDS_FILE")
    return DemoDirs(settings.media_base, settings.data_dir, settings.zips_dir,
                    settings.manifest_path.parent, settings.jellyfin_ids_file.parent, images)


def check(dirs: DemoDirs) -> None:
    for d in dirs.all():
        if d.is_dir() and any(d.iterdir()) and not (d / MARKER).is_file():
            raise NotDemo(f"{d} tiene datos y no es un directorio de demo (falta {MARKER}): "
                          "no se siembra nada")


def ids_export(root: Root) -> dict[str, str]:
    """Lo que devolvería el export del host: ruta en Jellyfin → ID, con el mismo
    cálculo que usa la app para vigilar el algoritmo, para que no avise en falso."""
    assert root.jellyfin_path is not None
    items: dict[str, str] = {}
    for entry in walk(root):
        kind = manifest_kind(entry.name)
        if kind is None or "/" in entry.relpath or entry.name in NOT_INDEXED:
            continue
        path = f"{root.jellyfin_path}/{entry.relpath}"
        item_id = compute_item_id(kind, path)
        assert item_id is not None
        items[path] = item_id
    return items


def seed(settings: Settings, images: Path) -> bool:
    """Siembra y devuelve True, o False si ya estaba sembrado: lo que se haya probado
    (enlaces, papelera, renombrados) sobrevive a los reinicios."""
    dirs = dirs_of(settings, images)
    check(dirs)
    roots = load_roots(settings)
    if (settings.media_base / MARKER).is_file() and settings.sentinel.is_file():
        return False
    for d in dirs.all():
        d.mkdir(parents=True, exist_ok=True)
        (d / MARKER).touch()

    for src in sorted(p for p in FIXTURES.iterdir() if p.is_dir() and p.name != "thumbs"):
        root = roots.get(src.name)
        if root is None:
            raise NotDemo(f"los fixtures traen la raíz {src.name} y roots.toml no la declara")
        shutil.copytree(src, root.path, dirs_exist_ok=True)
        for f in root.path.rglob("*"):
            os.utime(f, (COPIED_AT, COPIED_AT))
    settings.trash_dir.mkdir(exist_ok=True)
    settings.sentinel.touch()
    (dirs.zips / ZIPS_MARKER).touch()
    for sub in ("main", "public", "cache"):
        (dirs.data / sub).mkdir(exist_ok=True)

    items: dict[str, str] = {}
    for root in roots.values():
        if root.indexed_by_jellyfin:
            items |= ids_export(root)
    assert settings.jellyfin_ids_file is not None
    settings.jellyfin_ids_file.write_text(json.dumps({"generated_at": now_iso(), "items": items}, indent=1))
    for path, item_id in items.items():
        thumb = FIXTURES / "thumbs" / f"{Path(path).stem}.jpg"
        if thumb.is_file():
            shutil.copyfile(thumb, images / f"{item_id}.jpg")
    log.info("demo sembrado", extra={"fields": {"items": len(items)}})
    return True
