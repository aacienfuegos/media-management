import hashlib
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from media_management.api.deps import MainDb, TokenId
from media_management.db import get_state
from media_management.manifest import Manifest
from media_management.roots import Root

router = APIRouter(prefix="/api/v1")


class RootOut(BaseModel):
    name: str
    shareable: bool
    deletable: bool
    renamable: bool
    synced: bool
    requires_second_copy: bool
    indexed_by_jellyfin: bool
    in_manifest: bool
    metadata_profile: str
    files: int
    bytes: int


class FileOut(BaseModel):
    id: int
    root: str
    relpath: str
    name: str
    kind: Literal["video", "photo", "other"]
    size_bytes: int
    captured_at_utc: str | None
    captured_at_source: str | None
    duration_s: float | None
    width: int | None
    height: int | None
    jellyfin_item_id: str | None
    first_seen: str
    last_seen: str


class FilePage(BaseModel):
    items: list[FileOut]
    total: int
    page: int
    page_size: int


FILE_COLUMNS = ("id, root, relpath, name, kind, size_bytes, date_utc AS captured_at_utc, "
                "date_source AS captured_at_source, duration_s, width, height, "
                "jellyfin_item_id, first_seen, last_seen")


def _roots(request: Request) -> dict[str, Root]:
    roots: dict[str, Root] = request.app.state.roots
    return roots


@router.get("/roots", response_model=list[RootOut])
async def list_roots(request: Request, _token: TokenId, conn: MainDb) -> list[RootOut]:
    stats: dict[str, tuple[int, int]] = {}
    async with conn.execute("SELECT root, COUNT(*), COALESCE(SUM(size_bytes), 0) FROM files "
                            "WHERE present = 1 GROUP BY root") as cur:
        for root, n, total in await cur.fetchall():
            stats[root] = (n, total)
    return [RootOut(**r.model_dump(include=set(RootOut.model_fields) - {"files", "bytes"}),
                    files=stats.get(r.name, (0, 0))[0], bytes=stats.get(r.name, (0, 0))[1])
            for r in _roots(request).values()]


@router.get("/roots/{root}/files", response_model=FilePage)
async def list_files(request: Request, root: str, _token: TokenId, conn: MainDb,
                     page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=500),
                     kind: Literal["video", "photo", "other"] | None = None) -> FilePage:
    if root not in _roots(request):
        raise HTTPException(404, "raíz desconocida")
    where = "root = ? AND present = 1" + (" AND kind = ?" if kind else "")
    params: tuple[str, ...] = (root, kind) if kind else (root,)
    async with conn.execute(f"SELECT COUNT(*) FROM files WHERE {where}", params) as cur:
        row = await cur.fetchone()
    total = row[0] if row else 0
    async with conn.execute(
            f"SELECT {FILE_COLUMNS} FROM files WHERE {where} ORDER BY relpath LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size)) as cur:
        items = [FileOut(**dict(r)) for r in await cur.fetchall()]
    return FilePage(items=items, total=total, page=page, page_size=page_size)


@router.get("/files/{file_id}", response_model=FileOut)
async def file_detail(file_id: int, _token: TokenId, conn: MainDb) -> FileOut:
    async with conn.execute(f"SELECT {FILE_COLUMNS} FROM files WHERE id = ? AND present = 1",
                            (file_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, "fichero desconocido")
    return FileOut(**dict(row))


@router.get("/manifest", response_model=Manifest,
            responses={304: {"description": "sin cambios desde el ETag dado"},
                       503: {"description": "todavía no hay manifiesto"}})
async def manifest(request: Request, _token: TokenId, conn: MainDb) -> Response:
    """El mismo documento que el fichero del manifiesto, byte a byte. Con
    `If-None-Match` se puede preguntar barato si ha cambiado."""
    text = await get_state(conn, "manifest_doc")
    if text is None:
        raise HTTPException(503, "todavía no hay manifiesto")
    etag = '"' + hashlib.sha256(text.encode()).hexdigest()[:32] + '"'
    if etag in [t.strip() for t in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(text, media_type="application/json", headers={"ETag": etag})
