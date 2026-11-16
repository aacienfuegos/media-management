# Datos y autenticación

Dónde guarda media-management sus datos y cómo autentica cada uno de sus tres procesos
web, sacado del código. `src/` abrevia `src/media_management/`. Las columnas no se copian
aquí: el esquema completo está en `src/schema_main.sql` y `src/schema_public.sql`.

## Dos bases de datos

Dos SQLite en modo WAL, separadas por quién escribe (`src/db.py:41-58`,
`src/settings.py:63-69`):

| BD | Ruta bajo `MM_DATA_DIR` | Escriben | Leen | Por qué |
|---|---|---|---|---|
| `main.db` | `main/main.db` | panel, API (solo `last_used_at` de los tokens) y worker | los cuatro procesos | Catálogo, enlaces, concesiones, tokens, papelera y auditoría: lo que decide quién descarga qué |
| `public.db` | `public/public.db` | público, y el panel al resolver una solicitud | panel y público | Lo que generan los visitantes: tickets, descargas, intentos, solicitudes y eventos |

- **El público abre `main.db` en solo lectura**: por URI con `mode=ro` y con el directorio
  montado `:ro` en el contenedor, así que si cae no puede tocar enlaces, concesiones ni
  catálogo (`src/public/app.py:23-47`, `compose.yaml`, servicio `public`). Mantiene una
  conexión abierta para que los ficheros `-wal`/`-shm` no desaparezcan
  (`src/public/app.py:26-28`); el worker hace lo mismo desde el lado que escribe
  (`src/worker.py:155-157`).
- **El panel escribe en `public.db`** solo el estado de una solicitud al aprobarla o
  rechazarla (`src/panel/requests_routes.py:92-94`, `src/panel/requests_routes.py:102-104`).
  La concesión se escribe antes en `main.db`, que es la fuente de verdad
  (`src/panel/requests_routes.py:80-91`).
- **Las referencias entre las dos BD son solo identificadores**: `link_id`, `grant_id` y
  `link_file_id` en `public.db`, y `request_id` en `grants`, no tienen clave foránea
  (`src/schema_public.sql`, `src/schema_main.sql:64-73`). Dentro de cada BD, las claves
  foráneas se activan en las conexiones de escritura (`src/db.py:37-38`).
- Fechas en texto ISO UTC con `Z`, sin microsegundos (`src/db.py:11-27`).

## Tablas por grupo

### `main.db` (`src/schema_main.sql`)

| Grupo | Tablas | Relaciones a alto nivel | Línea |
|---|---|---|---|
| Catálogo | `files` | Una fila por fichero de cada raíz, única por raíz y ruta relativa; `present` marca si sigue en disco. Guarda los metadatos tal cual se leen y la fecha efectiva con su origen | 1-24 |
| Enlaces | `links`, `link_files`, `link_zips` | Un enlace tiene varios `link_files` (cada uno apunta a un `files` y guarda la miniatura ya limpia) y como mucho un `link_zips` | 26-62 |
| Accesos | `grants`, `api_tokens` | Una concesión por solicitud aprobada, ligada a un enlace; tokens de integración, con su hash | 64-83 |
| Operación | `trash_entries`, `audit`, `state` | Entradas de papelera que apuntan a `files`; registro de cambios; pares clave-valor del worker (último escaneo, latido, manifiesto, comparación, escaneo pedido, problema de media) | 85-114 |

### `public.db` (`src/schema_public.sql`)

| Grupo | Tablas | Relaciones a alto nivel | Línea |
|---|---|---|---|
| Accesos | `tickets`, `ticket_zip_entries`, `auth_attempts`, `access_requests` | Un ticket por descarga iniciada, de un fichero o del ZIP (`link_file_id` 0, `src/public/routes.py:33-34`), con lo que contenía el ZIP en ese momento; intentos de credencial por enlace e IP; solicitudes con el hash del código | 1-18, 33-59 |
| Descargas | `downloads` | Una fila por petición servida de un ticket (también cada `Range`) | 20-31 |
| Operación | `events` | Registro del proceso público; el panel lo muestra junto a `audit` (`src/panel/admin_routes.py:53-89`) | 61-71 |

## Migraciones

No hay. Cada proceso aplica su `.sql` al arrancar con `CREATE TABLE IF NOT EXISTS` y
`CREATE INDEX IF NOT EXISTS` (`src/db.py:41-58`; panel en `src/panel/app.py:27-31`, API
en `src/api/app.py:19-22`, público en `src/public/app.py:60-65`, worker en
`src/worker.py:154`). En `src/` no hay ningún `ALTER TABLE` ni `PRAGMA user_version`: un
cambio en una tabla existente no se aplica a una BD ya creada.

## Secretos y lo que se guarda de ellos

