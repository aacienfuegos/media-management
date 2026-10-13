# Pantallas

Las interfaces de media-management tal como están en el código. `src/` abrevia
`src/media_management/`; las plantillas del panel están en `src/panel/templates/` y se
citan por su nombre.

No hay app Android ni iOS. Hay dos interfaces web independientes, con hojas de estilo y
JavaScript propios: el **panel** (administración, plantillas Jinja) y la **página
pública** (quien recibe un enlace, una sola página pintada con JavaScript). La **API** no
tiene interfaz: sus rutas están en la tabla de `README.md`, "API".

## Panel

### Estructura común

Todas las páginas extienden `base.html`:

- **Cabecera fija** con la marca (enlace a `/`), cuatro pestañas y el menú "Más"
  (`base.html:15-40`). Una pestaña se marca como actual con `aria-current="page"` si la
  ruta coincide o empieza por ella; Biblioteca también se marca en `/files/...`
  (`base.html:18-20`).
- **Pestañas**: Biblioteca (`/library`), Enlaces (`/links`), Solicitudes (`/requests`) y
  Papelera (`/trash`) (`base.html:19-24`).
- **Menú "Más"**: Manifiesto (`/manifest`), Tokens API (`/tokens`), Registro (`/audit`),
  botón de atajos de teclado, selector de tema Sistema/Claro/Oscuro y el usuario de la
  sesión (`base.html:25-39`). El menú se cierra al pulsar fuera
  (`src/panel/static/panel.js:227-231`).
- **Aviso tras una acción** (`flash`): la redirección lleva `?hecho=<clave>` y el panel
  pinta el texto fijo de esa clave; la URL nunca lleva el texto
  (`src/panel/deps.py:144-160`, `base.html:42`). El JavaScript quita `hecho` de la URL
  después de pintarlo (`src/panel/static/panel.js:276-280`).
- **Enlace "Saltar al contenido"** (`base.html:14`).
- **Errores**: un 404 en GET, o un 500, se pinta con la barra del panel
  (`error.html`) solo para quien ya pasa la identidad; a los demás, la respuesta por
  defecto o un texto plano (`src/panel/app.py:42-61`).
- Las acciones destructivas piden confirmación del navegador con `data-confirm`
  (`src/panel/static/panel.js:254-256`).
- Todas las acciones POST llevan el campo oculto `csrf` (ver `datos.md`, "Panel").

### Atajos de teclado

Definidos en `src/panel/static/panel.js:197-233` y listados en el diálogo de
`base.html:45-57`:

| Atajo | Acción |
|---|---|
| `/` | Foco en la búsqueda; si la página no tiene, va a `/library#buscar` |
| `g` y luego `b`, `e`, `s` o `p` (en menos de 1,2 s) | Biblioteca, Enlaces, Solicitudes o Papelera |
| `?` | Abre el diálogo de atajos |
| `Esc` | Sale de la búsqueda o quita la selección de ficheros |
| `Mayús` + clic | Selecciona un rango de ficheros en la Biblioteca (`panel.js:62-66`) |

No se disparan mientras se escribe en un campo ni con `Ctrl`, `Cmd` o `Alt`
(`panel.js:205-212`).

### Mapa

