"""Papelera: la app mueve, nunca desenlaza.

Borrar es un rename a `<base>/.trash/<raíz>/<fecha>/<ruta>`: mismo sistema de ficheros
(instantáneo, no mueve bytes) y fuera de todas las raíces, así que Jellyfin deja de
verlo. El vaciado lo hace un timer del host, no este proceso: comprometer la app no
equivale a poder borrar la biblioteca."""
import ctypes
import errno
import os
from pathlib import Path, PurePosixPath

import aiosqlite

from media_management.db import now_iso, utcnow
from media_management.roots import PathRejected, Root, resolve_in_root
from media_management.settings import Settings

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_libc = ctypes.CDLL(None, use_errno=True)


class MoveError(Exception):
    pass


def rename_noreplace(src: Path, dst: Path) -> None:
    """rename que falla si el destino existe. Un rename normal lo sobrescribiría, y
    sobrescribir es borrar."""
    r = _libc.renameat2(_AT_FDCWD, os.fsencode(src), _AT_FDCWD, os.fsencode(dst), _RENAME_NOREPLACE)
    if r != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(dst))


def exact_file(root: Root, relpath: str) -> Path:
    """La ruta del fichero tal cual, sin symlinks en ningún componente: lo que se mueve
    es exactamente el fichero del catálogo, no aquello a lo que apunte un enlace."""
    real = resolve_in_root(root, relpath)
    literal = Path(os.path.realpath(root.path)) / PurePosixPath(relpath)
    if real != literal or literal.is_symlink() or not literal.is_file():
        raise PathRejected(relpath)
    return literal


def valid_new_name(name: str) -> str | None:
    name = name.strip()
    if (not name or name in (".", "..") or name.startswith(".") or "/" in name or "\x00" in name
            or any(not c.isprintable() for c in name) or len(name.encode()) > 255):
        return None
    return name


def _free_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = os.path.splitext(path.name)
    n = 2
    while True:
        candidate = path.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _move(src: Path, dst: Path) -> None:
    try:
        rename_noreplace(src, dst)
    except OSError as e:
        if e.errno == errno.EXDEV:
            raise MoveError("la papelera no está en el mismo sistema de ficheros: no se copia nada") from e
        if e.errno == errno.EEXIST:
            raise MoveError("ya existe un fichero con ese nombre en el destino") from e
        raise MoveError(f"no se pudo mover: {e.strerror}") from e


async def move_to_trash(conn: aiosqlite.Connection, settings: Settings, root: Root,
                        file_row: aiosqlite.Row, user: str) -> str:
    src = exact_file(root, file_row["relpath"])
    day = utcnow().strftime("%Y-%m-%d")
    dst = _free_destination(settings.trash_dir / root.name / day / PurePosixPath(file_row["relpath"]))
    dst.parent.mkdir(parents=True, exist_ok=True)
    _move(src, dst)
    trash_rel = str(dst.relative_to(settings.trash_dir))
    await conn.execute("UPDATE files SET present = 0 WHERE id = ?", (file_row["id"],))
    await conn.execute(
        "INSERT INTO trash_entries (file_id, root, relpath, trash_relpath, size_bytes, trashed_at, trashed_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (file_row["id"], root.name, file_row["relpath"], trash_rel, file_row["size_bytes"], now_iso(), user))
    return trash_rel


async def restore(conn: aiosqlite.Connection, settings: Settings, root: Root, entry: aiosqlite.Row) -> None:
    """Devuelve el fichero a su sitio si allí no hay ya otro con el mismo nombre."""
    src = settings.trash_dir / PurePosixPath(entry["trash_relpath"])
    real_trash = Path(os.path.realpath(settings.trash_dir))
    if src.is_symlink() or not src.is_file() or not Path(os.path.realpath(src)).is_relative_to(real_trash):
        raise MoveError("el fichero ya no está en la papelera")
    rel = PurePosixPath(entry["relpath"])
    base = Path(os.path.realpath(root.path))
    dst = base / rel
    # Una carpeta intermedia puede haberse cambiado por un symlink que saque de la raíz.
    existing = dst.parent
    while not existing.exists():
        existing = existing.parent
    if not Path(os.path.realpath(existing)).is_relative_to(base):
        raise MoveError("la carpeta de destino ya no está dentro de la raíz")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if Path(os.path.realpath(dst.parent)) != dst.parent:
        raise MoveError("la carpeta de destino ya no está dentro de la raíz")
    _move(src, dst)
    await conn.execute("UPDATE trash_entries SET restored_at = ? WHERE id = ?", (now_iso(), entry["id"]))
    if entry["file_id"] is not None:
        await conn.execute("UPDATE files SET present = 1 WHERE id = ?", (entry["file_id"],))


async def rename_file(conn: aiosqlite.Connection, root: Root, file_row: aiosqlite.Row, new_name: str) -> str:
    src = exact_file(root, file_row["relpath"])
    dst = src.with_name(new_name)
    _move(src, dst)
    parent = PurePosixPath(file_row["relpath"]).parent
    new_rel = str(parent / new_name) if str(parent) != "." else new_name
    await conn.execute("UPDATE files SET relpath = ?, name = ? WHERE id = ?", (new_rel, new_name, file_row["id"]))
    return new_rel


async def reconcile_trash(conn: aiosqlite.Connection, settings: Settings) -> None:
    """Marca como vaciado lo que el timer del host ya ha borrado de la papelera."""
    async with conn.execute("SELECT id, trash_relpath FROM trash_entries "
                            "WHERE restored_at IS NULL AND purged_at IS NULL") as cur:
        rows = await cur.fetchall()
    for row in rows:
        if not (settings.trash_dir / PurePosixPath(row["trash_relpath"])).exists():
            await conn.execute("UPDATE trash_entries SET purged_at = ? WHERE id = ?", (now_iso(), row["id"]))
