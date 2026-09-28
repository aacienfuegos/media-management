import os
import tomllib
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from media_management.settings import Settings

VIDEO_EXT = frozenset({".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"})
PHOTO_EXT = frozenset({".jpg", ".jpeg", ".png", ".heic", ".webp", ".dng"})


class Root(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$", max_length=64)
    path: Path
    shareable: bool = False
    deletable: bool = False
    renamable: bool = False
    synced: bool = False
    requires_second_copy: bool = False
    indexed_by_jellyfin: bool = False
    jellyfin_path: str | None = None
    in_manifest: bool = False
    metadata_profile: Literal["dji", "generic"] = "generic"
    thumbnail_from: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> "Root":
        if not self.path.is_absolute():
            raise ValueError(f"la raíz {self.name} necesita una ruta absoluta")
        if self.synced and self.renamable:
            raise ValueError(
                f"la raíz {self.name} está sincronizada: su nombre de fichero es la "
                "identidad del clip y no se puede renombrar")
        if (self.indexed_by_jellyfin or self.in_manifest) and not self.jellyfin_path:
            raise ValueError(f"la raíz {self.name} necesita jellyfin_path")
        if self.in_manifest and not self.indexed_by_jellyfin:
            raise ValueError(f"la raíz {self.name} va al manifiesto sin estar en Jellyfin")
        return self


class RootsConfig(BaseModel):
    roots: list[Root]


def load_roots(settings: Settings) -> dict[str, Root]:
    with open(settings.roots_file, "rb") as f:
        cfg = RootsConfig.model_validate(tomllib.load(f))
    roots: dict[str, Root] = {}
    base = settings.media_base
    for root in cfg.roots:
        if root.name in roots:
            raise ValueError(f"raíz duplicada: {root.name}")
        # Borrar es un rename a la papelera: solo es instantáneo y atómico si la raíz
        # y la papelera están en el mismo sistema de ficheros, bajo la misma base.
        if not root.path.is_relative_to(base) or root.path == base:
            raise ValueError(f"la raíz {root.name} tiene que estar dentro de {base}")
        if settings.trash_dir.is_relative_to(root.path) or root.path.is_relative_to(settings.trash_dir):
            raise ValueError(f"la papelera no puede solaparse con la raíz {root.name}")
        roots[root.name] = root
    if sum(r.in_manifest for r in roots.values()) > 1:
        raise ValueError("el manifiesto v1 es de una sola raíz")
    for a in roots.values():
        for b in roots.values():
            if a is not b and a.path.is_relative_to(b.path):
                raise ValueError(f"las raíces {a.name} y {b.name} se solapan")
        if a.thumbnail_from is not None and a.thumbnail_from not in roots:
            raise ValueError(f"thumbnail_from de {a.name} apunta a una raíz inexistente")
    return roots


def kind_of(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in PHOTO_EXT:
        return "photo"
    return "other"


class PathRejected(Exception):
    pass


def resolve_in_root(root: Root, relpath: str) -> Path:
    """Ruta real de `relpath` dentro de `root`, o PathRejected.

    `relpath` sale de la base de datos, nunca del cliente; aun así se valida aquí
    porque un fichero de la BD puede haberse cambiado en disco por un symlink."""
    rel = PurePosixPath(relpath)
    if (not relpath or rel.is_absolute() or "\x00" in relpath
            or any(part in ("", ".", "..") for part in relpath.split("/"))):
        raise PathRejected(relpath)
    real_root = Path(os.path.realpath(root.path))
    real = Path(os.path.realpath(real_root / rel))
    if real == real_root or not real.is_relative_to(real_root):
        raise PathRejected(relpath)
    return real


def media_problem(settings: Settings, roots: dict[str, Root]) -> str | None:
    """Motivo por el que no se puede operar sobre la media, o None.

    Una raíz vacía porque el disco cifrado no está desbloqueado es indistinguible de
    una biblioteca vacía salvo por el centinela, que vive dentro del volumen."""
    if not settings.sentinel.is_file():
        return (f"falta el centinela {settings.sentinel.name} en la base de media: "
                "el volumen no está montado o no está desbloqueado")
    for root in roots.values():
        if not root.path.is_dir():
            return f"no existe el directorio de la raíz {root.name}"
    return None
