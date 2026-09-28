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

Son dos controles distintos y cada uno tiene su prueba en "Comprobaciones tras el
alta": el cortafuegos (`DOCKER-USER`) decide quién llega al puerto, y la app decide de
quién se fía.

## Lista para el alta

### LXC

- LXC **sin privilegios** con Docker (`nesting=1`, `keyctl=1`).
- `/etc/docker/daemon.json` en el LXC:

  ```json
  {"userland-proxy": false,
   "log-driver": "json-file", "log-opts": {"max-size": "20m", "max-file": "5"}}
  ```

  - Sin `userland-proxy: false`, el contenedor ve como origen la pasarela de Docker y
    no la IP real: el panel rechaza a Traefik y los registros mienten.
  - Sin rotación, el JSON a stdout de cinco contenedores acaba llenando el disco.
- Bind mounts:
  - Directorio de media del host → en el LXC, **lectura-escritura** y **`optional`**.
    Si el disco cifrado no está desbloqueado, el LXC arranca igual y la app se niega a
    operar (ver centinela). En Proxmox, `mpX` no admite `optional`: va como
    `lxc.mount.entry: /ruta/a/media ruta/en/lxc none bind,optional,create=dir` en
    `/etc/pve/lxc/<ctid>.conf`. Si el disco se monta **después** de arrancar el LXC, el
    LXC no lo ve hasta reiniciarlo (el centinela lo detecta y `healthz` lo dice).
  - Durante el traspaso del manifiesto, dos:
    - un directorio **propio** para el candidato → **lectura-escritura** (`MANIFEST_DIR`);
    - el directorio que lee TripPlanner → **solo lectura** (`MANIFEST_CURRENT_DIR`).
    Tras el traspaso, solo el de TripPlanner, y pasa a **lectura-escritura**.
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

La app necesita **renombrar** (mover a la papelera también es un `rename`) solo en las
raíces que la versión actual puede tocar, y crear carpetas en la papelera. `rename`
necesita escritura en el directorio, no en el fichero: nunca escribe en los ficheros.

- **`APP_UID`: propio y único en todo el nodo.** Todos los LXC sin privilegios
  comparten el mismo mapeo de UIDs al host, así que el mismo UID en otro LXC es el
  mismo dueño en disco. No usar el `10001` de la imagen.
- **`APP_GID`**: el grupo de los ficheros de la biblioteca tal como se ve en el LXC.

Escritura (grupo + `2775`) **solo** en las raíces en las que se puede borrar o
renombrar algo hoy y en `.trash`. Las raíces con `synced` o `requires_second_copy` no
se tocan: en ellas no se renombra, y no se borra nada hasta que exista el inventario de
la segunda copia (`panel/policy.py`). Mínimo privilegio; el día que haga falta se abre
con su motivo. En el host, con el GID tal como se ve fuera del LXC:

```sh
chgrp <gid-host> /ruta/a/media/send && chmod 2775 /ruta/a/media/send
mkdir -p /ruta/a/media/.trash && chgrp <gid-host> /ruta/a/media/.trash && chmod 2775 /ruta/a/media/.trash
```

`chmod` sin `chgrp` no vale: si la raíz es de otro grupo, da escritura a quien no toca
y no a la app.

Además:
- nginx (`nginx-unprivileged`, sin root, sistema de ficheros de solo lectura) lee los
  ficheros que sirve como UID/GID 101: tienen que ser legibles por "otros" o por ese
  GID. Es el mismo usuario que ya usaban los workers de la imagen oficial.
- `DATA_DIR/{main,public,cache}` propiedad de `APP_UID:APP_GID`.
- `MANIFEST_DIR`, con escritura para `APP_GID`. Los ficheros se escriben
  con `0644`.

### Ancho de banda

nginx limita cada descarga (`DOWNLOAD_RATE`, 12 MB/s por defecto) y el número de
descargas simultáneas por IP (`DOWNLOAD_CONN_PER_IP`), pero no tiene un tope global
razonable: varios clientes a la vez suman. **El tope global es requisito del alta** y
va en el límite de velocidad de la interfaz del LXC: la mitad o algo más de la subida
medida, para que el resto de la casa y el resto de servicios publicados sigan
funcionando. `DOWNLOAD_RATE` se elige para que dos descargas a la vez quepan bajo ese
tope.

