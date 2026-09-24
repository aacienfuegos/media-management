#!/bin/bash
# Vacía la papelera de media-management: borra las carpetas de día con más de
# RETENTION_DAYS días. Corre en el host, no en la app: la app solo mueve ficheros
# y nunca borra.
#
#   TRASH_DIR=/ruta/a/media/.trash RETENTION_DAYS=30 media-trash-purge.sh [-n]
#
# Estructura que espera: $TRASH_DIR/<raíz>/<AAAA-MM-DD>/...
# Corre como root sobre un directorio en el que la app puede escribir: no sigue un
# symlink en ningún nivel, porque uno plantado en la papelera apuntaría el rm -rf a
# cualquier otro sitio.
set -euo pipefail

DRY=0
[ "${1:-}" = "-n" ] && DRY=1
: "${TRASH_DIR:?falta TRASH_DIR}"
RETENTION_DAYS=${RETENTION_DAYS:-30}

case "$TRASH_DIR" in
  */.trash) ;;
  *) echo "TRASH_DIR tiene que terminar en /.trash: $TRASH_DIR" >&2; exit 2 ;;
esac
[[ -d "$TRASH_DIR" && ! -L "$TRASH_DIR" ]] || { echo "no existe $TRASH_DIR (¿volumen sin montar?)" >&2; exit 1; }
[[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] || { echo "RETENTION_DAYS no es un número" >&2; exit 2; }

LIMIT=$(date -u -d "-$RETENTION_DAYS days" +%Y-%m-%d)
n=0
# find sin -L no sigue symlinks: ni una <raíz> ni una <fecha> que sean enlaces se
# recorren ni se devuelven (-type d excluye los symlinks).
while IFS= read -r -d '' day; do
  name=${day##*/}
  [[ "$name" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || continue
  # Comparación de texto: AAAA-MM-DD ordena igual que la fecha.
  [[ "$name" < "$LIMIT" ]] || continue
  if [ "$DRY" = 1 ]; then
    echo "se borraría $day"
  else
    rm -rf --one-file-system -- "$day"
    echo "borrado $day"
  fi
  n=$((n + 1))
done < <(find "$TRASH_DIR" -xdev -mindepth 2 -maxdepth 2 -type d -print0)
echo "$n carpetas de día anteriores a $LIMIT"
