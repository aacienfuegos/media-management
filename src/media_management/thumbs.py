import logging
import os
import re
from pathlib import Path

import aiosqlite

from media_management.jellyfin import Jellyfin, JellyfinError
from media_management.roots import Root

log = logging.getLogger(__name__)
ITEM_ID = re.compile(r"^[0-9a-f]{32}$")


async def thumb_item_id(conn: aiosqlite.Connection, roots: dict[str, Root],
                        file_row: aiosqlite.Row) -> str | None:
    """ID de Jellyfin cuya miniatura representa al fichero: la suya, o la del fichero
    con el mismo nombre en la raíz de la que toma prestadas las miniaturas (el master a
    120 fps usa la de su conversión a 60, que es el mismo plano)."""
    if file_row["jellyfin_item_id"]:
        return str(file_row["jellyfin_item_id"])
    root = roots.get(file_row["root"])
    if root is None or root.thumbnail_from is None:
        return None
    async with conn.execute(
            "SELECT jellyfin_item_id FROM files WHERE root = ? AND relpath = ? AND present = 1",
            (root.thumbnail_from, file_row["relpath"])) as cur:
        row = await cur.fetchone()
    return str(row[0]) if row and row[0] else None


def cache_path(cache_dir: Path, item_id: str) -> Path:
    if not ITEM_ID.match(item_id):
        raise ValueError("ID de Jellyfin con formato inesperado")
    return cache_dir / "thumbs" / f"{item_id}.jpg"


def cached(cache_dir: Path, item_id: str) -> bytes | None:
    try:
        return cache_path(cache_dir, item_id).read_bytes()
    except (FileNotFoundError, ValueError):
        return None


async def get_thumb(cache_dir: Path, jellyfin: Jellyfin, item_id: str) -> bytes | None:
    """Miniatura cacheada en disco. Un fallo de Jellyfin no se cachea: la siguiente
    petición lo vuelve a intentar."""
    hit = cached(cache_dir, item_id)
    if hit is not None:
        return hit
    try:
        path = cache_path(cache_dir, item_id)
    except ValueError:
        return None
    try:
        data = await jellyfin.primary_image(item_id)
    except JellyfinError as e:
        log.warning("miniatura no disponible", extra={"fields": {"item_id": item_id, "error": str(e)}})
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return data