Nombres de variable tal como están en `.env.example`; los valores nunca están en el repo.

| Secreto | Variable | Proceso | Qué se guarda | Fuente |
|---|---|---|---|---|
| Clave de tickets | `MM_TICKET_KEY` | público | nada (firma HMAC) | `src/settings.py:18-21`, `src/security.py:70-89` |
| Clave CSRF | `MM_CSRF_KEY` | panel | nada (firma HMAC) | `src/settings.py:18-21`, `src/security.py:117-122` |
| Token de ntfy | `MM_NTFY_TOKEN` | público | nada | `src/settings.py:43`, `src/notify.py:22-23` |
| Token de enlace | (generado) | | SHA-256 en `links.token_hash` | `src/security.py:19-24`, `src/panel/links_routes.py:69-76` |
| Token de API | (generado) | | SHA-256 en `api_tokens.token_hash` | `src/panel/admin_routes.py:27-31` |
| Contraseña de enlace | (la elige el dueño) | | Argon2id en `links.password_hash` | `src/security.py:16`, `src/security.py:32-40` |
| Código de solicitud | (generado) | | SHA-256 de `<link_id>:<código>` en `access_requests.code_hash` | `src/security.py:43-59` |

Las claves tienen que tener al menos 32 caracteres o el proceso no arranca
(`src/settings.py:84-87`). En logs y auditoría, un token solo aparece como los 12
primeros caracteres de su hash (`src/security.py:27-29`, `src/logs.py:47-49`).

## Autenticación

Tres flujos separados, uno por proceso. Ninguno comparte código de identidad con otro
(ver `tecnico.md`, "Por qué están separados").

### Panel

1. **Origen de confianza.** La petición tiene que venir de una IP de
   `MM_TRUSTED_PROXIES` (el proxy de autenticación); si no, 403. uvicorn no reescribe la
   IP del cliente (`src/panel/deps.py:83-91`, `src/cli.py:21-27`).
2. **Usuario del proxy.** El usuario sale de la cabecera `x-authentik-username` y tiene
   que estar en `MM_PANEL_ALLOWED_USERS`, que compara el nombre de usuario, no el nombre
   visible ni el correo; si no, 403 (`src/panel/deps.py:92-96`).
3. **Todas las páginas** dependen de esa identidad (`User`), salvo `/healthz`
   (`src/panel/deps.py:99`, `src/panel/app.py:63-66`). Las páginas de error del panel solo
   se pintan para quien la pasa (`src/panel/app.py:42-61`).
4. **CSRF en todo POST** (`CsrfUser`, `src/panel/deps.py:125-141`):
   - Si viene `Sec-Fetch-Site`, tiene que ser `same-origin` o `none`; si falta, se da
     por buena (líneas 129-130).
   - Si viene `Origin`, su host tiene que ser el de `Host` (líneas 131-133). Para que el
     navegador mande `Origin` real, el panel usa `Referrer-Policy: same-origin`
     (`src/panel/app.py:37-39`).
   - El formulario lleva `csrf`, un HMAC del usuario con `MM_CSRF_KEY`, comparado en
     tiempo constante (líneas 134-137; `src/security.py:117-122`). El token no caduca:
     depende solo del usuario y de la clave.
5. **IP para el registro**: la última de `X-Forwarded-For`, la que añade el proxy de
   confianza (`src/panel/deps.py:102-108`).

### API

1. Solo `Authorization: Bearer <token>`; sin él, 401 con `WWW-Authenticate: Bearer`
   (`src/api/deps.py:19-25`).
2. El token se busca por su SHA-256 en `api_tokens` y tiene que no estar revocado; cada
   uso actualiza `last_used_at` (`src/api/deps.py:26-34`).
3. **Solo lectura**: las cuatro rutas de `/api/v1` son GET (`src/api/routes.py:64-116`).
   `/healthz` no pide token (`src/api/app.py:30-34`), y tampoco el OpenAPI en
   `/api/v1/openapi.json` (`src/api/app.py:24-25`): solo las rutas de `src/api/routes.py`
   dependen de `TokenId`.
4. Los tokens se crean y se revocan en el panel, y el token en claro se enseña una sola
   vez (`src/panel/admin_routes.py:23-50`). Los tokens no caducan; solo se revocan
   (`src/schema_main.sql:75-83`).
5. `/api/v1` no tiene limitación de peticiones, ni en `src/` ni en las plantillas de
   `deploy/` (ver "Observado").

### Página pública

1. **El token del enlace va en el fragmento** (`#<token>`), que el navegador no envía; la
   página lo manda en el cuerpo de un POST, sin cookies (`credentials: "omit"`)
   (`src/public/static/share.js:1-5`, `share.js:9-29`). El servidor valida su forma
   (`src/public/routes.py:31`, `src/public/routes.py:37-40`) y busca el enlace por el hash
   (`src/links.py:37-44`).
