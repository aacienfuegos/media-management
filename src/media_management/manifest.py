import datetime
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from media_management.db import iso, utcnow
from media_management.media_meta import FileMeta, day_offsets, effective_date

MANIFEST_VIDEO = frozenset({".mp4", ".mov", ".mkv"})
MANIFEST_PHOTO = frozenset({".jpg", ".jpeg", ".png"})
CAMERA_NAME = re.compile(r"^DJI_\d{14}_\d{4}_D\.(MP4|JPG)$")


class ManifestClip(BaseModel):
    """Contrato v1 acordado con TripPlanner. Las claves de más son diagnóstico y la app
    las ignora; si hace falta un campo nuevo, se versiona, no se cambia v1 en sitio."""
    model_config = ConfigDict(extra="allow")

    jellyfin_path: str
    jellyfin_item_id: str | None
    kind: Literal["video", "photo"]
    captured_at_utc: str
    size_bytes: int


class Manifest(BaseModel):
    version: Literal[1]
    generated_at: str
    clips: list[ManifestClip]


@dataclass
class ManifestResult:
    doc: dict[str, Any]
    without_id: int
    without_date: list[str] = field(default_factory=list)
    odd_names: list[str] = field(default_factory=list)


def manifest_kind(name: str) -> str | None:
    ext = os.path.splitext(name)[1].lower()
    if ext in MANIFEST_VIDEO:
        return "video"
    if ext in MANIFEST_PHOTO:
        return "photo"
    return None


def build_manifest(files: list[FileMeta], jellyfin_dir: str, ids: dict[str, str],
                   now: datetime.datetime | None = None) -> ManifestResult:
    """Mismo resultado que el generador que corría en el host, clip a clip.

    Un clip sin fecha fiable se queda fuera y se informa: una fecha inventada lo
    colocaría en la inmersión equivocada, que es peor que no verlo."""
    entries = sorted((f for f in files if manifest_kind(f.name) is not None), key=lambda f: f.name)
    offsets = day_offsets(entries)
    clips: list[dict[str, Any]] = []
    without_date: list[str] = []
    without_id = 0
    for f in entries:
        utc, source = effective_date(f, offsets)
        if not utc:
            without_date.append(f.name)
            continue
        path = f"{jellyfin_dir}/{f.name}"
        item_id = ids.get(path)
        without_id += item_id is None
        clips.append({"jellyfin_path": path, "jellyfin_item_id": item_id,
                      "kind": manifest_kind(f.name), "captured_at_utc": utc,
                      "size_bytes": f.size_bytes,
                      "name": f.name, "captured_at_source": source,
                      "duration_s": f.duration_s, "width": f.width, "height": f.height,
                      "source_mtime": f.mtime_s})
    doc = {"version": 1, "generated_at": iso(now or utcnow()), "clips": clips}
    odd = sorted(f.name for f in entries if not CAMERA_NAME.match(f.name))
    return ManifestResult(doc, without_id, without_date, odd)


def serialize(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1)


def write_atomic(path: Path, text: str) -> None:
    """Temporal en el mismo directorio y `rename`: quien lo lee nunca ve medio JSON."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


COMPARED = ("jellyfin_item_id", "captured_at_utc", "size_bytes", "kind")


def compare(candidate: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    """Diferencias clip a clip entre dos manifiestos, por identidad (`jellyfin_path`)."""
    cand = {c["jellyfin_path"]: c for c in candidate.get("clips", [])}
    cur = {c["jellyfin_path"]: c for c in current.get("clips", [])}
    out: list[dict[str, Any]] = []
    for path in sorted(cand.keys() | cur.keys()):
        a, b = cand.get(path), cur.get(path)
        if a is None:
            out.append({"jellyfin_path": path, "problem": "solo en el actual"})
        elif b is None:
            out.append({"jellyfin_path": path, "problem": "solo en el candidato"})
        else:
            for key in COMPARED:
                if a.get(key) != b.get(key):
                    out.append({"jellyfin_path": path, "problem": f"{key} distinto",
                                "candidate": a.get(key), "current": b.get(key)})
    return out
