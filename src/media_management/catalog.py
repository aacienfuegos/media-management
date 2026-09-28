import asyncio
import datetime
import os
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from media_management.db import now_iso
from media_management.media_meta import FileMeta, day_offsets, effective_date, extract
from media_management.roots import Root, kind_of


@dataclass(frozen=True)
class DiskEntry:
    relpath: str
    name: str
    size: int
    mtime_ns: int


@dataclass
class RootScan:
    root: str
    files: int = 0
    new: int = 0
    gone: int = 0
    meta_errors: int = 0
    metas: dict[str, FileMeta] = field(default_factory=dict)


def walk(root: Root) -> list[DiskEntry]:
    """Ficheros regulares de la raíz. No sigue symlinks ni entra en nada que empiece
    por punto: lo que la app ve es exactamente lo que está dentro de la raíz."""
    out: list[DiskEntry] = []
    stack = [""]
    while stack:
        rel_dir = stack.pop()
        with os.scandir(os.path.join(root.path, rel_dir)) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(rel)
                elif entry.is_file(follow_symlinks=False):
                    st = entry.stat(follow_symlinks=False)
                    out.append(DiskEntry(rel, entry.name, st.st_size, st.st_mtime_ns))
    return out


def meta_from_row(row: aiosqlite.Row) -> FileMeta:
    local = row["captured_at_local"]
    return FileMeta(
        name=row["name"], kind=row["kind"], size_bytes=row["size_bytes"], mtime_ns=row["mtime_ns"],
        captured_at_utc=row["captured_at_utc"],
        captured_at_local=datetime.datetime.fromisoformat(local) if local else None,
        captured_at_source=row["captured_at_source"], duration_s=row["duration_s"],
        width=row["width"], height=row["height"], meta_error=row["meta_error"])


def jellyfin_path_of(root: Root, relpath: str) -> str | None:
    return f"{root.jellyfin_path}/{relpath}" if root.jellyfin_path else None


async def scan_root(conn: aiosqlite.Connection, root: Root, ids: dict[str, str] | None) -> RootScan:
    result = RootScan(root=root.name)
    entries = await asyncio.to_thread(walk, root)
    async with conn.execute("SELECT * FROM files WHERE root = ?", (root.name,)) as cur:
        existing = {row["relpath"]: row for row in await cur.fetchall()}

    def read_changed() -> dict[str, FileMeta]:
        out: dict[str, FileMeta] = {}
        for e in entries:
            row = existing.get(e.relpath)
            if row is not None and row["size_bytes"] == e.size and row["mtime_ns"] == e.mtime_ns:
                out[e.relpath] = meta_from_row(row)
            else:
                out[e.relpath] = extract(os.path.join(root.path, e.relpath), e.name,
                                         kind_of(e.name), root.metadata_profile, e.size, e.mtime_ns)
        return out

    metas = await asyncio.to_thread(read_changed)
    offsets = day_offsets(list(metas.values())) if root.metadata_profile == "dji" else {}
    now = now_iso()
    for e in entries:
        m = metas[e.relpath]
        date_utc, date_source = effective_date(m, offsets)
        row = existing.get(e.relpath)
        jf_path = jellyfin_path_of(root, e.relpath)
        if root.indexed_by_jellyfin and ids is not None and jf_path is not None:
            item_id = ids.get(jf_path)
        else:
            item_id = row["jellyfin_item_id"] if row is not None and root.indexed_by_jellyfin else None
        values: dict[str, Any] = {
            "name": e.name, "kind": m.kind, "size_bytes": e.size, "mtime_ns": e.mtime_ns,
            "captured_at_utc": m.captured_at_utc,
            "captured_at_local": m.captured_at_local.isoformat() if m.captured_at_local else None,
            "captured_at_source": m.captured_at_source, "date_utc": date_utc,
            "date_source": date_source, "duration_s": m.duration_s, "width": m.width,
            "height": m.height, "meta_error": m.meta_error, "jellyfin_item_id": item_id,
            "last_seen": now,
        }
        if row is None or not row["present"]:
            result.new += 1
        if row is None:
            cols = ", ".join(["root", "relpath", "first_seen", *values])
            marks = ", ".join("?" * (len(values) + 3))
            await conn.execute(f"INSERT INTO files ({cols}) VALUES ({marks})",
                               (root.name, e.relpath, now, *values.values()))
        else:
            sets = ", ".join(f"{k} = ?" for k in values)
            await conn.execute(f"UPDATE files SET {sets}, present = 1 WHERE id = ?",
                               (*values.values(), row["id"]))
        result.meta_errors += m.meta_error is not None
    seen = {e.relpath for e in entries}
    for relpath, row in existing.items():
        if relpath not in seen and row["present"]:
            await conn.execute("UPDATE files SET present = 0 WHERE id = ?", (row["id"],))
            result.gone += 1
    result.files = len(entries)
    result.metas = metas
    return result