| Ruta (GET) | Pantalla | Desde dónde se llega | Fuente |
|---|---|---|---|
| `/` | Inicio | Marca de la cabecera | `src/panel/app.py:68-97`, `home.html` |
| `/library` | Biblioteca | Pestaña, `g b`, búsqueda de Inicio, totales por raíz | `src/panel/catalog_routes.py:32-87`, `library.html` |
| `/files/{id}` | Ficha de fichero | Fila de la Biblioteca, de un enlace o de la papelera | `src/panel/catalog_routes.py:90-105`, `file.html` |
| `/thumb/{id}` | (imagen, no pantalla) | `<img>` de Biblioteca, ficha, enlace y papelera | `src/panel/catalog_routes.py:122-139` |
| `/files/{id}/rename` | Renombrar | Botón de la ficha | `src/panel/trash_routes.py:63-68`, `rename.html` |
| `/files/{id}/trash` | Confirmar papelera | Botón de la ficha | `src/panel/trash_routes.py:31-35`, `trash_confirm.html` |
| `/links` | Enlaces | Pestaña, `g e`, tarjeta de Inicio | `src/panel/links_routes.py:106-122`, `links.html` |
| `/links/new` | Nuevo enlace | Barra de selección de la Biblioteca o botón de la ficha | `src/panel/links_routes.py:38-45`, `link_new.html` |
| `/links/{id}` | Detalle de enlace | Fila de Enlaces, ficha, solicitudes, registro | `src/panel/links_routes.py:125-168`, `link.html` |
| `/requests` | Solicitudes | Pestaña, `g s`, tarjeta de Inicio, aviso en Enlaces | `src/panel/requests_routes.py:33-52`, `requests.html` |
| `/requests/{id}` | Solicitud | "ver detalles" en Solicitudes, "Resolver" en el enlace | `src/panel/requests_routes.py:55-67`, `request.html` |
| `/trash` | Papelera | Pestaña, `g p` | `src/panel/trash_routes.py:97-101`, `trash.html` |
| `/manifest` | Manifiesto | Menú "Más", fila de Inicio | `src/panel/catalog_routes.py:142-148`, `manifest.html` |
| `/manifest/compare` | Comparación | "ver" en Inicio, migas de Manifiesto | `src/panel/catalog_routes.py:151-156`, `compare.html` |
| `/tokens` | Tokens API | Menú "Más" | `src/panel/admin_routes.py:16-20`, `tokens.html` |
| `/audit` | Registro | Menú "Más", "Credenciales fallidas" del enlace | `src/panel/admin_routes.py:53-89`, `audit.html` |
| `/healthz` | (JSON, no pantalla) | Monitorización; sin identidad | `src/panel/app.py:63-66` |

### Inicio (`/`)

Muestra: aviso si la biblioteca no está disponible; búsqueda que manda a `/library?q=`;
tarjetas de solicitudes pendientes (resaltada si hay alguna) y enlaces activos; una
tarjeta por raíz con ficheros, tamaño y etiquetas (compartible, manifiesto, sincronizada,
exige 2ª copia); y una tabla de estado: último escaneo, biblioteca montada, worker,
Jellyfin, export de IDs, manifiesto y comparación (`home.html:4-69`).

| Acción | POST | Redirige a | Aviso |
|---|---|---|---|
| Escanear ahora | `/scan` | `/?hecho=scan` | "Escaneo pedido. El worker lo hará en su próxima vuelta." |

Fuente: `src/panel/app.py:99-104`, `src/panel/deps.py:147`.

### Biblioteca (`/library`)

Muestra: migas (Biblioteca / raíz / carpetas), título con número de ficheros y tamaño,
etiquetas de la raíz (exige segunda copia, nombres fijos), selector de raíz, búsqueda por
nombre en la carpeta actual (no entra en subcarpetas), filtro por tipo, carpetas como
chips, tabla paginada de 200 y estados vacíos según haya filtro o subcarpetas
(`library.html:9-99`). Las cabeceras ordenan en el servidor; la casilla solo aparece en
raíces compartibles (`library.html:6-8`, `library.html:54`, `library.html:69`).

Acciones:

- **Seleccionar ficheros**: la selección se guarda en `sessionStorage` y sobrevive al
  cambio de página; la barra inferior dice cuántos hay y cuántos "en otras páginas"
  (`src/panel/static/panel.js:5-95`). Hacer clic en la fila también marca la casilla
  (`panel.js:70-74`).
- **Crear enlace de descarga**: GET a `/links/new` con los `file_id` seleccionados
  (`library.html:50`, `library.html:104`).
