# Técnico

Cómo está construido media-management, sacado del código. `src/` abrevia
`src/media_management/`. El repo no tiene `CLAUDE.md` ni `PROMPT.md`: las referencias de
trabajo son [`README.md`](../README.md) (uso, demo, desarrollo y API) y
[`deploy/README.md`](../deploy/README.md) (despliegue). Este documento no repite ninguno de
los dos: los resume y remite a ellos.

## Stack

| Pieza | Qué se usa | Fuente |
|---|---|---|
| Lenguaje | Python 3.13 | `pyproject.toml` (`requires-python`), `.python-version`, `Dockerfile:1,10` |
| Gestor de dependencias | uv, con `uv.lock` | `README.md`, "Desarrollo"; `.github/workflows/image.yml` |
| Web | FastAPI servido con uvicorn, sin `proxy_headers` | `src/cli.py:14-27` |
| Plantillas del panel | Jinja2 con autoescape | `src/panel/deps.py:18-19` |
| Página pública | HTML estático más JavaScript sin framework | `src/public/static/index.html`, `share-ui.js`, `share.js` |
| Base de datos | Dos SQLite en WAL, con aiosqlite | `src/db.py:41-58` |
| Contenedor | Una imagen; el proceso corre como UID 10001 | `Dockerfile:18-20` |
| Delante del público | nginx, que sirve los bytes con `X-Accel-Redirect` | `src/public/routes.py:236-239`, `deploy/nginx/share.conf.template:67-82` |
| Tipos | mypy estricto sobre `src/` | `pyproject.toml`, `[tool.mypy]` |
| Tests | pytest, con marcas `local` (datos reales fuera del repo) y `docker` | `pyproject.toml`, `[tool.pytest.ini_options]` |

## Procesos

Un solo paquete con cuatro puntos de entrada (`src/cli.py:7-11`, `src/cli.py:43-47`):

| Proceso | Puerto por defecto | Qué hace | Monta | Fuente |
|---|---|---|---|---|
| `panel` | 8002 | Administración, plantillas Jinja | `panel/` | `src/panel/app.py:23-111` |
| `api` | 8003 | `/api/v1`, solo lectura con token | `api/` | `src/api/app.py:16-37` |
| `public` | 8001 | Página del enlace, tickets, solicitudes | `public/` | `src/public/app.py:55-90` |
| `worker` | (no escucha) | Escaneo, manifiesto, miniaturas, papelera, ZIP | `worker.py`, `zips.py` | `src/worker.py:152-169` |

Hay además dos subcomandos de apoyo: `compare` (compara dos manifiestos clip a clip) y
`demo-seed` (siembra el modo demo) (`src/cli.py:48-53`).

### Por qué están separados

- **Cada proceso contiene solo sus rutas.** El público no importa nada del panel y la
  API no contiene el código que lee la cabecera de identidad del proxy
  (`README.md`, "Arquitectura"; `src/api/deps.py:20-21`). Los routers del panel se montan
  solo en `src/panel/app.py:106-110`; el público monta un único router
  (`src/public/app.py:89`).
- **Una clave por proceso.** El panel exige `MM_CSRF_KEY` y el público `MM_TICKET_KEY`,
  cada una de al menos 32 caracteres; comprometer el público no da nada contra el panel
  (`src/settings.py:18-21`, `src/settings.py:84-87`, `src/panel/app.py:25`,
  `src/public/app.py:57`).
- **Cada proceso decide de quién se fía.** uvicorn arranca sin `proxy_headers`: el panel
  solo acepta identidad desde `MM_TRUSTED_PROXIES` y el público solo cree la IP real que
  manda nginx desde `MM_TRUSTED_NGINX` (`src/cli.py:21-22`, `src/panel/deps.py:83-96`,
  `src/public/guard.py:12-22`).
- **El público no puede escribir en `main.db`.** La abre con `mode=ro` y el contenedor la
  monta en solo lectura (`src/public/app.py:23-28`, `compose.yaml`, servicio `public`).
- **El público no habla con Jellyfin.** Las miniaturas se copian, sin metadatos, al crear
  el enlace (`src/panel/links_routes.py:80-85`, `src/links.py:87-104`).

## Límites de diseño

- **Los bytes no pasan por Python.** El público valida el ticket y responde con
  `X-Accel-Redirect` a una `location internal` de nginx, tanto para ficheros
  (`/_protected/`) como para ZIP (`/_zips/`) (`src/public/routes.py:195-239`,
  `deploy/nginx/share.conf.template:67-82`).
- **El cliente nunca manda rutas**, solo identificadores; la ruta sale de la BD y se
  resuelve con `realpath` dentro de la raíz (`src/roots.py:103-116`,
  `src/links.py:70-84`).
