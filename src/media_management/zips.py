"""Zip con todos los ficheros de un enlace: lo genera el worker y lo sirve nginx.

El worker es el único proceso con escritura en `zips_dir`. Cada pocos segundos compara
lo que hay con lo que debería haber: un enlace activo con el zip activado y al menos
dos ficheros presentes tiene un zip con exactamente esos ficheros, y todo lo demás se
borra. Así revocar, caducar, renovar, mandar a la papelera, restaurar o renombrar no
dependen de que llegue ningún aviso. El panel, además, anula el zip en el momento en
que cambia un fichero, para que no se sirva el viejo mientras se rehace.

Sin compresión: vídeo y fotos ya van comprimidos. Se escribe a un fichero y no en
streaming, así que cada entrada lleva sus tamaños en la cabecera y ZIP64 solo donde
hace falta, que es lo que mejor abren los descompresores de los sistemas.

Los zips son datos derivados y el worker sí los borra (la biblioteca nunca), pero solo
dentro de `zips_dir` y solo nombres con la forma de los suyos."""
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import aiosqlite

from media_management.db import now_iso, open_persistent, parse_iso
from media_management.links import is_active, link_files, servable_path
from media_management.logs import audit
from media_management.roots import Root, media_problem
from media_management.settings import Settings

log = logging.getLogger(__name__)
MIN_FILES = 2
INTERVAL_S = 5
MARKER = ".zips-root"
NOT_MOUNTED = "almacén de ZIP no montado"
OURS = re.compile(r"^(\d+-\d+\.zip|\.\d+-\d+\.zip\.\d+\.tmp)$")
GB = 1024**3

State = Literal["ready", "building", "pending", "skipped", "failed"]


@dataclass(frozen=True)
class ZipStatus:
    state: State
    version: int
    size_bytes: int | None
    built_at: str | None
    problem: str | None
    entries: list[str]


def zip_name(link_id: int, version: int) -> str:
    return f"{link_id}-{version}.zip"


def entry_name(name: str) -> str:
    """Nombre de una entrada: sin rutas ni caracteres de control, para que al
    descomprimir no pueda salir del directorio de destino (zip slip)."""
    clean = "".join("_" if c in "/\\" else c for c in name if c.isprintable()).strip().lstrip(".")
    return clean or "fichero"


def unique_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for name in map(entry_name, names):
        stem, dot, ext = name.rpartition(".") if "." in name else (name, "", "")
        candidate, n = name, 1
        while candidate.casefold() in seen:
            n += 1
            candidate = f"{stem} ({n}){dot}{ext}"
        seen.add(candidate.casefold())
        out.append(candidate)
    return out


def _state(row: aiosqlite.Row) -> State:
    if row["fingerprint"] is not None:
        return "ready"
    if row["building_at"] is not None:
        return "building"
    if row["problem"]:
        return "failed" if row["failures"] else "skipped"
    return "pending"


async def zip_status(conn: aiosqlite.Connection, link_id: int, present_files: int) -> ZipStatus | None:
    """Estado del zip de un enlace, o None si no lleva zip (desactivado o con menos de
    dos ficheros presentes)."""
    if present_files < MIN_FILES:
        return None
    async with conn.execute("SELECT * FROM link_zips WHERE link_id = ?", (link_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return ZipStatus(_state(row), row["version"], row["size_bytes"], row["built_at"], row["problem"],
                     json.loads(row["entries"] or "[]"))


async def invalidate_for_file(conn: aiosqlite.Connection, file_id: int) -> None:
    await conn.execute("UPDATE link_zips SET fingerprint = NULL WHERE link_id IN "
                       "(SELECT link_id FROM link_files WHERE file_id = ?)", (file_id,))


Entries = list[tuple[Path, str]]


async def _wanted(conn: aiosqlite.Connection, settings: Settings, roots: dict[str, Root],
                  link_id: int) -> tuple[Entries, str] | None:
    async with conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)) as cur:
        link = await cur.fetchone()
    if link is None or not is_active(link):
        return None
    files = [(lf, real) for lf in await link_files(conn, link_id)
             if (real := servable_path(roots, settings.media_base, lf)) is not None]
    if len(files) < MIN_FILES:
        return None
    entries = list(zip((real for _, real in files), unique_names([lf.name for lf, _ in files]), strict=True))
    stats = [(arc, str(real), (st := real.stat()).st_size, st.st_mtime_ns) for real, arc in entries]
    return entries, hashlib.sha256(json.dumps(stats).encode()).hexdigest()


