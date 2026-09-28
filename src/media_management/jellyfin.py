import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError

from media_management.db import parse_iso, utcnow
from media_management.roots import kind_of

log = logging.getLogger(__name__)

ENTITY_TYPE = {"video": "MediaBrowser.Controller.Entities.Video",
               "photo": "MediaBrowser.Controller.Entities.Photo"}


class IdsExport(BaseModel):
    generated_at: str
    items: dict[str, str]


@dataclass(frozen=True)
class ExportStatus:
    ids: dict[str, str] | None
    problem: str | None
    age_s: int | None


def load_ids_export(path: Path | None, max_age_s: int) -> ExportStatus:
    """Pares ruta→id que Jellyfin tiene indexados, o el motivo por el que no se pueden
    usar. Un export ausente, vacío o viejo no vale: convertiría en `null` ("aún no
    indexado") clips que sí lo están."""
    if path is None:
        return ExportStatus(None, "no hay export de IDs configurado", None)
    try:
        doc = IdsExport.model_validate(json.loads(path.read_text()))
        generated = parse_iso(doc.generated_at)
    except FileNotFoundError:
        return ExportStatus(None, "falta el export de IDs de Jellyfin", None)
    except (OSError, ValueError, ValidationError) as e:
        return ExportStatus(None, f"export de IDs ilegible: {type(e).__name__}", None)
    age = int((utcnow() - generated).total_seconds())
    if not doc.items:
        return ExportStatus(None, "el export de IDs está vacío", age)
    if age > max_age_s:
        return ExportStatus(None, f"el export de IDs tiene {age // 60} min (máximo {max_age_s // 60})", age)
    return ExportStatus({p: i.replace("-", "").lower() for p, i in doc.items.items()}, None, age)


def compute_item_id(kind: str, container_path: str) -> str | None:
    """ID que Jellyfin asigna a un fichero, derivado de su ruta dentro del contenedor.

    Solo sirve para detectar que Jellyfin ha cambiado el algoritmo: que el ID se pueda
    calcular no significa que el fichero esté indexado."""
    entity = ENTITY_TYPE.get(kind)
    if entity is None:
        return None
    digest = hashlib.md5((entity + container_path).encode("utf-16-le")).digest()
    return uuid.UUID(bytes_le=digest).hex


def id_mismatches(ids: dict[str, str]) -> list[str]:
    out = []
    for path, item_id in ids.items():
        expected = compute_item_id(kind_of(path), path)
        if expected is not None and expected != item_id:
            out.append(path)
    return out


class JellyfinError(Exception):
    pass


class Jellyfin:
    def __init__(self, base_url: str, timeout_s: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    async def ping(self) -> bool:
        if not self.base_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                r = await client.get(f"{self.base_url}/System/Info/Public")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def primary_image(self, item_id: str) -> bytes:
        """Miniatura JPEG de 320 px. Un 500 de Jellyfin es tanto "ese ID no existe" como
        "he fallado yo", así que cualquier respuesta que no sea una imagen es un error y
        nunca se cachea como "no hay miniatura"."""
        if not self.base_url:
            raise JellyfinError("Jellyfin no configurado")
        url = f"{self.base_url}/Items/{item_id}/Images/Primary"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                r = await client.get(url, params={"fillWidth": 320, "quality": 80})
        except httpx.HTTPError as e:
            raise JellyfinError(type(e).__name__) from e
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
            raise JellyfinError(f"HTTP {r.status_code}")
        return r.content
