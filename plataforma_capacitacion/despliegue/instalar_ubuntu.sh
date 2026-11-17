#!/usr/bin/env bash
# Instalación en un VPS Ubuntu/Debian limpio, sin Docker.
#
#   sudo bash despliegue/instalar_ubuntu.sh capacitacion.tuempresa.com
#
# Deja la plataforma corriendo con Nginx y HTTPS en el dominio indicado.
set -euo pipefail

DOMINIO="${1:-}"
DESTINO=/opt/capacitacion
USUARIO=capacitacion

if [[ -z "$DOMINIO" ]]; then
  echo "Uso: sudo bash instalar_ubuntu.sh tu-dominio.com"
  exit 1
fi
if [[ $EUID -ne 0 ]]; then
  echo "Ejecuta el script con sudo."
  exit 1
fi

echo "==> Instalando dependencias del sistema"
apt-get update
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx \
                   libreoffice-impress ffmpeg fonts-dejavu-core rsync

echo "==> Creando el usuario del servicio"
id -u "$USUARIO" &>/dev/null || useradd --system --create-home --home-dir "$DESTINO" "$USUARIO"

echo "==> Copiando la aplicación a $DESTINO"
ORIGEN="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$DESTINO"
rsync -a --exclude data --exclude .venv --exclude __pycache__ "$ORIGEN"/ "$DESTINO"/
mkdir -p "$DESTINO/data"

echo "==> Entorno virtual y dependencias"
python3 -m venv "$DESTINO/.venv"
"$DESTINO/.venv/bin/pip" install --quiet --upgrade pip
"$DESTINO/.venv/bin/pip" install --quiet -r "$DESTINO/requirements.txt" gunicorn

LLAVE="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
chown -R "$USUARIO:$USUARIO" "$DESTINO"

echo "==> Registrando el servicio"
sed -e "s|cambia-esto-por-una-cadena-larga-y-aleatoria|$LLAVE|" \
    "$DESTINO/despliegue/capacitacion.service" > /etc/systemd/system/capacitacion.service
systemctl daemon-reload
systemctl enable --now capacitacion

echo "==> Configurando Nginx"
sed -e "s|capacitacion.tuempresa.com|$DOMINIO|" \
    "$DESTINO/despliegue/nginx.conf" > /etc/nginx/sites-available/capacitacion
ln -sf /etc/nginx/sites-available/capacitacion /etc/nginx/sites-enabled/capacitacion
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "==> Solicitando el certificado HTTPS"
certbot --nginx -d "$DOMINIO" --non-interactive --agree-tos --register-unsafely-without-email \
  || echo "   (Si falló, revisa que el dominio ya apunte a este servidor y repite: certbot --nginx -d $DOMINIO)"

echo
echo "Listo: https://$DOMINIO"
echo "Crea tu primer administrador con:"
echo "  sudo -u $USUARIO $DESTINO/.venv/bin/python $DESTINO/crear_admin.py"