### Cortafuegos (`DOCKER-USER`)

Los puertos publicados por Docker pasan por `FORWARD` después del DNAT: una regla en
`INPUT` no los ve, y el control sale en verde sin filtrar nada. Van en `DOCKER-USER`,
casando por el puerto **original** (`--ctorigdstport`; `--dport` ya es el del
contenedor). Dos cosas:

1. Los puertos `8002`, `8003` y `8080`, solo desde Traefik y el monitor.
2. La red `share` (proceso público y nginx, lo único expuesto a internet) solo puede
   **iniciar** conexiones hacia ntfy, que es lo único a lo que sale el proceso público
   (las miniaturas las copia el panel al crear el enlace). Sin esta regla, un proceso
   público comprometido llega al endpoint de streaming de Jellyfin, que no pide
   autenticación, y al resto de la red.

Ejemplo con IPs de documentación (Traefik `192.0.2.10`, monitor `192.0.2.11`, ntfy
`192.0.2.12:8080`). La subred es `SHARE_SUBNET`: si se cambia en el `.env`, hay que
cambiarla aquí, o la salida queda abierta o nginx deja de llegar al público.

```sh
#!/bin/sh
set -eu
iptables -F DOCKER-USER
iptables -A DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
# Dentro de la propia red share (nginx → público).
iptables -A DOCKER-USER -s 172.31.250.0/24 -d 172.31.250.0/24 -j RETURN
iptables -A DOCKER-USER -s 172.31.250.0/24 -d 192.0.2.12 -p tcp --dport 8080 -j RETURN
iptables -A DOCKER-USER -s 172.31.250.0/24 -j DROP
for port in 8002 8003 8080; do
  for src in 192.0.2.10 192.0.2.11; do
    iptables -A DOCKER-USER -p tcp -s "$src" -m conntrack --ctorigdstport "$port" -j RETURN
  done
  iptables -A DOCKER-USER -p tcp -m conntrack --ctorigdstport "$port" -j DROP
done
iptables -A DOCKER-USER -j RETURN
```

Es solo IPv4. Si el LXC o Docker tienen IPv6, la cadena de `ip6tables` está vacía y
nada de esto filtra: o se desactiva IPv6 en el LXC (y no se activa `enable_ipv6` en
Docker), o se replican las reglas en `ip6tables`. Tiene que sobrevivir a reinicios de Docker, que puede vaciar `DOCKER-USER`: como unidad
`oneshot` con `After=docker.service`, `PartOf=docker.service` y
`WantedBy=docker.service`.

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
Lo que se compara es el **`username` de Authentik** (la cabecera
`X-authentik-username`), no el nombre visible ni el correo.

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
`/etc/default/media-trash-purge` **y** en el drop-in
`/etc/systemd/system/media-trash-purge.service.d/paths.conf` (plantilla en
`host/media-trash-purge.service.d/paths.conf.example`). El drop-in lleva las rutas que
systemd no puede leer del entorno (`RequiresMountsFor`, `ReadWritePaths` y el centinela
en `ConditionPathExists`); sin él la unidad falla a propósito. Con el disco cifrado sin
montar, el servicio se salta limpio. Borra `.trash/<raíz>/<AAAA-MM-DD>/` con más de 30
días, nunca sigue symlinks y se niega si `TRASH_DIR` no termina en `/.trash` o no
existe. Probar primero con `-n`. La app solo mueve ficheros: nunca borra.

### ZIP de los enlaces

Un enlace con dos o más ficheros puede ofrecer además un ZIP con todo (casilla al
crearlo, activada por defecto). Lo genera el worker y lo sirve nginx desde `ZIPS_DIR`,
**fuera de `MEDIA_DIR`**: dentro lo escanearía el catálogo y quizá Jellyfin, y el
worker necesitaría escritura en la biblioteca.

- **Marcador**, en el disco real y antes de montar el directorio:
  `touch <ZIPS_DIR>/.zips-root`. Sin él el worker no escribe ni borra nada ahí, los
  enlaces se ofrecen solo fichero a fichero y el log lo dice como error. Si el disco no
  estuviera montado, `ZIPS_DIR` sería un directorio vacío del disco del sistema y los
  gigas irían a parar ahí.
