import logging

import httpx

from media_management.settings import Settings

log = logging.getLogger(__name__)


def one_line(text: str, limit: int) -> str:
    return " ".join("".join(c if c.isprintable() else " " for c in text).split())[:limit]


async def notify_access_request(settings: Settings, link_id: int, link_title: str, name: str) -> bool:
    """Aviso por ntfy de una solicitud de acceso. El texto del visitante va solo en el
    cuerpo, en una línea y sin markdown; las cabeceras llevan texto propio."""
    if not settings.ntfy_url or not settings.ntfy_topic:
        return False
    body = f"{one_line(name, 80)} pide acceso a «{one_line(link_title, 120)}»."
    headers = {"Title": "Solicitud de acceso a un enlace", "Tags": "inbox_tray",
               "Click": f"{settings.panel_url.rstrip('/')}/links/{link_id}", "Markdown": "no"}
    if settings.ntfy_token:
        headers["Authorization"] = f"Bearer {settings.ntfy_token}"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(f"{settings.ntfy_url.rstrip('/')}/{settings.ntfy_topic}",
                                  content=body.encode(), headers=headers)
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("no se pudo avisar por ntfy", extra={"fields": {"error": type(e).__name__, "link_id": link_id}})
        return False
    return True
