from collections.abc import Callable
from typing import Any

ACTIONS: dict[str, tuple[str, str]] = {
    "link_created": ("Enlace creado", "Enlaces"),
    "link_renewed": ("Caducidad cambiada", "Enlaces"),
    "link_revoked": ("Enlace revocado", "Enlaces"),
    "link_zip_enable": ("ZIP activado", "Enlaces"),
    "link_zip_disable": ("ZIP retirado", "Enlaces"),
    "link_zip_regenerate": ("ZIP pedido de nuevo", "Enlaces"),
    "access_request": ("Solicitud de acceso", "Accesos"),
    "access_approved": ("Solicitud aprobada", "Accesos"),
    "access_rejected": ("Solicitud rechazada", "Accesos"),
    "grant_revoked": ("Acceso revocado", "Accesos"),
    "password_failed": ("Contraseña incorrecta", "Accesos"),
    "code_failed": ("Código incorrecto", "Accesos"),
    "auth_rate_limited": ("Demasiados intentos", "Accesos"),
    "share_not_found": ("Enlace inexistente", "Accesos"),
    "ticket_issued": ("Descarga iniciada", "Descargas"),
    "download_served": ("Descarga servida", "Descargas"),
    "file_trash": ("A la papelera", "Ficheros"),
    "file_restore": ("Restaurado", "Ficheros"),
    "file_rename": ("Renombrado", "Ficheros"),
    "scan": ("Escaneo", "Sistema"),
    "scan_requested": ("Escaneo pedido", "Sistema"),
    "zip_built": ("ZIP generado", "Sistema"),
    "api_token_created": ("Token creado", "Sistema"),
    "api_token_revoked": ("Token revocado", "Sistema"),
}

CATEGORIES = ("Enlaces", "Accesos", "Descargas", "Ficheros", "Sistema")

RESULTS = {"ok": "ok", "denied": "denegado", "rate_limited": "limitado", "queue_full": "cola llena",
           "error": "error"}

_KEYS = {
    "mode": "modo", "files": "ficheros", "days": "días", "zip": "ZIP", "size": "tamaño", "seconds": "segundos",
    "new": "nuevo", "gone": "desaparecidos", "request_id": "solicitud", "grant_id": "concesión",
    "link_file_id": "fichero del enlace", "file_id": "fichero", "entry_id": "entrada", "trash": "en papelera",
    "expires_at": "caduca", "name": "nombre", "reasons": "motivos", "status_code": "estado HTTP",
    "range": "rango", "need": "necesita", "error": "error",
}
_MODES = {"open": "abierto", "password": "contraseña", "request": "por solicitud"}
_HIDDEN = {"link_id"}


def action_label(action: str) -> str:
    return ACTIONS.get(action, (action, ""))[0]


def grouped_actions(present: set[str]) -> list[tuple[str, list[tuple[str, str]]]]:
    groups = [(cat, sorted((a, label) for a, (label, c) in ACTIONS.items() if c == cat and a in present))
              for cat in CATEGORIES]
    other = sorted((a, a) for a in present if a not in ACTIONS)
    return [(cat, items) for cat, items in [*groups, ("Otras", other)] if items]


def detail_items(detail: dict[str, Any] | None, size: Callable[[int], str],
                 local: Callable[[str], str]) -> list[tuple[str, str]]:
    items = []
    for key, value in (detail or {}).items():
        if key in _HIDDEN or value is None:
            continue
        if key == "mode":
            value = _MODES.get(value, value)
        elif key == "zip":
            value = "sí" if value else "no"
        elif key == "size":
            value = size(value)
        elif key == "expires_at":
            value = local(value)
        elif isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        items.append((_KEYS.get(key, key), str(value)))
    return items
