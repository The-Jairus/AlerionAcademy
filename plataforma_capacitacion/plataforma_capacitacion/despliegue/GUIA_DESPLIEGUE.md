# Cómo publicar la plataforma en tu dominio

## Primero, qué es lo que ya tienes

La plataforma **ya es una aplicación web**: páginas HTML, CSS y JavaScript que se abren en el
navegador. Python es el lenguaje del servidor, el que atiende cada visita, consulta la base de
datos y arma la página. Es el mismo papel que cumple PHP en WordPress o Node.js en otros sistemas:
quien entra al sitio nunca ve Python, ve la página.

Lo que sigue es ponerla en línea. Una aplicación web en un dominio necesita tres cosas:

1. **Un servidor encendido** que ejecute la aplicación (no basta con subir archivos por FTP).
2. **El dominio apuntando** a la IP de ese servidor.
3. **Un certificado HTTPS**, para que las contraseñas viajen cifradas.

Abajo están las tres formas de lograrlo, de la más simple a la más manual. Elige una sola.

---

## Opción A · Sin servidor propio (la más rápida)

Servicios como Render, Railway o Fly.io ejecutan la aplicación por ti y te dan HTTPS automático.

1. Sube la carpeta del proyecto a un repositorio de GitHub.
2. En Render: **New → Blueprint** y selecciona el repositorio. El archivo `render.yaml` ya está
   configurado, incluido el disco de 10 GB donde viven la base, el material y los certificados.
3. Cuando el servicio arranque, entra a **Settings → Custom Domain**, escribe
   `capacitacion.tuempresa.com` y crea en tu proveedor de dominio el registro CNAME que te indique.
4. Crea tu primer administrador desde la consola web del servicio:
   `python crear_admin.py`

Costo aproximado: 7 USD al mes el servicio + 2.50 USD el disco. El plan gratuito no sirve aquí
porque borra los archivos en cada reinicio, y ahí están los videos y los certificados.

---

## Opción B · Tu propio servidor, con Docker (recomendada)

Sirve cualquier VPS con Ubuntu: DigitalOcean, Hetzner, Linode, AWS Lightsail, Azure. Con 2 GB de
RAM alcanza; el disco depende del peso de tus videos.

1. Apunta el dominio: crea un registro **A** de `capacitacion.tuempresa.com` hacia la IP del servidor.
2. Instala Docker en el servidor y sube el proyecto (por `git clone` o `scp`).
3. Edita `docker-compose.yml`: cambia `DOMINIO`, `CORREO` y `SECRET_KEY`.
4. Levántalo:

```bash
docker compose up -d --build
docker compose exec app python crear_admin.py
```

Listo: `https://capacitacion.tuempresa.com` con certificado emitido y renovado solo.

Comandos del día a día:

```bash
docker compose logs -f app                    # ver qué está pasando
docker compose restart app                    # reiniciar
docker compose up -d --build                  # actualizar tras cambiar el código
docker run --rm -v capacitacion_datos:/d -v $PWD:/b alpine \
  tar czf /b/respaldo-$(date +%F).tar.gz -C /d .   # respaldo completo
```

---

## Opción C · Tu propio servidor, sin Docker

Un solo comando en un VPS Ubuntu recién creado, con el dominio ya apuntando a su IP:

```bash
sudo bash despliegue/instalar_ubuntu.sh capacitacion.tuempresa.com
```

El script instala Python, LibreOffice, ffmpeg, Nginx y Certbot; crea el usuario del servicio,
registra la aplicación en systemd, configura Nginx y solicita el certificado HTTPS. Al terminar te
dice cómo crear el administrador.

Si prefieres hacerlo a mano, los archivos `despliegue/nginx.conf` y
`despliegue/capacitacion.service` traen comentadas las instrucciones paso a paso.

---

## Sobre el hosting compartido (cPanel, Plesk, hosting de 3 USD al mes)

Ese tipo de hosting suele ejecutar solo PHP y no permite dejar un proceso corriendo, así que **no
sirve** para esta aplicación tal como está. Tienes tres caminos:

- Revisa si tu plan incluye **"Setup Python App"** (Passenger). Varios cPanel lo traen: ahí sí
  funciona, apuntando el punto de entrada a `app.py` y el objeto `app`.
- Deja el dominio donde está y **apunta solo un subdominio** (`capacitacion.tuempresa.com`) al
  servidor nuevo con un registro A. Tu sitio principal no se toca.
- Rehacer el backend en PHP. Es reescribir la aplicación completa; solo tiene sentido si estás
  obligado a quedarte en ese hosting.

La segunda opción es la que usa casi todo el mundo: el sitio corporativo sigue en su hosting y la
plataforma vive en su propio subdominio.

---

## Antes de abrirlo a la gente

- [ ] `SECRET_KEY` propia (las opciones A y C la generan solas; en la B la escribes tú).
- [ ] HTTPS funcionando y variable `HTTPS=1` puesta, para que la cookie de sesión solo viaje cifrada.
- [ ] Cambiar la contraseña del administrador que creaste.
- [ ] Configurar el SMTP en **Configuración** y usar **Enviar prueba** para confirmar que el
      certificado llega al correo.
- [ ] Borrar los usuarios de demostración si sembraste datos de prueba (`seed.py`).
- [ ] Dejar programado el respaldo de la carpeta de datos: ahí están la base, el material y los
      certificados emitidos.

## Cuánto aguanta

SQLite sostiene sin problema una organización de unos cientos de personas usando la plataforma a
diario, porque la mayor parte del tráfico es servir video e imágenes, no escribir en la base. Si
llegas a miles de usuarios simultáneos, el cambio natural es mover la base a PostgreSQL y el
material a un almacenamiento tipo S3; el resto del código no cambia.