- **Permisos**: propiedad de `APP_UID:APP_GID` con `0755`. Escribe solo el worker; nginx
  (UID 101) lee los zips, que se crean con `0644`. El panel y el proceso público no lo
  montan: el estado sale de la BD.
- **Espacio**: cada zip ocupa lo mismo que los ficheros del enlace mientras el enlace
  vive. No se genera si después quedarían menos de `ZIP_MIN_FREE_GB` libres o si pasa de
  `ZIP_MAX_GB` (0 = sin tope); el enlace sigue funcionando fichero a fichero y la ficha
  del enlace dice el motivo.
- **Ciclo de vida**: el worker compara cada pocos segundos lo que hay con lo que debería
  haber. Revocar, caducar o quitar el zip lo borra; renovar lo rehace; mandar un fichero
  a la papelera, restaurarlo o renombrarlo lo anula en el acto y lo rehace. Cada versión
  es `<enlace>-<versión>.zip` y el ticket va ligado a ella: reanudar una descarga del zip
  viejo da 404 en vez de mezclar dos zips. Un fallo al generarlo se reintenta con espera
  creciente y hasta cinco veces; la ficha del enlace tiene "Regenerar el ZIP".
- **Borrado**: es la única excepción a "la app nunca borra", porque son datos
  derivados. El worker solo borra dentro de `ZIPS_DIR`, sin seguir symlinks y solo
  nombres con la forma de los suyos (`<n>-<n>.zip` y su temporal).

### ntfy

Un tema propio para las solicitudes de acceso (no el de alertas) y un token con
permiso **solo de escritura** en ese tema. `MM_NTFY_URL` es la URL interna.

### Secretos (`.env`)

Parte de `.env.example`. Secretos, cada uno solo en el contenedor que lo usa:

| Variable | Contenedor | Para qué |
|---|---|---|
| `MM_TICKET_KEY` | `public` | Firma de los tickets de descarga. Cambiarla invalida los tickets vivos |
| `MM_CSRF_KEY` | `panel` | Firma de los tokens CSRF. Cambiarla obliga a recargar el panel |
| `MM_NTFY_TOKEN` | `public` | Aviso de solicitudes de acceso |

Las dos claves, de al menos 32 caracteres aleatorios y distintas
(`openssl rand -base64 48`); sin su clave, el proceso no arranca. El `.env` solo se usa
para interpolar `compose.yaml`: una variable `MM_*` que no aparezca allí no llega a
ningún contenedor. Nada de secretos en el compose ni en git.

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
nginx escribe su access log en JSON. El ticket de descarga va en la ruta (`/download/<ticket>`) y basta
para descargar ese fichero durante horas, así que no se registra: nginx lo sustituye
por `<ticket>` (también en `/Download/...` y demás variantes), su error log solo
escribe errores críticos porque lleva la línea de petición sin tapar, y el proceso
público no tiene access log propio.

**Riesgo aceptado:** el access log de Traefik sí lo ve. No se recorta porque CrowdSec
necesita la ruta. Aguanta porque el ticket no da más que el enlace del que sale, cada
petición vuelve a comprobar que el enlace y la concesión siguen vivos (revocar lo
mata) y esos logs solo los lee administración. El ticket no se liga a la IP a
propósito: rompería reanudar en el móvil al pasar de wifi a datos. Recoger los logs de los contenedores (driver
`journald` o los ficheros de Docker).

### Copias de seguridad

`DATA_DIR/main` y `DATA_DIR/public`: enlaces vivos, concesiones, tokens y auditoría.
`DATA_DIR/cache` y `ZIPS_DIR` no hacen falta: son datos derivados y se regeneran. Para una copia consistente con la app en marcha:

```sh
sqlite3 DATA_DIR/main/main.db ".backup /destino/main.db"
sqlite3 DATA_DIR/public/public.db ".backup /destino/public.db"
```

## Actualizar

Las dos imágenes (`IMAGE` y `NGINX_IMAGE`) van por digest y el compose no arranca sin
ellas: un tag olvidado no puede convertirse en un despliegue que nadie ha decidido.
Se suben a mano:
- la app, con el digest que publica la Action al fusionar en `main`;
- nginx, cuando haya una versión de parche o un aviso de seguridad de la rama estable:
  `docker pull` del tag, `docker image inspect --format '{{index .RepoDigests 0}}'` y ese
  valor en `NGINX_IMAGE`.

