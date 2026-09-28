#!/bin/sh
# Modo demo: la app entera con datos de prueba, sin Jellyfin, Authentik ni ntfy.
#
#   ./demo.sh           construye la imagen y arranca (panel en http://localhost:8002)
#   ./demo.sh reset     para todo y borra los datos de prueba; el siguiente arranque
#                       los siembra desde cero
#   ./demo.sh <args>    cualquier otro comando de docker compose: down, logs -f, ps…
set -eu
cd "$(dirname "$0")"

keys=.env.demo.keys
if [ ! -f "$keys" ]; then
    (
        umask 077
        for name in MM_TICKET_KEY MM_CSRF_KEY; do
            printf '%s=%s\n' "$name" "$(head -c 48 /dev/urandom | base64 | tr -d '\n')"
        done > "$keys"
    )
fi

# Si no existen, Docker los crea como root y la siembra, que corre con este usuario,
# no puede escribir en ellos.
mkdir -p demo-data/media demo-data/data demo-data/zips demo-data/manifest \
         demo-data/jellyfin-ids demo-data/jellyfin
APP_UID=$(id -u)
APP_GID=$(id -g)
export APP_UID APP_GID

dc() {
    docker compose -p media-management-demo --env-file .env.demo --env-file "$keys" \
        -f compose.yaml -f compose.demo.yaml -f compose.demo.local.yaml "$@"
}

case "${1:-}" in
    "") dc up --build ;;
    reset)
        # La app nunca borra; los datos de prueba se vacían desde aquí y se vuelven a
        # sembrar al arrancar.
        dc down
        rm -rf demo-data
        echo "Datos de prueba borrados: ./demo.sh los vuelve a sembrar al arrancar."
        ;;
    *) dc "$@" ;;
esac
