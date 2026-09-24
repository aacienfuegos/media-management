# media-management

Panel para gestionar una biblioteca de media personal y compartir ficheros con
enlaces de descarga, pensado para un homelab.

- **Catálogo** de las raíces declaradas en configuración, con metadatos (fecha de
  captura, duración, resolución), miniaturas de Jellyfin y estado de indexado.
- **Manifiesto** JSON versionado para otras apps (contrato v1) y una **API** de lectura
  (`/api/v1`) con tokens.
- **Enlaces de descarga** sobre ficheros que ya existen: caducidad obligatoria, modo
  abierto, con contraseña o por solicitud con aprobación persona a persona, y registro
  de quién descarga qué y desde dónde.
- **Papelera** que solo mueve ficheros (el vaciado lo hace el host) y renombrado donde
  la raíz lo permite.

La interfaz y la documentación están en español.

## Arquitectura

```
internet ─► Traefik ─► nginx ─► público (FastAPI)      enlaces, tickets, solicitudes
LAN      ─► Traefik + forward-auth ─► panel (FastAPI + Jinja)
LAN      ─► Traefik ─► API (FastAPI)                   /api/v1, solo tokens
                          worker                        escaneo, manifiesto, miniaturas
```

Un paquete, cuatro puntos de entrada, sin routers compartidos. Cada proceso contiene
solo sus rutas: el público no tiene código de administración y la API no contiene el
código que lee la identidad del proxy de autenticación.

Dos SQLite en WAL, separadas por quién escribe: `main.db` (catálogo, enlaces,
concesiones, tokens, auditoría) la escriben panel, API y worker; el público la abre en
solo lectura (por URI y por montaje) y solo escribe en `public.db`.

## Decisiones de seguridad

- **El token del enlace va en el fragmento** (`https://share.example.org/#<token>`): el
  navegador no lo envía nunca, así que no acaba en access logs, en el `Referer` ni en
  los bots de previsualización. La página lo manda en el cuerpo de un POST.
- **Abrir el enlace no consume nada.** Cada fichero se pide con un ticket firmado,
  ligado al enlace, al fichero y a la credencial, que dura 4 horas (o menos, si el
  enlace caduca antes) para poder reanudar. Cada petición, también cada `Range`,
  vuelve a comprobar que el enlace y la concesión siguen vivos.
- **Los bytes no pasan por Python**: nginx los sirve por `X-Accel-Redirect` desde una
  `location internal`.
- **El cliente nunca manda rutas**, solo identificadores; la ruta sale de la BD y se
  valida con `realpath` contra la raíz.
- **La app mueve, nunca desenlaza.** Borrar es un `rename` sin sobrescritura a una
  papelera fuera de las raíces; el host la vacía. En las raíces marcadas como
  `requires_second_copy`, no se borra nada que no conste en una segunda copia.
- **El endpoint de streaming de Jellyfin no pide autenticación**: hacia internet no sale
  nunca un ID ni una URL de Jellyfin. Las miniaturas se copian al crear el enlace, sin
  metadatos.
- Contraseñas con Argon2id, retardo creciente por enlace y bloqueo por IP (nunca por
  enlace). La IP real solo se acepta del proxy que hay delante.
- El texto que escriben los visitantes se trata como hostil: autoescape, CSP estricta
  sin `unsafe-inline` en todas las páginas, texto plano y longitud acotada.

## Desarrollo

Requisitos: Python 3.13 y [uv](https://docs.astral.sh/uv/); `ffmpeg` para los vídeos
sintéticos de los tests; Docker para la prueba de integración con nginx (se salta sin
él).

```sh
uv sync
uv run pytest            # suite completa con datos sintéticos
uv run mypy              # tipos (estricto)
uv run pip-audit         # vulnerabilidades en dependencias
```

Las pruebas marcadas `local` usan datos reales que no están en el repo
(`fixtures/local/`, ignorado por git) y se saltan si no existen.

Para arrancar un proceso: `uv run media-management serve panel|api|public` o
`uv run media-management worker`, con la configuración en variables `MM_*` (ver
`.env.example` y `src/media_management/settings.py`). Las raíces se declaran en un
TOML (`deploy/roots.example.toml`).

## Despliegue

Imagen en `ghcr.io` construida por la Action en cada push a `develop` (`staging`) y
`main` (`latest`). Cómo darlo de alta, qué necesita del host y el traspaso del
manifiesto: [`deploy/README.md`](deploy/README.md).

## API

OpenAPI en `/api/v1/openapi.json`. Autenticación: `Authorization: Bearer <token>`, con
tokens de solo lectura creados desde el panel.

| Método | Ruta | |
|---|---|---|
| GET | `/api/v1/roots` | raíces con sus propiedades y totales |
| GET | `/api/v1/roots/{root}/files?page=&page_size=&kind=` | ficheros presentes, paginado |
| GET | `/api/v1/files/{id}` | detalle de un fichero |
| GET | `/api/v1/manifest` | el manifiesto, byte a byte igual que el fichero; `ETag` + `If-None-Match` → `304` |
| GET | `/healthz` | estado real: raíces montadas, export de IDs fresco, Jellyfin, worker |