- **La app mueve y nunca borra la biblioteca.** Borrar es un `rename` sin sobrescritura
  (`RENAME_NOREPLACE`) a `<base>/.trash/` en el mismo sistema de ficheros
  (`src/trash.py:1-8`, `src/trash.py:30-32`); la papelera no puede solaparse con ninguna
  raíz (`src/roots.py:61-62`). La excepción son los ZIP, que el worker borra solo dentro
  de `MM_ZIPS_DIR` y solo con nombres suyos (`src/zips.py:14-15`, `src/zips.py:42`), y que
  no se generan ni se borran sin el marcador `.zips-root` (`src/zips.py:129-132`).
- **Sin centinela no se toca nada.** Si falta `.media-root` en la base de media, el worker
  no escanea y el panel no mueve ni renombra (`src/roots.py:119-130`,
  `src/panel/trash_routes.py:25-28`).
- **Cabeceras de seguridad en los tres procesos web**: CSP sin `unsafe-inline`,
  `nosniff`, `X-Frame-Options: DENY`, COOP/CORP `same-origin` y `no-store` por defecto
  (`src/web.py:5-20`). El panel usa `Referrer-Policy: same-origin` para que el CSRF pueda
  comprobar `Origin` (`src/panel/app.py:37-39`).
- **Logs en JSON a stdout**, sin el `INFO` de httpx porque la URL de una miniatura lleva un
  ID de Jellyfin (`src/logs.py:29-38`). El público no tiene access log propio
  (`src/cli.py:23-27`).
- **Configuración por variables `MM_*`** con pydantic-settings, sin leer ficheros `.env`
  (`src/settings.py:11-12`). Los nombres de variable están en `.env.example`.

## Dependencias

De ejecución (`pyproject.toml`, `dependencies`): `fastapi`, `uvicorn`, `jinja2`,
`pydantic-settings`, `aiosqlite`, `httpx` (Jellyfin y ntfy), `argon2-cffi` (contraseñas de
enlaces) y `python-multipart` (formularios del panel). De desarrollo
(`[dependency-groups]`): `pytest`, `pip-audit` y `mypy`. Las versiones exactas están en
`uv.lock`.

## Servicios externos

| Servicio | Para qué | Quién lo usa | Fuente |
|---|---|---|---|
| Proxy de autenticación (Authentik detrás de Traefik) | Identidad del panel por cabecera | `panel` | `src/panel/deps.py:83-96` |
| Jellyfin | Miniaturas y comprobación de que responde | `panel`, `worker`, `/healthz` | `src/jellyfin.py:103-120`, `src/health.py:17` |
| Export de IDs de Jellyfin | Fichero ruta-ID que genera un timer del host; base del manifiesto | `worker`, `/healthz` | `src/jellyfin.py:45-53`, `deploy/host/jellyfin-ids-export.py` |
| ntfy | Aviso de cada solicitud de acceso | `public` | `src/notify.py:14-32` |
| nginx | Delante del público; sirve los bytes | `public` | `deploy/nginx/share.conf.template` |

Las URL de estos servicios son configuración (`MM_JELLYFIN_URL`, `MM_NTFY_URL`,
`MM_PANEL_URL`, `MM_PUBLIC_URL`; `src/settings.py:23-43`) y no están en el repo salvo como
valores de ejemplo. Cómo darlos de alta: `deploy/README.md`.

## Desarrollo

Requisitos, comandos (`uv sync`, `uv run pytest`, `uv run mypy`, `uv run pip-audit`) y cómo
arrancar cada proceso en local: `README.md`, "Desarrollo". El modo demo con Docker, que
levanta un proxy de cabeceras y un Jellyfin falsos: `README.md`, "Probar en local", y
`demo.sh`.

## CI

Un solo workflow, `.github/workflows/image.yml`, en cada push a `develop` y `main` (no en
los PR):

1. `checks`: `uv sync --frozen`, `mypy`, `pytest -q` y `pip-audit`.
2. `demo`: arranca el modo demo, exige los cuatro chequeos de `/healthz` en verde, que el
   panel responda a través del proxy de cabeceras y que el manifiesto sea igual al
   esperado (`deploy/demo/manifest.expected.json`).
3. `build`: construye la imagen, la escanea con Trivy (bloquea con HIGH o CRITICAL que
   tengan arreglo) y la publica en `ghcr.io` con la etiqueta `staging` (`develop`) o
   `latest` (`main`).

## Despliegue

Fuera de este documento: [`deploy/README.md`](../deploy/README.md). En el repo solo hay
plantillas con valores de documentación (`deploy/README.md:3-6`).

## Observado

- La CI solo corre en push a `develop` y `main` (`.github/workflows/image.yml:3-7`): un
  PR no pasa mypy ni pytest hasta que se mergea.