2. **Enlace inexistente, caducado o revocado** dan la misma respuesta 404
   (`src/links.py:37-44`, `src/public/routes.py:96-100`).
3. **Según el modo** (`src/public/credentials.py:37-47`):
   - **Abierto**: concedido con la caducidad del enlace.
   - **Contraseña**: Argon2id, verificada fuera del bucle de eventos; un fallo da 401
     `bad_credentials` y queda en `auth_attempts` y en `events`
     (`src/public/credentials.py:50-62`).
   - **Solicitud**: el visitante pide acceso con nombre y nota (texto plano acotado a 80 y
     500 caracteres) y recibe un código de 10 caracteres de un alfabeto sin ambiguos, que
     se enseña una vez (`src/public/routes.py:255-295`, `src/security.py:13-14`,
     `src/security.py:43-48`). Hasta 5 solicitudes por IP y hora y 20 pendientes por
     enlace (`src/settings.py:56-57`). Con el código: si hay concesión vigente, concedido
     hasta la menor de las dos caducidades; si la solicitud sigue pendiente, 202; si está
     rechazada, revocada, caducada o no existe, el mismo 401 en todos los casos
     (`src/public/credentials.py:65-95`).
4. **Tickets firmados.** Cada fichero (o el ZIP) se pide con un ticket HMAC ligado al
   enlace, al fichero, a la concesión y, en el ZIP, a su versión
   (`src/security.py:74-114`, `src/public/routes.py:134-172`). Dura 4 horas o hasta que
   caduque el enlace o la concesión, lo que llegue antes (`src/settings.py:52`,
   `src/public/routes.py:157`, `src/public/credentials.py:89`).
5. **Revalidación en cada petición.** `/download/{ticket}` vuelve a comprobar la firma, la
   caducidad, que el enlace sigue activo, que la concesión sigue vigente y, en el ZIP, que
   la versión es la misma; también en cada reanudación con `Range`. Revocar corta las
   peticiones siguientes, no una transferencia ya en curso (`src/public/routes.py:195-239`).
6. **Bloqueo por IP**, nunca por enlace: 10 fallos de credencial en 15 minutos desde una
   IP bloquean esa IP en la verificación de credenciales de cualquier enlace
   (`src/public/guard.py:29-38`, `src/public/credentials.py:40-41`,
   `src/settings.py:58-59`). No se consulta en `/api/request` (`src/public/routes.py:255-295`)
   ni en los enlaces abiertos.
7. **Retardo por enlace**: 0,5 s por fallo reciente en ese enlace, hasta 10 s, antes de
   comprobar la credencial (`src/public/guard.py:41-50`, `src/settings.py:60-61`).
8. **IP real**: solo se cree `X-Real-IP` si la conexión viene de `MM_TRUSTED_NGINX`; si no,
   la del socket (`src/public/guard.py:12-22`).
9. **En el navegador**, la contraseña o el código viven solo en memoria y se olvidan al
   salir de la página (`src/public/static/share.js:1-7`, `share.js:353-354`).
10. Un error de validación responde `bad_request` sin repetir la entrada, que lleva el
    token y la credencial (`src/public/app.py:84-87`).

## Observado

No se corrige en este cambio.

- **Mismo nombre, distinto dato.** `files` tiene una columna `captured_at_utc` (lo que dicen
  los metadatos) y otra `date_utc` (la fecha efectiva, que puede salir de EXIF más un
  desfase) (`src/schema_main.sql:9-13`, `src/media_meta.py:146-155`). La API publica
  `date_utc` con el nombre `captured_at_utc` y `date_source` como `captured_at_source`
  (`src/api/routes.py:54-56`).
- **Sin retención.** En `src/` no hay ningún borrado de `tickets`, `downloads`,
  `auth_attempts`, `access_requests`, `events` ni `audit`, que guardan IP y, algunas
  (`tickets`, `downloads`, `access_requests`), navegador: las tablas crecen sin límite. Pendiente de confirmar si se purgan fuera de la app.
- **API sin limitación de peticiones.** `API_RATE` es del nginx del público y limita su
  `/api/` (`/api/share`, `/api/ticket`, `/api/zip`, `/api/request`), no `/api/v1`
  (`deploy/nginx/share.conf.template:10,25,54-55`; `.env.example` lo pone en la sección de
  nginx delante del público). El router de la API en `deploy/traefik/media.example.yml` no
  tiene `middlewares`, y en `src/` no hay ningún límite.
- **Sin migraciones** (ver "Migraciones"): cualquier cambio de columna en una tabla
  existente necesita un paso manual que hoy no está en el repo.
