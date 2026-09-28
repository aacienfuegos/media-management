import json
from pathlib import PurePosixPath
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from media_management.db import get_state, now_iso
from media_management.jellyfin import Jellyfin
from media_management.panel.deps import MainDb, User, render, roots_of, settings_of
from media_management.panel.policy import delete_blockers
from media_management.thumbs import get_thumb, thumb_item_id

router = APIRouter()
PAGE = 200
SORTS = {"name": "relpath COLLATE NOCASE", "root": "root", "kind": "kind", "size": "size_bytes",
         "date": "date_utc", "duration": "duration_s", "resolution": "width * height"}
ICONS = {"video": "vídeo", "photo": "foto", "other": "fichero"}


def _subdirs(relpaths: list[str], current: str) -> list[str]:
    prefix = f"{current}/" if current else ""
    out = set()
    for rel in relpaths:
        if rel.startswith(prefix):
            rest = rel[len(prefix):]
            if "/" in rest:
                out.add(prefix + rest.split("/", 1)[0])
    return sorted(out, key=str.lower)


@router.get("/library")
async def library(request: Request, user: User, conn: MainDb,
                  root: str = "", dir: str = "", q: str = Query("", max_length=200),
                  kind: Literal["", "video", "photo", "other"] = "",
                  sort: Literal["name", "root", "kind", "size", "date", "duration", "resolution"] = "date",
                  order: Literal["asc", "desc"] = "desc", page: int = Query(1, ge=1)) -> Response:
    roots = roots_of(request)
    if root and root not in roots:
        raise HTTPException(404)
    where = ["present = 1"]
    params: list[Any] = []
    subdirs: list[str] = []
    crumbs = []
    current = ""
    if root:
        # `dir` solo filtra filas de la BD por prefijo de relpath; nunca toca el disco.
        current = dir.strip("/")
        async with conn.execute("SELECT relpath FROM files WHERE root = ? AND present = 1", (root,)) as cur:
            subdirs = _subdirs([r[0] for r in await cur.fetchall()], current)
        where.append("root = ?")
        params.append(root)
        if current:
            where.append("substr(relpath, 1, ?) = ? AND instr(substr(relpath, ? + 1), '/') = 0")
            params += [len(current) + 1, current + "/", len(current) + 1]
        else:
            where.append("instr(relpath, '/') = 0")
        acc: list[str] = []
        for part in PurePosixPath(current).parts if current else []:
            acc.append(part)
            crumbs.append(("/".join(acc), part))
    else:
        where.append(f"root IN ({', '.join('?' * len(roots))})")
        params += list(roots)
    if q:
        where.append("instr(lower(name), lower(?)) > 0")
        params.append(q)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    clause = " AND ".join(where)
    async with conn.execute(f"SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM files WHERE {clause}",
                            params) as cur:
        row = await cur.fetchone()
    total, total_bytes = (row[0], row[1]) if row else (0, 0)
    direction = "DESC" if order == "desc" else "ASC"
    async with conn.execute(
            f"SELECT * FROM files WHERE {clause} ORDER BY {SORTS[sort]} {direction}, root, relpath "
            "LIMIT ? OFFSET ?", (*params, PAGE, (page - 1) * PAGE)) as cur:
        files = await cur.fetchall()
    shown = [roots[root]] if root else list(roots.values())
    return render(request, user, "library.html", root=roots.get(root), files=files, subdirs=subdirs,
                  current=current, crumbs=crumbs, q=q, kind=kind, sort=sort, order=order,
                  page=page, pages=max(1, (total + PAGE - 1) // PAGE), total=total,
                  total_bytes=total_bytes, icons=ICONS,
                  shareable=any(r.shareable for r in shown),
                  jellyfin=any(r.indexed_by_jellyfin for r in shown))


@router.get("/files/{file_id}")
async def file_view(request: Request, file_id: int, user: User, conn: MainDb) -> Response:
    async with conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)) as cur:
        f = await cur.fetchone()
    if f is None:
        raise HTTPException(404)
    root = roots_of(request).get(f["root"])
    if root is None:
        raise HTTPException(404)
    async with conn.execute(
            "SELECT l.* FROM links l JOIN link_files lf ON lf.link_id = l.id WHERE lf.file_id = ? "
            "ORDER BY l.created_at DESC", (file_id,)) as cur:
        links = await cur.fetchall()
    blockers = await delete_blockers(conn, root, f) if f["present"] else []
    return render(request, user, "file.html", f=f, root=root, links=links, now=now_iso(),
                  blockers=blockers, icons=ICONS)


PLACEHOLDER = """<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">
<title>{label}</title>
<rect width="320" height="180" fill="#8b93a3" fill-opacity="0.18"/>
<path transform="translate(124 54) scale(3)" fill="#8b93a3" fill-rule="evenodd" d="{path}"/>
</svg>"""

ICON_PATHS = {
    "video": "M4 5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-2.5l5 3.5V6l-5 3.5V7a2 2 0 0 0-2-2H4Z",
    "photo": "M4 4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2H4Zm4 3.5a2 2 0 1 1 0 4 2 2 0 0 1 0-4ZM4 "
             "18l5-6 3.5 4 2.5-3 5 5H4Z",
    "other": "M6 2h8l6 6v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Zm7 1.5V9h5.5L13 3.5Z",
}


@router.get("/thumb/{file_id}")
async def thumb(request: Request, file_id: int, user: User, conn: MainDb) -> Response:
    async with conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)) as cur:
        f = await cur.fetchone()
    if f is None:
        raise HTTPException(404)
    settings = settings_of(request)
    item_id = await thumb_item_id(conn, roots_of(request), f)
    if item_id is not None:
        data = await get_thumb(settings.cache_dir, Jellyfin(settings.jellyfin_url, settings.jellyfin_timeout_s),
                               item_id)
        if data is not None:
            return Response(data, media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=86400"})
    label = ICONS.get(f["kind"], "fichero") if item_id is None else "sin miniatura"
    svg = PLACEHOLDER.format(label=label, path=ICON_PATHS.get(f["kind"], ICON_PATHS["other"]))
    return Response(svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "private, max-age=300"})


@router.get("/manifest")
async def manifest_view(request: Request, user: User, conn: MainDb) -> Response:
    text = await get_state(conn, "manifest_doc")
    status = await get_state(conn, "manifest_status")
    doc = json.loads(text) if text else None
    return render(request, user, "manifest.html", doc=doc,
                  status=json.loads(status) if status else None)


@router.get("/manifest/compare")
async def compare_view(request: Request, user: User, conn: MainDb) -> Response:
    comparison = await get_state(conn, "manifest_comparison")
    return render(request, user, "compare.html",
                  comparison=json.loads(comparison) if comparison else None,
                  configured=settings_of(request).manifest_compare_with is not None)
