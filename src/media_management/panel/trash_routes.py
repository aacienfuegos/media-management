from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from media_management.logs import audit
from media_management.panel.deps import CsrfUser, MainDb, User, client_ip, render, roots_of, settings_of
from media_management.panel.policy import delete_blockers, rename_allowed
from media_management.roots import PathRejected, Root, media_problem
from media_management.trash import MoveError, move_to_trash, rename_file, restore, valid_new_name

router = APIRouter()


async def _file(request: Request, conn: aiosqlite.Connection, file_id: int) -> tuple[aiosqlite.Row, Root]:
    async with conn.execute("SELECT * FROM files WHERE id = ? AND present = 1", (file_id,)) as cur:
        f = await cur.fetchone()
    root = roots_of(request).get(f["root"]) if f is not None else None
    if f is None or root is None:
        raise HTTPException(404)
    return f, root


def _require_media(request: Request) -> None:
    problem = media_problem(settings_of(request), roots_of(request))
    if problem is not None:
        raise HTTPException(503, problem)


@router.get("/files/{file_id}/trash")
async def trash_confirm(request: Request, file_id: int, user: User, conn: MainDb) -> Response:
    f, root = await _file(request, conn, file_id)
    return render(request, user, "trash_confirm.html", f=f, root=root, error=None,
                  blockers=await delete_blockers(conn, root, f))


@router.post("/files/{file_id}/trash")
async def trash_file(request: Request, file_id: int, user: CsrfUser, conn: MainDb) -> Response:
    _require_media(request)
    f, root = await _file(request, conn, file_id)
    blockers = await delete_blockers(conn, root, f)
    if blockers:
        await audit(conn, user, "file_trash", "denied", target=f"{root.name}/{f['relpath']}",
                    ip=client_ip(request), reasons=blockers)
        await conn.commit()
        return render(request, user, "trash_confirm.html", status_code=409, f=f, root=root,
                      blockers=blockers, error=None)
    try:
        trash_rel = await move_to_trash(conn, settings_of(request), root, f, user)
    except (MoveError, PathRejected) as e:
        await audit(conn, user, "file_trash", "error", target=f"{root.name}/{f['relpath']}",
                    ip=client_ip(request), error=str(e))
        await conn.commit()
        return render(request, user, "trash_confirm.html", status_code=409, f=f, root=root, blockers=[],
                      error=str(e) if isinstance(e, MoveError) else "el fichero ya no es el del catálogo")
    await audit(conn, user, "file_trash", "ok", target=f"{root.name}/{f['relpath']}", ip=client_ip(request),
                file_id=file_id, trash=trash_rel)
    await conn.commit()
    return RedirectResponse("/trash", status_code=303)


@router.get("/files/{file_id}/rename")
async def rename_form(request: Request, file_id: int, user: User, conn: MainDb) -> Response:
    f, root = await _file(request, conn, file_id)
    if not rename_allowed(root):
        raise HTTPException(403, "esta raíz no permite renombrar")
    return render(request, user, "rename.html", f=f, root=root, error=None)


@router.post("/files/{file_id}/rename")
async def rename(request: Request, file_id: int, user: CsrfUser, conn: MainDb,
                 new_name: Annotated[str, Form(max_length=300)] = "") -> Response:
    """Renombrar solo existe en raíces que lo permiten. En las sincronizadas el nombre
    es la identidad del clip: renombrarlo lo duplicaría en la siguiente pasada."""
    f, root = await _file(request, conn, file_id)
    if not rename_allowed(root):
        await audit(conn, user, "file_rename", "denied", target=f"{root.name}/{f['relpath']}", ip=client_ip(request))
        await conn.commit()
        raise HTTPException(403, "esta raíz no permite renombrar")
    _require_media(request)
    name = valid_new_name(new_name)
    if name is None:
        return render(request, user, "rename.html", status_code=400, f=f, root=root,
                      error="Nombre no válido: sin barras, sin empezar por punto y sin caracteres de control.")
    try:
        new_rel = await rename_file(conn, root, f, name)
    except (MoveError, PathRejected) as e:
        return render(request, user, "rename.html", status_code=409, f=f, root=root,
                      error=str(e) if isinstance(e, MoveError) else "el fichero ya no es el del catálogo")
    await audit(conn, user, "file_rename", "ok", target=f"{root.name}/{f['relpath']}", ip=client_ip(request),
                file_id=file_id, new=new_rel)
    await conn.commit()
    return RedirectResponse(f"/files/{file_id}", status_code=303)


@router.get("/trash")
async def trash_view(request: Request, user: User, conn: MainDb) -> Response:
    async with conn.execute("SELECT * FROM trash_entries ORDER BY trashed_at DESC LIMIT 500") as cur:
        entries = await cur.fetchall()
    return render(request, user, "trash.html", entries=entries)


@router.post("/trash/{entry_id}/restore")
async def restore_entry(request: Request, entry_id: int, user: CsrfUser, conn: MainDb) -> Response:
    _require_media(request)
    async with conn.execute("SELECT * FROM trash_entries WHERE id = ? AND restored_at IS NULL "
                            "AND purged_at IS NULL", (entry_id,)) as cur:
        entry = await cur.fetchone()
    root = roots_of(request).get(entry["root"]) if entry is not None else None
    if entry is None or root is None:
        raise HTTPException(404)
    try:
        await restore(conn, settings_of(request), root, entry)
    except MoveError as e:
        await audit(conn, user, "file_restore", "error", target=f"{root.name}/{entry['relpath']}",
                    ip=client_ip(request), error=str(e))
        await conn.commit()
        async with conn.execute("SELECT * FROM trash_entries ORDER BY trashed_at DESC LIMIT 500") as cur:
            entries = await cur.fetchall()
        return render(request, user, "trash.html", status_code=409, entries=entries,
                      error=f"No se ha podido restaurar {entry['relpath']}: {e}.")
    await audit(conn, user, "file_restore", "ok", target=f"{root.name}/{entry['relpath']}", ip=client_ip(request),
                entry_id=entry_id)
    await conn.commit()
    return RedirectResponse("/trash", status_code=303)