def mounted(zdir: Path) -> bool:
    """El marcador vive en el disco de verdad. Sin él, `zips_dir` puede ser el
    directorio vacío sobre el que no se ha montado nada, y los gigas irían a parar al
    disco del sistema."""
    marker = zdir / MARKER
    return marker.is_file() and not marker.is_symlink()


def _sweep(zdir: Path, keep: set[str]) -> None:
    real_dir = Path(os.path.realpath(zdir))
    for entry in os.scandir(zdir):
        if not OURS.match(entry.name) or entry.name in keep:
            continue
        if entry.is_symlink() or Path(os.path.realpath(entry.path)).parent != real_dir:
            continue
        os.unlink(entry.path)


def _needed_bytes(entries: Entries) -> int:
    # Cabecera local + central + extras zip64, holgado; más el final del archivo.
    return sum(real.stat().st_size + 200 + 2 * len(arc.encode()) for real, arc in entries) + 200


def _write(zdir: Path, name: str, entries: Entries) -> int:
    tmp = zdir / f".{name}.{os.getpid()}.tmp"
    try:
        with open(tmp, "xb") as f:
            with zipfile.ZipFile(f, "w", zipfile.ZIP_STORED, allowZip64=True, strict_timestamps=False) as zf:
                for real, arc in entries:
                    zf.write(real, arc)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, zdir / name)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return (zdir / name).stat().st_size


async def _skip(conn: aiosqlite.Connection, row: aiosqlite.Row, problem: str | None) -> None:
    if row["problem"] != problem or row["failures"]:
        await conn.execute("UPDATE link_zips SET problem = ?, failures = 0 WHERE link_id = ?",
                           (problem, row["link_id"]))
        if problem:
            log.warning("zip sin generar", extra={"fields": {"link_id": row["link_id"], "problem": problem}})


async def reconcile_zips(conn: aiosqlite.Connection, settings: Settings, roots: dict[str, Root]) -> None:
    zdir = settings.zips_dir
    if zdir is None or media_problem(settings, roots) is not None:
        return
    async with conn.execute("SELECT * FROM link_zips ORDER BY link_id") as cur:
        rows = await cur.fetchall()
    if not mounted(zdir):
        await conn.execute("UPDATE link_zips SET fingerprint = NULL, problem = ?, failures = 0",
                           (NOT_MOUNTED,))
        await conn.commit()
        return
    keep: set[str] = set()
    todo: list[tuple[aiosqlite.Row, Entries, str]] = []
    for row in rows:
        wanted = await _wanted(conn, settings, roots, row["link_id"])
        name = zip_name(row["link_id"], row["version"])
        if wanted is not None and row["fingerprint"] == wanted[1] and (zdir / name).is_file():
            keep.add(name)
            continue
        if row["fingerprint"] is not None:
            await conn.execute("UPDATE link_zips SET fingerprint = NULL WHERE link_id = ?", (row["link_id"],))
        if wanted is None:
            await _skip(conn, row, None)
        else:
            todo.append((row, *wanted))
    await conn.commit()
    await asyncio.to_thread(_sweep, zdir, keep)
    for row, entries, fp in todo:
        if await _build(conn, settings, roots, zdir, row, entries, fp):
            break


def _backing_off(settings: Settings, row: aiosqlite.Row, fp: str) -> bool:
    if not row["failures"] or row["failed_fingerprint"] != fp:
        return False
    if row["failures"] >= settings.zip_max_attempts:
        return True
    wait = settings.zip_retry_s * 2 ** (row["failures"] - 1)
    return bool(time.time() - parse_iso(row["failed_at"]).timestamp() < wait)


