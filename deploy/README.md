# Despliegue

Todo lo de esta carpeta son **plantillas**: los nombres de host, IPs y rutas que
aparecen son de documentación (`example.org`, `192.0.2.0/24`, `/ruta/a/...`). Los
valores reales van en `deploy/local/` (ignorado por git) o en `.env`.

## Qué corre

Una imagen, cinco contenedores (`compose.yaml`):

| Servicio | Puerto | Quién tiene que poder llegar | Qué es |
|---|---|---|---|
| `nginx` | 8080 | Traefik (router público) y Uptime Kuma | Delante del proceso público. Sirve los bytes de las descargas (`X-Accel-Redirect`) |
| `public` | — (solo red interna `share`) | solo nginx | Página del enlace, tickets, solicitudes de acceso. Sin admin, sin cookies |
| `panel` | 8002 | Traefik (router con forward-auth) y Uptime Kuma | Panel de administración |
| `api` | 8003 | Traefik (router sin forward-auth) y Uptime Kuma | `/api/v1`, solo tokens |
| `worker` | — | — | Escaneo, manifiesto, miniaturas, conciliación de la papelera |

Los tres puertos publicados **solo** desde Traefik, más Uptime Kuma para los `healthz`
(que va directo al backend). El panel solo acepta la identidad de Authentik si la
conexión viene de una IP de `MM_TRUSTED_PROXIES`: que Kuma llegue al puerto no le da
acceso a nada más que a `/healthz`.

> Comprueba que Docker conserva la IP de origen en los puertos publicados (con el
> proxy de espacio de usuario, la IP que ve el contenedor es la de la pasarela de
> Docker). Tras el alta: una petición al panel desde fuera de Traefik tiene que dar
> 403, y el registro del panel tiene que mostrar la IP real del cliente.

## Lista para el alta

### LXC

- LXC **sin privilegios** con Docker (`nesting=1`, `keyctl=1`).
- Bind mounts:
  - Directorio de media del host → en el LXC, **lectura-escritura** y **`optional`**.
    Si el disco cifrado no está desbloqueado, el LXC arranca igual y la app se niega a
    operar (ver centinela).
  - Directorio del manifiesto que lee TripPlanner → **lectura-escritura**. La app
    escribe ahí `buceo.candidate.json` (y, tras el traspaso, `buceo.json`).
  - Directorio del export de IDs de Jellyfin → **solo lectura**.
- Salidas: al `8096` de Jellyfin (miniaturas y `healthz`) y a ntfy (URL interna).

> **Solo el panel monta la biblioteca con escritura** (lo necesita para mover a la
> papelera y renombrar); worker, API, público y nginx la montan en solo lectura. Las
> primitivas de la app nunca borran ni sobrescriben, pero eso protege frente a fallos
> de lógica, no frente a ejecución de código en el proceso del panel: con escritura
> en el volumen, un proceso comprometido puede borrar directamente. El panel solo es
> alcanzable desde la LAN a través de Authentik, y la biblioteca debe tener otra copia.

### Centinela

Crear en la raíz del volumen de media, **dentro del volumen cifrado**:

```sh
touch /ruta/a/media/.media-root
```

Sin él, la app trata la biblioteca como no montada: no escanea, no escribe
manifiesto, no sirve descargas y no mueve nada. `healthz` lo dice.

### Permisos (UID/GID)

La app necesita poder **renombrar** dentro de las raíces borrables o renombrables
(`rename` necesita escritura en el directorio, no en el fichero) y crear carpetas en
la papelera. No necesita escribir en los ficheros.

Recomendado: el proceso con un UID propio (`APP_UID`) y como grupo el de los ficheros
de la biblioteca (`APP_GID`, el GID del usuario de Jellyfin tal como se ve en el LXC).
Luego, en el host:

```sh
chmod 2775 /ruta/a/media/buceo /ruta/a/media/originales-120fps /ruta/a/media/send
mkdir -p /ruta/a/media/.trash && chgrp <gid-host> /ruta/a/media/.trash && chmod 2775 /ruta/a/media/.trash
```

Además:
- `DATA_DIR/{main,public,cache}` propiedad de `APP_UID:APP_GID`.
- El directorio del manifiesto, con escritura para `APP_GID`. Los ficheros se escriben
  con `0644`.

### Traefik

Tres routers (ver `traefik/media.example.yml`):
- Panel en `websecure` **con** forward-auth de Authentik, y su router del outpost.
- API en `websecure` **sin** forward-auth.
- Público en el entrypoint de internet con `crowdsec-bouncer`. Ese entrypoint no debe
  tener `forwardedHeaders.trustedIPs`: nginx toma la IP del cliente del
  `X-Forwarded-For` que pone Traefik, y solo si la petición viene de `TRAEFIK_IP`.

### Authentik

Una aplicación/proveedor de forward-auth para el host del panel. La app además
comprueba el usuario contra `MM_PANEL_ALLOWED_USERS`: con la lista vacía no entra nadie.

### Jellyfin

- Abrir el `8096` del contenedor de Jellyfin al LXC nuevo. **Si falta, la app arranca y
  las miniaturas fallan sin que parezca un problema de red**: `healthz` lo marca como
  `jellyfin: false`.
- Sin API key: la app no la necesita (las miniaturas no piden autenticación y los IDs
  salen del export).

