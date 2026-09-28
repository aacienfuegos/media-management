#!/usr/bin/env python3
"""Exporta qué ficheros tiene indexados Jellyfin y con qué ID.

Corre en el host como root (lee la BD de Jellyfin dentro de su contenedor con
`pct exec ... sqlite3 -readonly`) y escribe un JSON que la app lee montado en solo
lectura. La app no guarda ninguna llave de Jellyfin ni monta su base de datos.

Si no se puede leer la BD, o no hay ni una fila de la biblioteca, NO escribe nada:
el fichero anterior se queda y envejece, y la app deja de fiarse de él pasada una
hora. Escribir un export vacío convertiría en "aún no indexado" clips que sí lo
están.

Configuración por entorno (ver jellyfin-ids-export.env.example).
"""
import datetime
import json
import os
import subprocess
import sys

CTID = os.environ["MM_EXPORT_CTID"]
DB = os.environ.get("MM_EXPORT_DB", "/var/lib/jellyfin/data/jellyfin.db")
PREFIXES = [p.rstrip("/") + "/" for p in os.environ["MM_EXPORT_LIBRARY_DIRS"].split(",") if p.strip()]
OUT = os.environ["MM_EXPORT_OUT"]
PCT = os.environ.get("MM_EXPORT_PCT", "pct").split()
QUERY = "SELECT Id || '|' || Path FROM BaseItems WHERE Path IS NOT NULL;"


def log(msg: str) -> None:
    sys.stderr.write(msg + "\n")


def read_ids() -> dict[str, str] | None:
    try:
        r = subprocess.run([*PCT, "exec", CTID, "--", "sqlite3", "-readonly", "-cmd", ".timeout 5000",
                            DB, QUERY], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"no pude consultar la BD de Jellyfin: {e}")
        return None
    if r.returncode != 0:
        log(f"sqlite3 en el contenedor {CTID} salió con {r.returncode}: {(r.stderr or '').strip()[:200]}")
        return None
    items: dict[str, str] = {}
    for line in r.stdout.splitlines():
        if "|" not in line:
            continue
        ident, path = line.split("|", 1)
        if any(path.startswith(p) for p in PREFIXES):
            items[path] = ident.replace("-", "").lower()
    return items


def main() -> int:
    items = read_ids()
    if items is None:
        return 1
    if not items:
        log("la BD de Jellyfin no devolvió ni una fila de la biblioteca: no escribo el export")
        return 1
    doc = {"generated_at": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "items": dict(sorted(items.items()))}
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o644)
    os.replace(tmp, OUT)
    log(f"export al día: {len(items)} ficheros indexados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
