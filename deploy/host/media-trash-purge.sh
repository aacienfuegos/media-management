#!/bin/sh
# Vacía la papelera de media-management: borra las carpetas de día con más de
# RETENTION_DAYS días. Corre en el host, no en la app: la app solo mueve ficheros
# y nunca borra, así que comprometerla no permite destruir la biblioteca.
#
#   TRASH_DIR=/ruta/a/media/.trash RETENTION_DAYS=30 media-trash-purge.sh [-n]
#
# Estructura que espera: $TRASH_DIR/<raíz>/<AAAA-MM-DD>/...
set -eu

DRY=0
[ "${1:-}" = "-n" ] && DRY=1
: "${TRASH_DIR:?falta TRASH_DIR}"
RETENTION_DAYS=${RETENTION_DAYS:-30}

case "$TRASH_DIR" in
  */.trash) ;;
  *) echo "TRASH_DIR tiene que terminar en /.trash: $TRASH_DIR" >&2; exit 2 ;;
esac
[ -d "$TRASH_DIR" ] && [ ! -L "$TRASH_DIR" ] || { echo "no existe $TRASH_DIR (¿volumen sin montar?)" >&2; exit 1; }
case "$RETENTION_DAYS" in ''|*[!0-9]*) echo "RETENTION_DAYS no es un número" >&2; exit 2 ;; esac

LIMIT=$(date -u -d "-$RETENTION_DAYS days" +%Y-%m-%d)
n=0
for day in "$TRASH_DIR"/*/*; do
  [ -d "$day" ] && [ ! -L "$day" ] || continue
  name=$(basename "$day")
  echo "$name" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' || continue
  # Comparación de texto: AAAA-MM-DD ordena igual que la fecha.
  if [ "$name" \< "$LIMIT" ]; then
    if [ "$DRY" = 1 ]; then
      echo "se borraría $day"
    else
      rm -rf --one-file-system -- "$day"
      echo "borrado $day"
    fi
    n=$((n + 1))
  fi
done
echo "$n carpetas de día anteriores a $LIMIT"