### Exportador de IDs (host)

`host/jellyfin-ids-export.py` + `host/media-management-ids.{service,timer}`, configurado
con `host/jellyfin-ids-export.env.example` en `/etc/default/media-management-ids`.
Cada 15 min escribe, de forma atómica:

```json
{"generated_at": "2031-01-01T00:00:00Z",
 "items": {"/ruta/de/jellyfin/buceo/FICHERO.MP4": "32 hex en minúsculas", "...": "..."}}
```

Si no puede leer la BD de Jellyfin, o no sale ni una fila de la biblioteca, **no
escribe** y el fichero anterior envejece. La app no escribe manifiesto con un export
ausente, vacío o de más de una hora (`MM_JELLYFIN_IDS_MAX_AGE_S`).

### Papelera (host)

`host/media-trash-purge.sh` + `host/media-trash-purge.{service,timer}`, configurado en
`/etc/default/media-trash-purge`. Borra `.trash/<raíz>/<AAAA-MM-DD>/` con más de 30
días, nunca sigue symlinks y se niega si `TRASH_DIR` no termina en `/.trash` o no
existe. Probar primero con `-n`. La app solo mueve ficheros: nunca borra.

### ntfy

Un tema propio para las solicitudes de acceso (no el de alertas) y un token con
permiso **solo de escritura** en ese tema. `MM_NTFY_URL` es la URL interna.

### Secretos (`.env`)

Parte de `.env.example`. Secretos: `MM_SECRET_KEY` (firma de tickets y CSRF, al menos
32 caracteres aleatorios; cambiarla invalida los tickets vivos) y `MM_NTFY_TOKEN`.
Nada de secretos en el compose ni en git.

### Monitorización

Uptime Kuma, directo al backend:
- `http://<lxc>:8002/healthz` y `http://<lxc>:8003/healthz`: `200` solo si las raíces
  están montadas, el export de IDs está fresco, Jellyfin responde y el worker late.
  El cuerpo JSON dice qué falla.
- `http://<lxc>:8080/healthz`: `200 ok` o `503 unavailable`, sin detalle (es la cara
  pública).

### Registros (Wazuh)

Todos los procesos escriben JSON a stdout. Las acciones que cambian estado van con
`action`, `result`, `ip` y, cuando hay un token de por medio, solo su hash truncado.
nginx escribe su access log en JSON. Recoger los logs de los contenedores (driver
`journald` o los ficheros de Docker).

### Copias de seguridad

`DATA_DIR/main` y `DATA_DIR/public`: enlaces vivos, concesiones, tokens y auditoría.
`DATA_DIR/cache` no hace falta. Para una copia consistente con la app en marcha:

```sh
sqlite3 DATA_DIR/main/main.db ".backup /destino/main.db"
sqlite3 DATA_DIR/public/public.db ".backup /destino/public.db"
```

## Traspaso del manifiesto

TripPlanner prod lee `buceo.json`. Hoy lo escribe el generador del host; la app lo
sustituye así, sin saltarse pasos y sin dos escritores sobre el mismo fichero:

1. **Exportador de IDs desplegado** en el host, con su timer. El generador viejo sigue
   igual.
   *Para seguir:* el export se renueva cada 15 min y `healthz` dice
   `jellyfin_ids_export.ok: true`.
2. **La app escribe el candidato**: `MANIFEST_FILE=buceo.candidate.json` (valor por
   defecto). Nunca `buceo.json` en este paso.
   *Para seguir:* el panel muestra el manifiesto como escrito, con el mismo número de
   clips que el actual.
3. **Comparar varios días**, incluido al menos un alta de clips nuevos:
   `MM_MANIFEST_COMPARE_WITH=/manifest/buceo.json` en el worker. El panel
   (Manifiesto → comparación) enseña las diferencias clip a clip: identidad,
   `jellyfin_item_id`, `captured_at_utc`, `size_bytes`. También a mano:
   `media-management compare buceo.candidate.json buceo.json`.
   *Para seguir:* cero diferencias que se repitan en dos comparaciones seguidas. Una
   diferencia que aparece en una pasada y desaparece en la siguiente es de timing
   (los dos generadores no corren a la vez).
4. **Cambio de escritor**, en este orden: desactivar el timer del generador viejo, y
   luego `MANIFEST_FILE=buceo.json` y quitar `MM_MANIFEST_COMPARE_WITH`. Es
   configuración, no código.
   *Para comprobar:* `generated_at` de `buceo.json` avanza con el escaneo de la app
   y TripPlanner sigue viendo sus miniaturas.

Si difieren, manda el generador viejo, y el fallo está en la app hasta que se
demuestre lo contrario.

## Comprobaciones tras el alta

- El token de un enlace **no** aparece en ningún log (Traefik, nginx, contenedores):
  `grep -r <token>` tiene que dar vacío tras abrir el enlace y descargar.
- La IP que registra el panel en una descarga es la del cliente, no la de Traefik ni
  la de nginx.
- Desde fuera de Traefik, `curl -H 'X-authentik-username: <usuario>' http://<lxc>:8002/`
  da 403; contra la API da 401.
- Abrir el enlace en WhatsApp o Telegram (previsualización) no genera ningún ticket en
  la ficha del enlace.