- **Quitar selección** (botón o `Esc`).
- Sin JavaScript, el filtro de tipo se aplica con el botón "Aplicar"; con JavaScript, al
  cambiar el selector (`library.html:40`, `panel.js:258-262`).

### Ficha de fichero (`/files/{id}`)

Muestra: migas, nombre, miniatura grande, enlaces que lo incluyen con su modo y estado, y
una tabla con raíz, ruta, tipo, tamaño, fecha de captura (Madrid y UTC), origen de la
fecha, duración, resolución, estado en Jellyfin, error de metadatos y primera y última
vez visto (`file.html:5-64`). Si el fichero ya no está en disco, un aviso y ninguna
acción; si no se puede mover a la papelera, los motivos (`file.html:21-26`).

Acciones (solo si el fichero está presente, `file.html:11-19`):

- **Crear enlace** (si la raíz es compartible): GET a `/links/new?file_id=<id>`.
- **Renombrar** (si la raíz es `renamable` y no `synced`): a `/files/{id}/rename`.
- **Mover a la papelera** (si no hay motivos en contra): a `/files/{id}/trash`.

### Renombrar (`/files/{id}/rename`)

Formulario con el nombre actual, con la parte antes de la extensión seleccionada
(`rename.html`, `panel.js:264-268`). Si la raíz no permite renombrar, 403
(`src/panel/trash_routes.py:66-67`).

| Acción | POST | Resultado |
|---|---|---|
| Renombrar | `/files/{id}/rename` | Redirige a `/files/{id}?hecho=renamed`, aviso "Fichero renombrado." |
| Nombre no válido | | Misma pantalla con 400 y el motivo |
| Ya existe o el fichero cambió | | Misma pantalla con 409 |

Fuente: `src/panel/trash_routes.py:71-94`, `src/panel/deps.py:159`.

### Confirmar papelera (`/files/{id}/trash`)

Miniatura, nombre, tamaño y raíz. Si hay motivos para no borrar, los lista con "Volver";
si no, explica que el fichero se mueve fuera de la biblioteca y se puede restaurar, y en
raíces sincronizadas avisa de que no se volverá a subir (`trash_confirm.html:13-26`).

| Acción | POST | Resultado |
|---|---|---|
| Sí, mover a la papelera | `/files/{id}/trash` | Redirige a `/trash?hecho=trashed`, aviso "Fichero movido a la papelera. Se puede restaurar desde aquí." |
| Hay motivos en contra | | Misma pantalla con 409 y los motivos |
| Falla el movimiento | | Misma pantalla con 409 y el error |

Fuente: `src/panel/trash_routes.py:38-60`, `src/panel/deps.py:157`.

### Enlaces (`/links`)

Filtro Activos/Todos, filtro por título en el navegador y tabla ordenable en el navegador
con título, solicitudes pendientes, modo, número de ficheros, descargas (tickets), última
descarga, creación y caducidad; en "Todos", también el estado (`links.html`,
`panel.js:97-125`, `panel.js:240-248`). "Nuevo enlace" lleva a la Biblioteca, que es
donde se crean (`links.html:6`).

### Nuevo enlace (`/links/new`)

Sin ficheros seleccionados, un estado vacío que manda a la Biblioteca
(`link_new.html:7-13`). Con ficheros: título, modo (cualquiera con el enlace, con
contraseña o por solicitud), contraseña (solo en ese modo, mínimo 8), caducidad en días
con atajos 1 día, 1 semana, 1 mes y Máximo, casilla de ZIP si hay dos o más ficheros, y la
lista de ficheros con su tamaño total (`link_new.html:15-82`). Solo entran ficheros
presentes de raíces compartibles, sin repetir y hasta 500
(`src/panel/links_routes.py:20-35`).

