# Producto

Qué es media-management y qué hace hoy, sacado del código. Cada afirmación remite al
fichero (y a la línea cuando ayuda) de donde sale; las rutas son relativas a la raíz del
repo y `src/` abrevia `src/media_management/`. Lo que el código no permite confirmar
lleva la marca "Pendiente de confirmar".

Documentos hermanos: [`tecnico.md`](tecnico.md), [`pantallas.md`](pantallas.md),
[`diseno.md`](diseno.md) y [`datos.md`](datos.md).

## Qué es

Un panel para gestionar una biblioteca de media personal (vídeos y fotos) y compartir
ficheros con enlaces de descarga, pensado para un homelab (`README.md`, primer párrafo;
`pyproject.toml`, `description`). La interfaz está en español (`README.md`; todas las
plantillas de `src/panel/templates/` llevan `lang="es"` desde `base.html:2`).

No crea ni sube media: trabaja sobre ficheros que ya están en disco, en las "raíces"
declaradas en un TOML (`src/roots.py:45-74`, ejemplo en `deploy/roots.example.toml`).

## Para quién

- **El dueño del panel.** Entra por el proxy de autenticación (Traefik con forward-auth de
  Authentik: `deploy/traefik/media.example.yml`, `deploy/README.md`, "Traefik") y solo si su nombre de usuario está en la lista
  `MM_PANEL_ALLOWED_USERS` (`src/panel/deps.py:83-96`). Es quien cataloga, crea enlaces,
  aprueba solicitudes y mueve ficheros a la papelera.
- **Quien recibe un enlace.** No tiene cuenta: abre una URL con el token en el fragmento
  y, según el modo del enlace, escribe una contraseña o pide acceso con su nombre
  (`src/public/static/share.js`, `src/public/credentials.py:37-47`).
- **Otras apps (integraciones).** Leen el catálogo y el manifiesto por la API con un
  token de solo lectura creado en el panel (`src/api/deps.py:19-34`,
  `src/panel/templates/tokens.html:5`). El manifiesto v1 es un contrato con TripPlanner
  (`src/manifest.py:20-21`, `src/worker.py:29`).

## Funcionalidades por proceso

El paquete tiene cuatro procesos: tres web (`panel`, `api`, `public`) y el `worker`
(`src/cli.py:7-11`, `src/cli.py:47`). El detalle de cada pantalla está en
[`pantallas.md`](pantallas.md).

### Panel (administración)

- **Inicio**: búsqueda, solicitudes pendientes, enlaces activos, totales por raíz y estado
  del servicio (biblioteca montada, worker, Jellyfin, export de IDs, manifiesto). Botón
  para pedir un escaneo inmediato (`src/panel/app.py:68-104`,
  `src/panel/templates/home.html`).
- **Biblioteca**: listado de ficheros presentes de todas las raíces o de una, con
  navegación por carpetas, búsqueda por nombre, filtro por tipo, orden por columna y
  paginación de 200 (`src/panel/catalog_routes.py:15-87`). Muestra miniatura, tamaño,
  fecha de captura en hora de Madrid, duración, resolución y estado en Jellyfin
  (`src/panel/templates/library.html:53-80`).
- **Ficha de fichero**: metadatos, origen de la fecha, enlaces que lo incluyen y
  acciones (crear enlace, renombrar, mover a la papelera) según las propiedades de la
  raíz (`src/panel/catalog_routes.py:90-105`, `src/panel/templates/file.html`).
- **Miniaturas**: las sirve el panel desde una caché en disco que se llena desde
  Jellyfin; si no hay, un SVG con el icono del tipo (`src/panel/catalog_routes.py:122-139`,
  `src/thumbs.py:43-45`).
- **Enlaces de descarga**: se crean seleccionando ficheros de raíces compartibles (hasta
  500, `src/panel/links_routes.py:20`), con título, modo (abierto, contraseña o
  solicitud), caducidad obligatoria (por defecto 7 días, máximo 90:
  `src/settings.py:53-54`) y ZIP opcional si hay al menos dos ficheros
  (`src/zips.py:38`). La URL se enseña una sola vez (`src/panel/links_routes.py:48-94`,
  `src/panel/templates/link_created.html:9`). Después se puede renovar, revocar y
  activar, quitar o regenerar el ZIP (`src/panel/links_routes.py:171-216`).
- **Registro de descargas por enlace**: cada ticket emitido, sus peticiones (también las
  reanudaciones), IP y navegador, y en modo solicitud a qué persona corresponde
  (`src/panel/links_routes.py:125-168`, `src/panel/templates/link.html:94-115`).
- **Solicitudes de acceso**: lista de pendientes y recientes, ficha de cada solicitud con
  cuántas más vienen de la misma IP, aprobar con días de acceso o rechazar; revocar el
  acceso de una persona ya aprobada (`src/panel/requests_routes.py`).
