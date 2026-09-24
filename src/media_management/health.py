from typing import Any

import aiosqlite

from media_management.db import get_state, parse_iso, utcnow
from media_management.jellyfin import Jellyfin, load_ids_export
from media_management.roots import Root, media_problem
from media_management.settings import Settings


async def health_detail(settings: Settings, roots: dict[str, Root],
                        conn: aiosqlite.Connection) -> tuple[bool, dict[str, Any]]:
    """Estado real de lo que el servicio necesita, no solo que el proceso vive."""
    media = media_problem(settings, roots)
    export = load_ids_export(settings.jellyfin_ids_file, settings.jellyfin_ids_max_age_s)
    jellyfin_ok = await Jellyfin(settings.jellyfin_url, settings.jellyfin_timeout_s).ping()
    beat = await get_state(conn, "worker_heartbeat")
    beat_age = None if beat is None else int((utcnow() - parse_iso(beat)).total_seconds())
    worker_ok = beat_age is not None and beat_age < max(3 * settings.scan_interval_s, 120)
    detail: dict[str, Any] = {
        "media": {"ok": media is None, "problem": media},
        "jellyfin_ids_export": {"ok": export.problem is None, "problem": export.problem,
                                "age_s": export.age_s},
        "jellyfin": {"ok": jellyfin_ok},
        "worker": {"ok": worker_ok, "heartbeat_age_s": beat_age},
    }
    return all(v["ok"] for v in detail.values()), detail