| Acción | POST | Resultado |
|---|---|---|
| Crear enlace | `/links` | Pinta "Enlace creado" en la misma respuesta (sin redirección) |
| Sin ficheros, más días del máximo o contraseña corta | | Misma pantalla con 400 y el motivo |

Fuente: `src/panel/links_routes.py:48-94`.

**Enlace creado** (`link_created.html`): la URL completa, que solo se muestra esta vez,
con botón "Copiar enlace"; un mensaje listo para enviar con título, URL, fecha de
caducidad y cómo entrar según el modo; aviso de mandar la contraseña por otro canal; y
enlace al detalle. Al llegar aquí se vacía la selección de la Biblioteca
(`panel.js:236`).

### Detalle de enlace (`/links/{id}`)

Muestra: estado (activo, caducado o revocado), tarjetas de modo, caducidad, descargas
iniciadas y credenciales fallidas (con enlace al registro); aviso de solicitudes
pendientes; ficheros con descargas por fichero y si siguen en disco; en modo solicitud,
las solicitudes y las personas con acceso (con aviso "¿código reenviado?" si una
concesión se usa desde más de una IP); y las descargas, una fila por ticket, con IP,
navegador y peticiones (`link.html:4-115`). Panel lateral "Gestionar"
(`link.html:118-172`).

| Acción | POST | Redirige a | Aviso |
|---|---|---|---|
| Renovar / Reactivar (días) | `/links/{id}/renew` | `/links/{id}?hecho=renewed` | "Caducidad del enlace actualizada." |
| Revocar el enlace | `/links/{id}/revoke` | `/links/{id}?hecho=revoked` | "Enlace revocado. Ya no se puede abrir ni reanudar ninguna descarga." |
| Ofrecer un ZIP con todo | `/links/{id}/zip` (`action=enable`) | `/links/{id}?hecho=zip_enable` | "ZIP activado. Se generará en segundo plano." |
| Quitar el ZIP | `/links/{id}/zip` (`action=disable`) | `/links/{id}?hecho=zip_disable` | "ZIP retirado del enlace." |
| Regenerar el ZIP | `/links/{id}/zip` (`action=regenerate`) | `/links/{id}?hecho=zip_regenerate` | "El ZIP se volverá a generar." |
| Revocar el acceso de una persona | `/grants/{id}/revoke` | `/links/{id}?hecho=grant_revoked` | "Acceso de esa persona revocado." |
| Resolver una solicitud | (GET) | `/requests/{id}` | |

Fuente: `src/panel/links_routes.py:171-216`, `src/panel/requests_routes.py:111-121`,
`src/panel/deps.py:148-155`. La sección ZIP solo aparece con dos o más ficheros y muestra
el estado: desactivado, listo, generándose, en cola, no se genera o ha fallado
(`link.html:140-162`).

### Solicitudes (`/requests`)

Aviso de que el nombre y la nota son texto de un desconocido. Pendientes (no caducadas)
con nombre, enlace, nota, cuándo, IP y "ver detalles"; y una tabla de recientes (aprobadas,
rechazadas o caducadas sin respuesta, hasta 100) (`requests.html`,
`src/panel/requests_routes.py:33-52`). Las solicitudes caducan a los 7 días por defecto
(`src/settings.py:55`).

| Acción | POST | Redirige a | Aviso |
|---|---|---|---|
| Aprobar (días; por defecto lo que le queda al enlace) | `/requests/{id}/approve` | `/links/{id_enlace}?hecho=approved` | "Solicitud aprobada. Ya puede descargar con su código." |
| Rechazar | `/requests/{id}/reject` | `/requests?hecho=rejected` | "Solicitud rechazada." |

Fuente: `src/panel/requests_routes.py:70-108`, `src/panel/deps.py:153-154`. Si el enlace ya
no está activo o no es de modo solicitud, en lugar de "Aprobar" aparece "enlace inactivo"
(`requests.html:18-28`).

### Solicitud (`/requests/{id}`)