- **Papelera**: mover un fichero a la papelera, restaurarlo y ver lo que el host ya ha
  vaciado (`src/panel/trash_routes.py`, `src/trash.py:1-8`). No se puede mover si la raíz
  no permite borrar o exige segunda copia (`src/panel/policy.py:6-18`).
- **Renombrar**: solo en raíces `renamable` y no `synced` (`src/panel/policy.py:21-22`).
- **Manifiesto**: el último manifiesto generado, clip a clip, con avisos de clips sin
  fecha y nombres fuera del patrón; y la comparación con el manifiesto anterior durante
  el traspaso (`src/panel/catalog_routes.py:142-156`).
- **Tokens API**: crear (se enseña una vez), ver último uso y revocar
  (`src/panel/admin_routes.py:16-50`).
- **Registro (auditoría)**: lo que cambia estado en panel, API y worker, junto con los
  eventos del proceso público, filtrable por acción (`src/panel/admin_routes.py:53-89`).

### Página pública (quien recibe el enlace)

Una sola página que se pinta con JavaScript (`src/public/static/index.html:16-18`,
`src/public/static/share.js`).

- Abrir el enlace no consume nada; se cuenta al pedir el ticket de un fichero
  (`src/public/routes.py:87-90`).
- Tres modos de acceso: abierto, contraseña o solicitud con aprobación y código personal
  (`src/public/credentials.py:37-95`).
- Lista de ficheros con miniatura, tipo y tamaño; vista previa ampliada de la miniatura;
  descarga de cada fichero; "Descargar todos" uno a uno o un ZIP con todo si el enlace lo
  ofrece (`src/public/static/share.js`, funciones `showFiles`, `preview`, `downloadAll`,
  `showZip`).
- Las descargas se pueden reanudar durante unas horas: el ticket dura 4 horas o menos si
  el enlace caduca antes (`src/settings.py:52`, `src/public/routes.py:157`).
- Quien comparte aparece con el nombre de `MM_SHARE_SENDER` si está configurado
  (`src/settings.py:37-38`, `src/public/routes.py:108`).
- Cada solicitud de acceso avisa por ntfy si está configurado (`src/notify.py:14-32`,
  `src/public/routes.py:294`).

### API (integraciones)

Solo lectura y solo con token Bearer (`src/api/deps.py:19-34`): raíces con totales,
ficheros presentes de una raíz paginados, detalle de un fichero y el manifiesto con
`ETag` (`src/api/routes.py:64-116`). La tabla de rutas está en `README.md`, "API"; el
OpenAPI se publica sin token en `/api/v1/openapi.json` (`src/api/app.py:24-25`).

### Worker (segundo plano)

- Escaneo periódico de las raíces (cada 900 s por defecto, `src/settings.py:30`) o
  cuando se pide desde el panel (`src/worker.py:172-180`). Lee metadatos según el perfil
  de la raíz (`dji` o `generic`, `src/roots.py:27`) y marca como desaparecido lo que ya
  no está (`src/catalog.py:68`, `src/catalog.py:118-122`).
- Si falta el centinela `.media-root` en la base de media, no escanea nada
  (`src/roots.py:119-130`, `src/worker.py:118-123`).
- Genera el manifiesto v1 de la única raíz `in_manifest`; si no sabe qué ha indexado
  Jellyfin, no lo escribe y conserva el anterior (`src/worker.py:26-63`).
- Precarga miniaturas de Jellyfin, hasta 400 por vuelta (`src/worker.py:23`,
  `src/worker.py:92-113`).
- Concilia la papelera: marca como vaciado lo que el host ya ha borrado
  (`src/trash.py:133-140`).
- Genera y borra los ZIP de los enlaces (`src/zips.py:1-15`, `src/worker.py:158`).
- Deja un latido en `main.db` que usa `/healthz` (`src/worker.py:117`,
  `src/health.py:18-20`).

## Lo que no hace

- No borra ficheros de la biblioteca: mueve a una papelera y el vaciado lo hace un timer
  del host (`src/trash.py:1-8`, `deploy/host/media-trash-purge.sh`). La única excepción
  son los ZIP de los enlaces, que el worker borra dentro de su directorio
  (`src/zips.py:14-15`).
- No sube ni edita media, ni transcodifica: lee metadatos y sirve los ficheros tal cual
  (`src/media_meta.py`, `src/public/routes.py:195-239`).
- No tiene cuentas para quien recibe un enlace ni recuperación de URL, código o token
  perdidos: se guardan hasheados (`src/panel/templates/link.html:121`,
  `src/public/static/share.js`, `showNewCode`).
- No tiene app móvil: las dos interfaces son web (ver [`pantallas.md`](pantallas.md)).

## Observado

- La papelera se vacía "a los 30 días" según los textos del panel
  (`src/panel/templates/trash.html:6`, `src/panel/templates/trash_confirm.html:19`). Ese
  plazo no lo controla la app: es el valor por defecto del script del host
  (`deploy/host/media-trash-purge.sh:17`). Pendiente de confirmar que el despliegue usa
  ese valor.
