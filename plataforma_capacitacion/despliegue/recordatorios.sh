#!/usr/bin/env bash
# Recordatorio diario de capacitación vencida.
#
# Instalación (una sola vez, en el servidor):
#   cd /root/AlerionAcademy/plataforma_capacitacion
#   chmod +x despliegue/recordatorios.sh
#   (crontab -l 2>/dev/null; echo "0 13 * * 1-5 /root/AlerionAcademy/plataforma_capacitacion/despliegue/recordatorios.sh >> /var/log/alerion-recordatorios.log 2>&1") | crontab -
#
# "0 13 * * 1-5" = todos los días hábiles a las 13:00 UTC, que son las 8:00 de
# la mañana en Colombia (UTC-5). Cámbialo si quieres otra hora: el servidor
# trabaja en UTC, así que suma 5 horas a la hora local que quieras.
#
# El script no envía nada dos veces el mismo día aunque cron se dispare de más.
set -euo pipefail

APP_DIR="${APP_DIR:-/root/AlerionAcademy/plataforma_capacitacion}"
cd "$APP_DIR"

echo "===== $(date -u '+%Y-%m-%d %H:%M:%S UTC') ====="

# Si el contenedor no está arriba, no es un error del recordatorio: se avisa y ya.
if ! docker compose ps --status running --quiet app | grep -q .; then
    echo "El contenedor 'app' no está corriendo. No se envía ningún recordatorio."
    exit 0
fi

docker compose exec -T app python recordatorios.py
