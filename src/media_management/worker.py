import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

import aiosqlite

from media_management.catalog import RootScan, scan_root
from media_management.db import get_state, init_main, now_iso, open_persistent, set_state
from media_management.jellyfin import Jellyfin, id_mismatches, load_ids_export
from media_management.logs import audit
from media_management.manifest import build_manifest, compare, serialize, write_atomic
from media_management.roots import Root, load_roots, media_problem
from media_management.settings import Settings
from media_management.thumbs import get_thumb, thumb_item_id

log = logging.getLogger(__name__)
THUMB_PREFETCH_PER_CYCLE = 400


async def write_manifest(conn: aiosqlite.Connection, settings: Settings, roots: dict[str, Root],
                         scans: dict[str, RootScan], ids: dict[str, str] | None,
                         export_problem: str | None) -> None:
    """Manifiesto para TripPlanner. Si no se sabe qué ha indexado Jellyfin, no se
    escribe: `null` tiene que significar "aún no indexado", nunca "no pude mirar", y el
    manifiesto anterior, más viejo pero cierto, se queda donde está."""
    root = next((r for r in roots.values() if r.in_manifest), None)
    if root is None or root.jellyfin_path is None:
        return
    previous = await get_state(conn, "manifest_status")
    last_written = json.loads(previous).get("last_written_at") if previous else None
    status: dict[str, Any] = {"at": now_iso(), "written": False, "last_written_at": last_written}
    scan = scans.get(root.name)
    top_level = [m for rel, m in (scan.metas.items() if scan else []) if "/" not in rel]
    if ids is None:
        status["problem"] = export_problem or "sin IDs de Jellyfin"
    elif not top_level:
        status["problem"] = f"ni un fichero en {root.name}"
    else:
        result = build_manifest(top_level, root.jellyfin_path, ids)
        text = serialize(result.doc)
        if settings.manifest_path is not None:
            await asyncio.to_thread(write_atomic, settings.manifest_path, text)
        await set_state(conn, "manifest_doc", text)
        mismatches = id_mismatches(ids)
        if mismatches:
            log.warning("IDs de Jellyfin distintos del cálculo: puede haber cambiado el algoritmo",
                        extra={"fields": {"count": len(mismatches), "sample": mismatches[:5]}})
        status.update(written=True, last_written_at=status["at"], clips=len(result.doc["clips"]),
                      without_id=result.without_id, without_date=len(result.without_date),
                      without_date_names=result.without_date, odd_names=result.odd_names,
                      id_mismatches=len(mismatches), path=str(settings.manifest_path or ""))
        if settings.manifest_compare_with is not None:
            await compare_with_current(conn, settings.manifest_compare_with, result.doc)
    await set_state(conn, "manifest_status", json.dumps(status))


async def compare_with_current(conn: aiosqlite.Connection, current_path: Path,
                               candidate: dict[str, Any]) -> None:
    out: dict[str, Any] = {"at": now_iso(), "current": str(current_path)}
    try:
        current = json.loads(await asyncio.to_thread(current_path.read_text))
    except (OSError, ValueError) as e:
        out.update(identical=False, differences=[], problem=f"no pude leer el actual: {type(e).__name__}")
    else:
        diffs = compare(candidate, current)
        out.update(identical=not diffs, differences=diffs[:500], total=len(diffs),
                   current_generated_at=current.get("generated_at"),
                   candidate_generated_at=candidate.get("generated_at"))
    await set_state(conn, "manifest_comparison", json.dumps(out))


async def prefetch_thumbs(conn: aiosqlite.Connection, settings: Settings, roots: dict[str, Root]) -> int:
    jellyfin = Jellyfin(settings.jellyfin_url, settings.jellyfin_timeout_s)
    if not settings.jellyfin_url:
        return 0
    async with conn.execute("SELECT * FROM files WHERE present = 1 AND kind IN ('video', 'photo')") as cur:
        rows = await cur.fetchall()
    wanted: list[str] = []
    for row in rows:
        item_id = await thumb_item_id(conn, roots, row)
        if item_id and item_id not in wanted:
            wanted.append(item_id)
    sem = asyncio.Semaphore(4)
    fetched = 0

    async def one(item_id: str) -> None:
        nonlocal fetched
        async with sem:
            if await get_thumb(settings.cache_dir, jellyfin, item_id) is not None:
                fetched += 1

    await asyncio.gather(*(one(i) for i in wanted[:THUMB_PREFETCH_PER_CYCLE]))
    return fetched


async def run_cycle(conn: aiosqlite.Connection, settings: Settings, roots: dict[str, Root]) -> None:
    await set_state(conn, "worker_heartbeat", now_iso())
    problem = media_problem(settings, roots)
    if problem is not None:
        await set_state(conn, "media_problem", problem)
        await conn.commit()
        log.error("media no disponible: no se escanea", extra={"fields": {"problem": problem}})
        return
    await conn.execute("DELETE FROM state WHERE key = 'media_problem'")
    export = load_ids_export(settings.jellyfin_ids_file, settings.jellyfin_ids_max_age_s)
    started = now_iso()
    scans: dict[str, RootScan] = {}
    for root in roots.values():
        scans[root.name] = await scan_root(conn, root, export.ids)
    summary = {
        "started_at": started, "finished_at": now_iso(),
        "files": sum(s.files for s in scans.values()),
        "new": sum(s.new for s in scans.values()),
        "gone": sum(s.gone for s in scans.values()),
        "meta_errors": sum(s.meta_errors for s in scans.values()),
        "roots": {name: {k: v for k, v in asdict(s).items() if k != "metas"} for name, s in scans.items()},
        "ids_export_problem": export.problem,
    }
    await set_state(conn, "last_scan", json.dumps(summary))
    await write_manifest(conn, settings, roots, scans, export.ids, export.problem)
    await conn.commit()
    if summary["new"] or summary["gone"]:
        await audit(conn, "worker", "scan", "ok", new=summary["new"], gone=summary["gone"])
        await conn.commit()
    fetched = await prefetch_thumbs(conn, settings, roots)
    log.info("ciclo completado", extra={"fields": {**{k: summary[k] for k in ("files", "new", "gone")},
                                                    "thumbs_fetched": fetched}})


async def run(settings: Settings) -> None:
    roots = load_roots(settings)
    await init_main(settings)
    # Conexión que vive lo que el proceso: mantiene los ficheros -wal/-shm de main.db,
    # sin los que el proceso público, que la ve en solo lectura, no puede abrirla.
    conn = await open_persistent(settings.main_db)
    try:
        while True:
            try:
                await run_cycle(conn, settings, roots)
            except Exception:
                log.exception("fallo en el ciclo del worker")
                await conn.rollback()
            await wait_next(conn, settings)
    finally:
        await conn.close()


async def wait_next(conn: aiosqlite.Connection, settings: Settings) -> None:
    before = await get_state(conn, "scan_requested")
    for tick in range(max(1, settings.scan_interval_s // 2)):
        await asyncio.sleep(2)
        if await get_state(conn, "scan_requested") != before:
            return
        if tick % 15 == 14:
            await set_state(conn, "worker_heartbeat", now_iso())
            await conn.commit()