Luego `docker compose pull && docker compose up -d`.

### Antes de actualizar a la versión con ZIP

**Rompe si no se prepara antes:** `compose.yaml` exige `ZIPS_DIR` (`${ZIPS_DIR:?}`).
Con la imagen nueva y el `.env` viejo, `docker compose up` no arranca **ningún**
servicio. Antes del `pull`:

1. Crear el directorio en el disco de datos, con `.zips-root` dentro, y montarlo en el
   LXC (ver "ZIP de los enlaces").
2. Añadir `ZIPS_DIR` al `.env` (y, si hacen falta otros valores, `ZIP_MIN_FREE_GB` y
   `ZIP_MAX_GB`).
3. `docker compose config -q` sin errores.

El esquema se amplía solo al arrancar: dos tablas nuevas (`link_zips` en `main.db`,
`ticket_zip_entries` en `public.db`), creadas con `CREATE TABLE IF NOT EXISTS` y sin
tocar las existentes. Volver a la imagen anterior las ignora; lo único que se pierde
son las descargas de ZIP a medio reanudar, cuyo ticket la versión vieja no entiende.

## Traspaso del manifiesto

TripPlanner prod lee `buceo.json`. Hoy lo escribe el generador del host; la app lo
sustituye así, sin saltarse pasos y sin dos escritores sobre el mismo fichero:

1. **Exportador de IDs desplegado** en el host, con su timer. El generador viejo sigue
   igual.
   *Para seguir:* el export se renueva cada 15 min y `healthz` dice
   `jellyfin_ids_export.ok: true`.
2. **La app escribe el candidato** en un directorio propio: `MANIFEST_DIR` es ese
   directorio, `MANIFEST_FILE=buceo.candidate.json` (valor por defecto), y el directorio
   de TripPlanner (`MANIFEST_CURRENT_DIR`) se monta en solo lectura con
   `compose.compare.yaml`, activado en el `.env` con
   `COMPOSE_FILE=compose.yaml:compose.compare.yaml` (no con dos `-f`: un
   `docker compose up -d` a secas quitaría el montaje de solo lectura). Así la app no puede
   escribir `buceo.json` aunque haya una errata; si los dos directorios resultan ser el
   mismo, el worker no escribe nada y el panel dice por qué.
   *Para seguir:* el panel muestra el manifiesto como escrito, con el mismo número de
   clips que el actual.
3. **Comparar varios días**, incluido al menos un alta de clips nuevos. Con
   `compose.compare.yaml` el worker compara cada candidato con
   `/manifest-current/buceo.json` (`MANIFEST_CURRENT_FILE` si se llama distinto). El panel
   (Manifiesto → comparación) enseña las diferencias clip a clip: identidad,
   `jellyfin_item_id`, `captured_at_utc`, `size_bytes`. También a mano:
   `media-management compare buceo.candidate.json buceo.json`.
   *Para seguir:* cero diferencias que se repitan en dos comparaciones seguidas. Una
   diferencia que aparece en una pasada y desaparece en la siguiente es de timing
   (los dos generadores no corren a la vez).
4. **Cambio de escritor**, en este orden:
   1. desactivar el timer del generador viejo;
   2. en el LXC, el directorio de TripPlanner pasa de solo lectura a
      **lectura-escritura**;
   3. `MANIFEST_DIR` = ese directorio, `MANIFEST_FILE=buceo.json`, vaciar
      `MANIFEST_CURRENT_DIR`, quitar `COMPOSE_FILE` del `.env` y
      `docker compose up -d`.

   Es configuración, no código. Cambia qué directorio se monta, no solo el nombre del
   fichero.
   *Para comprobar:* `generated_at` de `buceo.json` avanza con el escaneo de la app
   y TripPlanner sigue viendo sus miniaturas.

Si difieren, manda el generador viejo, y el fallo está en la app hasta que se
demuestre lo contrario.

## Comprobaciones tras el alta

Cada comprobación negativa va con su pareja positiva: un "no aparece" o un "no
responde" solo vale si en el mismo sitio sí aparece o sí responde lo legítimo. Si no,
la forma más barata de aprobar es mirar el fichero equivocado o romper el servicio.

