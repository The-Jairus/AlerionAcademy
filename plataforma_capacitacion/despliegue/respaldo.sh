#!/usr/bin/env bash
# Respaldo diario de la plataforma: base de datos, material y certificados.
#
# Instalación (una sola vez, en el servidor):
#   chmod +x despliegue/respaldo.sh
#   (crontab -l 2>/dev/null; echo "15 3 * * * /root/AlerionAcademy/plataforma_capacitacion/despliegue/respaldo.sh") | crontab -
#
# Deja los respaldos en /root/respaldos y conserva los últimos 14 días.
set -euo pipefail

VOLUMEN="${VOLUMEN:-plataforma_capacitacion_datos}"
DESTINO="${DESTINO:-/root/respaldos}"
CONSERVAR_DIAS="${CONSERVAR_DIAS:-14}"

mkdir -p "$DESTINO"
ARCHIVO="$DESTINO/alerion-$(date +%F-%H%M).tar.gz"

docker run --rm \
  -v "$VOLUMEN":/datos:ro \
  -v "$DESTINO":/respaldo \
  alpine tar czf "/respaldo/$(basename "$ARCHIVO")" -C /datos .

find "$DESTINO" -name 'alerion-*.tar.gz' -mtime "+$CONSERVAR_DIAS" -delete

echo "$(date '+%F %T')  respaldo listo: $ARCHIVO ($(du -h "$ARCHIVO" | cut -f1))"