Solo para solicitudes pendientes y no caducadas; si no, 404
(`src/panel/requests_routes.py:25-30`). Muestra nombre ("Dice llamarse"), nota, cuándo,
IP con el número de solicitudes más desde esa IP, navegador y enlace, y los mismos botones
Aprobar y Rechazar que la lista (`request.html`).

### Papelera (`/trash`)

Las últimas 500 entradas con fichero, raíz, tamaño, cuándo y quién, y estado (en la
papelera, restaurado o vaciado) (`trash.html`, `src/panel/trash_routes.py:97-101`).

| Acción | POST | Resultado |
|---|---|---|
| Restaurar | `/trash/{id}/restore` | Redirige a `/trash?hecho=restored`, aviso "Fichero restaurado a su sitio." |
| Ya hay otro fichero con ese nombre | | Misma pantalla con 409 y el error |

Fuente: `src/panel/trash_routes.py:104-126`, `src/panel/deps.py:158`.

### Manifiesto (`/manifest`) y Comparación (`/manifest/compare`)

Manifiesto: estado de la última pasada (escrito o por qué no), clips fuera por falta de
fecha, nombres fuera del formato de la cámara y la tabla de clips, filtrable y ordenable
en el navegador (`manifest.html`). Comparación: resultado clip a clip contra el
manifiesto anterior, o por qué no hay comparación (`compare.html`). Sin acciones.

### Tokens API (`/tokens`)

Explicación de que son de solo lectura, formulario con el nombre de la integración y la
tabla con creación, último uso y estado (`tokens.html`).

| Acción | POST | Resultado |
|---|---|---|
| Crear token | `/tokens` | Pinta la misma pantalla con el token nuevo, que solo se muestra esta vez (sin redirección) |
| Revocar | `/tokens/{id}/revoke` | Redirige a `/tokens?hecho=token_revoked`, aviso "Token revocado." |

Fuente: `src/panel/admin_routes.py:23-50`, `src/panel/deps.py:156`.

### Registro (`/audit`)

Tabla de 100 entradas por página que mezcla `audit` de `main.db` y `events` de
`public.db`, con cuándo, quién (o "público"), acción, sobre qué, resultado, IP y detalle;
filtro por acción agrupado por categorías (`src/panel/admin_routes.py:53-89`,
`src/panel/audit_labels.py`, `audit.html`). Sin acciones.

## Página pública

Una sola URL (`/`) que sirve `index.html`; todo lo demás lo pinta el JavaScript en
`<main id="app">` (`src/public/app.py:74-76`, `src/public/static/index.html:16-18`). Las
pantallas son tarjetas que sustituyen a la anterior y ponen el foco en el título o en el
primer campo (`src/public/static/share-ui.js:47-55`).

### Estados