- **Logs sin secretos.** Tras abrir un enlace, descargar un fichero entero y reanudar
  otro, en los logs de nginx y de los contenedores (`docker compose logs`):
  - `grep <token>` y `grep <ticket>` dan vacío (el ticket es el último segmento de la
    URL `/download/...` que pide el navegador);
  - `grep /download/` y `grep /_protected/` **sí** encuentran esas descargas.

  En el access log de Traefik el ticket sí aparece (riesgo aceptado, ver "Registros").
- **ZIP.** Crear un enlace de prueba con dos ficheros y la casilla del ZIP: en unos
  segundos la ficha dice "listo" y `ZIPS_DIR` tiene `<enlace>-1.zip`. Bajarlo, cortarlo a
  mitad y reanudarlo; `unzip -t` sobre lo bajado da OK y el access log de nginx registra
  `/_zips/<enlace>-1.zip` sin el ticket. Revocar el enlace: en unos segundos el zip ya no
  está en `ZIPS_DIR`. Pareja del marcador: con `.zips-root` renombrado, la ficha dice
  "almacén de ZIP no montado", el log del worker lo da como error y no aparece nada
  nuevo en `ZIPS_DIR`; al devolverlo, el zip vuelve.
- **IP real.** La IP que registra el panel en una descarga es la del cliente, no la de
  Traefik, la de nginx ni la pasarela de Docker.
- **Control de la app**, desde el propio LXC y por su IP (no `localhost`, que no pasa
  por la misma ruta):
  - `curl -H 'X-authentik-username: <usuario>' http://<ip-lxc>:8002/` → `403`;
  - lo mismo contra `:8003/api/v1/roots` → `401`.
- **Cortafuegos**, desde otra máquina de la red que no sea Traefik ni el monitor: los
  puertos `8002`, `8003` y `8080` no responden, ni por IPv4 ni por IPv6 (o el LXC no
  tiene dirección IPv6).
- **La pareja legítima de las dos anteriores:** el panel por su nombre, a través de
  Traefik y Authentik, funciona; los tres `healthz` del monitor siguen en verde; y un
  enlace abierto desde fuera de la LAN descarga.
- **Salida de `share` cerrada**: desde el contenedor `public`, una conexión al `8096`
  de Jellyfin falla; una solicitud de acceso sí llega a ntfy.
- Abrir el enlace en WhatsApp o Telegram (previsualización) no genera ningún ticket en
  la ficha del enlace.

## Staging (modo demo)

El staging corre el modo demo con Authentik real: `compose.yaml` + `compose.demo.yaml`,
**sin** `compose.demo.local.yaml` (ese es el proxy de cabeceras y el build locales).

- Ficheros del commit de la imagen (`org.opencontainers.image.revision`):
  `compose.yaml`, `compose.demo.yaml`, `.env.demo`, `deploy/demo/roots.toml`,
  `deploy/demo/jellyfin.conf` y `deploy/nginx/share.conf.template`. Los fixtures van
  dentro de la imagen.
- Variables: las de `.env.demo` y encima un `.env` propio, que gana
  (`docker compose --env-file .env.demo --env-file .env -f compose.yaml -f compose.demo.yaml`).
  En el `.env` propio: `IMAGE`, `APP_UID`, `APP_GID`, `MM_TICKET_KEY`, `MM_CSRF_KEY`,
  `TRAEFIK_IP`, `MM_TRUSTED_PROXIES`, `MM_PANEL_ALLOWED_USERS`, `MM_PUBLIC_URL`,
  `MM_PANEL_URL`, los `*_BIND` y, si se quieren fuera del directorio del proyecto, las
  rutas de datos (`MEDIA_DIR`, `DATA_DIR`, `ZIPS_DIR`, `MANIFEST_DIR`,
  `IDS_EXPORT_DIR`, `DEMO_JELLYFIN_DIR`).
- Cada directorio de datos tiene que existir, ser de `APP_UID` y estar vacío o marcado
  con `.demo-root`: si no, `demo-seed` falla y no arranca nada más.
- Volver a los datos de prueba iniciales: `down`, vaciar esos directorios y `up`. La
  app nunca borra, tampoco en demo.