async def _build(conn: aiosqlite.Connection, settings: Settings, roots: dict[str, Root], zdir: Path,
                 row: aiosqlite.Row, entries: Entries, fp: str) -> bool:
    """Genera un zip. Devuelve si lo ha intentado: uno por vuelta, para que un zip
    grande no retrase más de uno las anulaciones y borrados de los demás."""
    link_id = row["link_id"]
    if _backing_off(settings, row, fp):
        return False
    needed = await asyncio.to_thread(_needed_bytes, entries)
    free = (await asyncio.to_thread(shutil.disk_usage, zdir)).free
    problem = None
    if settings.zip_max_gb and needed > settings.zip_max_gb * GB:
        problem = f"ocuparía {needed / GB:.1f} GB, más que el máximo de {settings.zip_max_gb} GB"
    elif free - needed < settings.zip_min_free_gb * GB:
        problem = (f"sin espacio: ocuparía {needed / GB:.1f} GB y deben quedar "
                   f"{settings.zip_min_free_gb} GB libres")
    if problem is not None or not row["failures"]:
        await _skip(conn, row, problem)
        await conn.commit()
    if problem is not None:
        return False
    version = row["version"] + 1
    name = zip_name(link_id, version)
    await conn.execute("UPDATE link_zips SET building_at = ? WHERE link_id = ?", (now_iso(), link_id))
    await conn.commit()
    started = time.monotonic()
    try:
        size = await asyncio.to_thread(_write, zdir, name, entries)
    except OSError as e:
        failures = (row["failures"] if row["failed_fingerprint"] == fp else 0) + 1
        log.error("fallo al generar el zip", extra={"fields": {"link_id": link_id, "error": str(e),
                                                               "failures": failures}})
        await conn.execute(
            "UPDATE link_zips SET building_at = NULL, problem = ?, failures = ?, failed_fingerprint = ?, "
            "failed_at = ? WHERE link_id = ?",
            (f"error al generarlo: {e.strerror or type(e).__name__}", failures, fp, now_iso(), link_id))
        await conn.commit()
        return True
    await conn.execute("UPDATE link_zips SET building_at = NULL WHERE link_id = ?", (link_id,))
    again = await _wanted(conn, settings, roots, link_id)
    if again is None or again[1] != fp or not mounted(zdir):
        # El enlace ha cambiado mientras se generaba: no corresponde, se tira.
        await asyncio.to_thread((zdir / name).unlink, True)
        await conn.commit()
        return True
    cur = await conn.execute(
        "UPDATE link_zips SET version = ?, fingerprint = ?, size_bytes = ?, entries = ?, built_at = ?, "
        "problem = NULL, failures = 0, failed_fingerprint = NULL, failed_at = NULL WHERE link_id = ?",
        (version, fp, size, json.dumps([arc for _, arc in entries]), now_iso(), link_id))
    if cur.rowcount:
        await audit(conn, "worker", "zip_built", "ok", link_id=link_id, files=len(entries), size=size,
                    seconds=round(time.monotonic() - started, 1))
    await conn.commit()
    return True


async def zip_loop(settings: Settings, roots: dict[str, Root]) -> None:
    if settings.zips_dir is None:
        return
    conn = await open_persistent(settings.main_db)
    try:
        # Un worker que murió a mitad deja la marca y quizá un temporal; la marca se
        # quita aquí y el temporal lo borra la primera limpieza.
        await conn.execute("UPDATE link_zips SET building_at = NULL")
        await conn.commit()
        was_mounted = True
        while True:
            try:
                now_mounted = mounted(settings.zips_dir)
                if now_mounted != was_mounted and not now_mounted:
                    log.error("falta el marcador del almacén de ZIP: no se generan ni se limpian",
                              extra={"fields": {"marker": str(settings.zips_dir / MARKER)}})
                was_mounted = now_mounted
                await reconcile_zips(conn, settings, roots)
            except Exception:
                log.exception("fallo al reconciliar los zips")
                await conn.rollback()
            await asyncio.sleep(INTERVAL_S)
    finally:
        await conn.close()