| Estado | Cuándo | Qué ofrece | Fuente (`share.js` salvo indicación) |
|---|---|---|---|
| Sin JavaScript | El navegador no ejecuta JS | Aviso de que hace falta | `index.html:17` |
| Enlace incompleto | El fragmento no tiene forma de token | Explica que falta lo que va tras `#` | `share.js:343-346` |
| Cargando | Mientras responde `/api/share` | Indicador | `share-ui.js:67-70` |
| Contraseña | 401 con `need: password` | Campo con "Mostrar/Ocultar"; si falla, mensaje de error | `share.js:43`, `share.js:58-77` |
| Pedir acceso | 401 con `need: code` | Nombre y mensaje opcional; "Ya tengo un código" abre el campo de código | `share.js:44`, `share.js:90-115` |
| Código incorrecto | 401 con `need: code` y `bad_credentials` | El formulario de código abierto con error | `share.js:79-88`, `share.js:109` |
| Código nuevo | La solicitud se acepta | El código (`XXXXX-XXXXX`), "Copiar el código", aviso de que no se puede recuperar y "Ya lo he guardado" | `share.js:127-148` |
| Solicitud pendiente | 202 con `status: pending` | "Comprobar de nuevo" | `share.js:42`, `share.js:150-155` |
| Demasiadas solicitudes / cola llena | 429 en `/api/request` | Mensaje y "Volver" | `share.js:121-122` |
| Lista de ficheros | 200 | Quién comparte, título, número y tamaño total, caducidad en hora de Madrid, una fila por fichero | `share.js:193-213`, `share-ui.js:83-87` |
| Un solo fichero | 200 con un fichero | Miniatura grande y botón ancho; el nombre se omite si coincide con el título | `share.js:215-226` |
| Sin ficheros | 200 con la lista vacía | "Este enlace ya no tiene archivos disponibles." | `share.js:206-207` |
| Vista previa | Clic en una miniatura | Diálogo con la imagen, nombre, tipo, tamaño, "Cerrar" y "Descargar" | `share.js:159-191` |
| Descargando | Clic en "Descargar" | "Preparando…", luego "Descarga iniciada" y el botón pasa a "Otra vez" | `share.js:318-341` |
| Descargar todos (sin ZIP) | Más de un fichero y sin ZIP | Uno a uno, con un segundo entre cada uno; se para en el primero que falla | `share.js:228-249` |
| ZIP listo | `zip.state: ready` | "Descargar todo en un solo archivo"; los sueltos quedan como enlace secundario | `share.js:259-263`, `share.js:302-316` |
| ZIP preparándose | `zip.state: pending` | Botón desactivado; vuelve a preguntar a los 15 s doblando hasta 120 s, 8 veces; después, botón para comprobar a mano | `share.js:253-255`, `share.js:264-272` |
| ZIP no disponible | El ZIP desaparece tras ofrecerse | "El archivo con todo no está disponible. Descárgalos uno a uno." | `share.js:273-274` |
| Enlace no disponible | 404 (no existe, caducado o revocado: no se distingue) | Mensaje | `share.js:45`, `src/links.py:37-44` |
| Demasiados intentos | 429 al abrir | Mensaje y "Reintentar" | `share.js:46` |
| Error de red u otro | Cualquier otra respuesta | Mensaje y "Reintentar" | `share.js:47` |
| Error en una descarga | 404 o 429 al pedir el ticket | Texto en la fila del fichero | `share.js:295-300` |

Si una descarga o el ZIP responden 401 o 202 (la credencial ya no vale o la solicitud
volvió a pendiente), la página vuelve a abrir el enlace desde el principio
(`share.js:314`, `share.js:335-338`). Al salir de la página se olvidan la contraseña y el
código, y una página restaurada desde la caché del historial se recarga
(`share.js:350-354`).

## API

Sin interfaz. Rutas, parámetros y autenticación: `README.md`, "API", y
`src/api/routes.py`.

## Observado

- En el panel, los errores de los POST que no pintan su propia pantalla (por ejemplo, 409
  al aprobar una solicitud ya aprobada o un enlace inactivo, 400 por días por encima del
  máximo, 403 al renombrar en una raíz que no lo permite) salen como el JSON por defecto
  de FastAPI y no con la barra del panel: el manejador solo cubre 404 en GET y 500
  (`src/panel/app.py:42-61`, `src/panel/requests_routes.py:76-79`,
  `src/panel/links_routes.py:178-179`, `src/panel/trash_routes.py:77-80`).
- El formulario de renovar de `link.html:129` propone siempre 7 días (valor fijo en la
  plantilla), no `MM_LINK_DEFAULT_DAYS`, que sí usa el de nuevo enlace
  (`src/panel/links_routes.py:44`).
- Crear un token y crear un enlace responden con la página y no redirigen
  (`src/panel/admin_routes.py:37`, `src/panel/links_routes.py:93-94`): recargar esa
  página reenvía el POST y crea otro. Pendiente de confirmar si es intencionado para
  poder mostrar el secreto una sola vez.
