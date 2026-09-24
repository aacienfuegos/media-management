import datetime
import re
from dataclasses import dataclass

import aiosqlite

from media_management.db import iso, now_iso, utcnow

TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{20,100}$")
MODES = ("open", "password", "request")


@dataclass(frozen=True)
class LinkFile:
    id: int
    file_id: int
    name: str
    root: str
    relpath: str
    kind: str
    size_bytes: int
    present: bool
    thumb_jpeg: bytes | None


def expiry(days: int, max_days: int) -> str:
    return iso(utcnow() + datetime.timedelta(days=max(1, min(days, max_days))))


def is_active(link: aiosqlite.Row, now: str | None = None) -> bool:
    return link["revoked_at"] is None and link["expires_at"] > (now or now_iso())


async def active_link_by_hash(conn: aiosqlite.Connection, token_hash: str) -> aiosqlite.Row | None:
    """El enlace si existe, no está revocado y no ha caducado. Hacia fuera, cualquiera
    de esos casos es el mismo "no existe"."""
    async with conn.execute("SELECT * FROM links WHERE token_hash = ?", (token_hash,)) as cur:
        link = await cur.fetchone()
    if link is None or not is_active(link):
        return None
    return link


async def link_by_id(conn: aiosqlite.Connection, link_id: int) -> aiosqlite.Row | None:
    async with conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)) as cur:
        return await cur.fetchone()


async def link_files(conn: aiosqlite.Connection, link_id: int) -> list[LinkFile]:
    async with conn.execute(
            "SELECT lf.id, lf.file_id, lf.thumb_jpeg, f.name, f.root, f.relpath, f.kind, f.size_bytes, "
            "f.present FROM link_files lf JOIN files f ON f.id = lf.file_id "
            "WHERE lf.link_id = ? ORDER BY lf.position", (link_id,)) as cur:
        rows = await cur.fetchall()
    return [LinkFile(id=r["id"], file_id=r["file_id"], name=r["name"], root=r["root"],
                     relpath=r["relpath"], kind=r["kind"], size_bytes=r["size_bytes"],
                     present=bool(r["present"]), thumb_jpeg=r["thumb_jpeg"]) for r in rows]


async def link_file(conn: aiosqlite.Connection, link_id: int, link_file_id: int) -> LinkFile | None:
    for lf in await link_files(conn, link_id):
        if lf.id == link_file_id:
            return lf
    return None


def strip_jpeg_metadata(data: bytes) -> bytes | None:
    """Quita los segmentos APPn (salvo JFIF) y COM de un JPEG. Una miniatura que sale a
    internet no lleva nada del servidor que la generó; si no se entiende, no sale."""
    if data[:2] != b"\xff\xd8":
        return None
    out = bytearray(b"\xff\xd8")
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker == 0xDA:
            out += data[i:]
            return bytes(out)
        length = int.from_bytes(data[i + 2:i + 4], "big")
        segment = data[i:i + 2 + length]
        if not (0xE1 <= marker <= 0xEF or marker == 0xFE):
            out += segment
        i += 2 + length
    return None
