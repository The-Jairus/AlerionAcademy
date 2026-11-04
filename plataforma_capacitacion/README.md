# Alerion Academy — plataforma de capacitación

> La interfaz, los correos y los certificados están en inglés. Esta guía técnica
> está en español para el equipo que administra el servidor.

Aplicación web completa (frontend + backend + base de datos) para asignar capacitaciones,
controlar que la gente vea el contenido, evaluarla y emitir el certificado por correo.

Hecha con Flask y SQLite: no necesita servidor de base de datos ni servicios externos.

---

## 1. Instalación

```bash
cd plataforma_capacitacion
python3 -m venv .venv && source .venv/bin/activate      # en Windows: .venv\Scripts\activate
pip install -r requirements.txt
python seed.py            # datos de demostración (opcional pero recomendado la primera vez)
python app.py
```

Abre <http://127.0.0.1:5000>.

Requisitos del sistema:

| Herramienta | Para qué | Obligatoria |
|---|---|---|
| Python 3.10+ | la aplicación | sí |
| LibreOffice (`soffice`) | convertir `.pptx` a PDF al subir el curso | solo si subes presentaciones |
| ffmpeg / ffprobe | medir la duración de los videos | solo si subes videos |

Si no instalas LibreOffice puedes subir el material ya convertido a PDF.

### Usuarios de la demostración

| Rol | Usuario | Contraseña |
|---|---|---|
| Admin | admin@flyalerion.com | Admin123* |
| Instructor | elena@flyalerion.com | Course123* |
| Learner | james@flyalerion.com | Course123* |
| Learner | diana@flyalerion.com | Course123* |
| Learner | marcus@flyalerion.com | Course123* |

`python seed.py --reiniciar` borra todo y vuelve a generar la demo.

### Empezar de cero con los cursos

Si la parte de cursos quedó inconsistente por una migración, `reiniciar.py` la reconstruye:

```bash
python reiniciar.py --cursos    # borra cursos, módulos, exámenes, asignaciones,
                                # intentos, certificados y el material subido.
                                # CONSERVA usuarios y configuración.
python reiniciar.py --todo      # borra además usuarios: queda como recién instalada.
```

En el servidor: `docker compose exec app python reiniciar.py --cursos`.
Las tablas no se vacían, se eliminan y se recrean desde el esquema actual, así que
no queda ningún resto de versiones anteriores. Al final revisa la integridad y lo informa.
Para arrancar con la base vacía: borra la carpeta `data/` y ejecuta `python app.py`; luego crea
el primer administrador con `python crear_admin.py`.

---

## 2. Cómo funciona

### Ingreso
Usuario y contraseña contra la tabla `usuarios`. Las contraseñas se guardan con hash PBKDF2-SHA256
(nunca en texto plano). Hay bloqueo temporal tras 8 intentos fallidos y protección CSRF en todos
los formularios.

### Roles
La plataforma tiene tres roles:

| Rol | Qué puede hacer |
|---|---|
| **Learner** | Ve y realiza los cursos que le asignaron, y descarga sus certificados. |
| **Instructor** | Todo lo del learner, **más** crear cursos y exámenes. Edita y asigna **solo los cursos que él creó**: los ajenos no le aparecen en la lista y el servidor responde 403 si intenta abrirlos por URL. |
| **Admin** | Todo lo anterior sobre **cualquier** curso, más usuarios, importación de registros, certificados y configuración. |

El dueño de un curso queda guardado en `cursos.creado_por` cuando se crea. Al actualizar una base
que venía de la versión anterior, los cursos existentes quedan a nombre del primer administrador y
el rol `estudiante` pasa a llamarse `learner`.

### Vista de estudiante
1. **Mis cursos** lista solo lo asignado a ese usuario, con su avance y fecha límite.
2. **Contenido.** Si es video, se reproduce sin permitir adelantar: al saltar, la barra vuelve al
   punto ya alcanzado. Si es documento, el PDF o PPTX se muestra **página por página como imagen**,
   con un tiempo mínimo de lectura por página.
3. **Examen bloqueado.** El botón solo se habilita cuando el material está completo (95 % del video
   o la última página del documento). El bloqueo también se valida en el servidor: entrar a la URL
   del examen a mano no funciona.
4. **Certificado.** Al aprobar se genera el PDF y se envía al correo del usuario **con copia al
   correo del administrador**. También queda disponible para descarga.

### Cursos y módulos
Un curso es un contenedor: se crea primero y luego se le agregan los módulos que haga falta. Cada
módulo tiene su propio material (video, PDF o PPTX) y su propio examen. El curso se aprueba, y el
certificado se emite, solo cuando **todos** los módulos están aprobados. La nota del certificado es
el promedio de la mejor nota de cada módulo.

### Varios materiales en un módulo
Un módulo puede llevar **los materiales que haga falta**: una presentación y un video, por ejemplo.
Se recorren en el orden definido, y el examen no se desbloquea hasta que la persona terminó
**todos**. El avance se guarda por material (`avance_contenidos`) y el del módulo es el promedio.

Desde el curso, cada material se reordena con ↑↓, se renombra y se quita. Un módulo no puede
quedarse sin ninguno. Al agregar material nuevo a un módulo que alguien ya había terminado, ese
módulo vuelve a quedar incompleto para esa persona: el contenido que vio ya no es todo el contenido.

### Preview del curso
El botón **Preview as a learner**, en el curso, lo muestra tal como lo verá el aprendiz: los
módulos en orden, cada material con su visor y el banco de preguntas de cada examen. No registra
avance, no cuenta intentos y no emite certificados, así que revisar un curso no ensucia los
reportes. En el preview se puede saltar entre páginas libremente; el aprendiz no.

Un módulo puede llevar examen o no (ver más abajo). Dentro del curso, los módulos se reordenan con
las flechas ↑↓ y cada examen viaja con su módulo: al mover el módulo se mueve también su examen. El botón **Edit module** cambia el título, la
descripción y, si hace falta, reemplaza el material; al reemplazarlo se reinicia el avance de quienes
ya lo habían recorrido, porque el contenido que vieron ya no es el mismo.

### Vigencia y cursos de una sola vez
Al crear o editar un curso se indica cada cuántos meses vence el certificado. La casilla
**One-time course** bloquea ese campo: el curso se hace una vez, el certificado no lleva fecha de
vencimiento y nunca aparece en el control de vencimientos de **Certificates**, que marca en rojo lo
que ya caducó y tiene un filtro **Expired only**.

### Exámenes: opcionales y reutilizables
Un examen **existe por su cuenta**, con su propio banco de preguntas, y se gestiona en la pantalla
**Exams**. Desde ahí se crea, se carga el banco y se ve en qué módulos se está usando.

Al crear o editar un módulo, el campo **Exam** ofrece tres caminos:

- **Sin examen**: recorrer el contenido completo basta para aprobar el módulo. Si ningún módulo del
  curso tiene examen, al terminar el material el curso queda aprobado y el certificado se emite con
  nota 100.
- **Crear uno nuevo** para ese módulo.
- **Reutilizar uno existente**: el mismo examen puede usarse en varios módulos, incluso de cursos
  distintos. Comparten el banco de preguntas, así que editarlo los actualiza a todos; la pantalla
  avisa cuando un examen está compartido.

Borrar un módulo ya **no** borra su examen, y un examen que esté en uso no se puede borrar: primero
hay que desengancharlo de los módulos que lo usan.

En la configuración de cada examen se define cuántas preguntas ve la persona en cada intento
(0 = todas) y si se mezclan. Con la mezcla activa, cada intento saca preguntas distintas y en
distinto orden, y las opciones también se barajan sin perder cuál es la correcta. Las preguntas del
banco se reordenan con ↑↓ y su enunciado se puede corregir sin volver a crearlas.

### Certificado firmado o sin firma
Cada curso define qué tipo de certificado emite, y de ahí salen las dos plantillas base:

- **Sin firma**: no lleva bloque de firma. El sello queda centrado y el pie aclara que se emitió
  electrónicamente y no requiere firma.
- **Firmado**: se pide el nombre, el cargo y la imagen de la firma de quien firma. La imagen se
  recorta y se optimiza al subirla (PNG con fondo transparente es lo que mejor queda) y se imprime
  sobre la línea de firma.

El firmante configurado en **Settings** quedó como respaldo: solo se usa si un curso firmado dejó
esos campos en blanco.

El certificado ya **no imprime el número de documento ni la duración del curso**. El campo `horas`
se sigue guardando en la base por si más adelante lo exige la regulación.

### Plantilla del certificado
En **Settings → Certificate design** se puede seguir usando el diseño incluido o subir la plantilla
aprobada por la empresa en Word, PowerPoint u ODF. La plataforma reemplaza los marcadores
(`{{NAME}}`, `{{COURSE}}`, `{{DATE}}`, `{{SCORE}}`, `{{CODE}}` y demás; `{{DATE}}` es la
fecha de ejecución del curso en formato mm/dd/yyyy, y `{{SIGNER}}` queda en blanco si el curso
emite certificado sin firma) y la convierte a PDF con
LibreOffice. El botón **Download a sample** genera un certificado de prueba para revisar el resultado
antes de usarlo. Si la plantilla falla, el sistema vuelve solo al diseño incluido para no dejar a
nadie sin certificado.

### Importación de registros anteriores
**Import records** carga capacitaciones hechas antes de la plataforma. Se puede importar de a uno,
adjuntando el PDF del certificado original, o en bloque con un CSV (`email,course,date,score,code`).
Los registros importados cuentan en el training summary igual que los ejecutados en la plataforma.

### Identidad visual
Los archivos de marca están en `static/img/`: `logo.png` (barra lateral y pantalla de ingreso),
`mark.png` (marca de agua), `certificate_logo.png` y `certificate_mark.png` (certificado) y
`favicon.png`. Los colores viven en el bloque de variables al inicio de `static/css/app.css` y se
repiten como `NAVY` y `GOLD` en `certificados.py`.

### Quién puede editar un curso
Además de quien lo creó, se pueden agregar **otros instructores** desde el propio curso, en *Who
can edit this course*. Quedan guardados en `curso_instructores` y pueden editarlo y asignarlo igual
que el dueño. El dueño no se puede quitar.

Cada curso guarda **cuándo se modificó por última vez y quién lo hizo**, y eso se ve en la lista de
cursos y en el encabezado del curso.

### Encuesta de satisfacción
Al terminar un curso, la persona ve una encuesta de una a cinco estrellas con un comentario
opcional. Cada respuesta se le manda por correo al instructor del curso (y a los instructores
adicionales, y al administrador), y se acumula en el propio curso, con el promedio.

### Bitácora
**Activity log**, solo para el administrador, es la lista de lo que se ha hecho en la plataforma:
quién, qué, cuándo y desde qué dirección. Se agrupa por día y se puede filtrar por persona o por
texto. Guarda el nombre de quien actuó como copia, para que el registro siga siendo legible aunque
después se borre esa cuenta.

### Ficha de una persona
Al hacer clic en un nombre, en **Users** o en **Assignments**, se abre su ficha: estadísticas
(cursos asignados, completados, pendientes, vencidos, cumplimiento, nota promedio, horas y
certificados), la lista de lo que tiene asignado con su estado, y sus certificados. Desde ahí, y
desde Assignments, se puede **previsualizar el certificado** (con descargar y cancelar),
**reiniciar** el curso de esa persona para que empiece de cero, y **desmatricularla**.

### Vista de administrador
Un instructor ve estas mismas pantallas, pero limitadas a sus cursos, y sin Usuarios, Importación,
Certificados, Bitácora ni Configuración.

- **Panel**: indicadores, sus propios cursos pendientes, vencidos y últimos certificados.
- **Usuarios**: crear, editar, cambiar contraseña, activar/desactivar y eliminar. Se elige el rol
  entre Learner, Instructor y Admin.
- **Cursos**: crear subiendo el material (video, PDF, PPTX, ODP), editar, ocultar y eliminar,
  con el tipo de certificado y la vigencia del curso.
- **Asignar cursos**: varios cursos a varias personas de una vez, eligiendo con casillas y buscador
  (sin Ctrl+clic), con fecha límite y aviso por correo. Lo asignado se agrupa por curso, con las
  fechas de asignación, vencimiento y ejecución de cada persona, y lo vencido marcado en rojo.
- **Exámenes**: biblioteca de exámenes reutilizables, con puntaje mínimo, intentos, preguntas por
  intento y banco de preguntas de selección única.
- **Resumen de capacitaciones**: todo lo asignado con filtros; lo pendiente aparece en rojo pastel
  y lo aprobado en blanco.
- **Certificados**: buscador por persona (nombre o correo) y por curso, con la fecha de vencimiento
  de cada uno, descarga del PDF y reenvío por correo.
- **Configuración**: datos que salen en el certificado y servidor SMTP.

---

## 3. Correo

### Los cuatro correos que salen solos

| Correo | Cuándo sale | Qué lleva |
|---|---|---|
| **Bienvenida** | Al crear la cuenta en **Users** | Usuario, contraseña temporal y los pasos para cambiarla |
| **Curso asignado** | Al asignar en **Assignments** | Qué cursos, la fecha límite y el enlace directo a la plataforma |
| **Vencidos** | Una vez al día, por cron | Todo lo que la persona tiene pasado de fecha, en un solo correo |
| **Certificado** | Al aprobar un curso | El PDF adjunto, con copia al administrador |

Los tres primeros se encienden y apagan en **Settings → Automatic emails**. Los dos primeros además
traen una casilla en su propio formulario, por si en un caso puntual no quieres avisar.

Todos los enlaces salen de **Settings → Platform address**. Si ese campo está mal, los correos
llegan con enlaces rotos: tiene que ser la dirección pública, con `https://`.

### Recordatorio diario de vencidos

`recordatorios.py` busca las asignaciones cuya fecha límite ya pasó y que la persona todavía no
aprueba, y manda **un correo por persona** con todo lo que debe, nunca uno por curso. No le escribe
a cuentas inactivas ni sobre cursos ocultos, y si se ejecuta dos veces el mismo día la segunda no
envía nada: antes de escribir revisa en la tabla `correos` si esa persona ya recibió el recordatorio
de hoy.

Probar sin enviar nada:

```bash
docker compose exec -T app python recordatorios.py --probar
```

Programarlo (una sola vez, en el servidor):

```bash
cd /root/AlerionAcademy/plataforma_capacitacion
chmod +x despliegue/recordatorios.sh
(crontab -l 2>/dev/null; echo "0 13 * * 1-5 /root/AlerionAcademy/plataforma_capacitacion/despliegue/recordatorios.sh >> /var/log/alerion-recordatorios.log 2>&1") | crontab -
```

El servidor trabaja en UTC: `13:00` UTC son las 8:00 de la mañana en Colombia. `1-5` lo limita a
días hábiles.

### Por dónde sale el correo

En **Settings → How mail is sent** se elige entre dos caminos:

**Microsoft Graph (HTTPS, puerto 443).** Es el recomendado si la empresa usa Microsoft 365. Envía
por HTTPS, que ningún proveedor bloquea, no guarda contraseñas y no le afecta el retiro de la
autenticación básica que Microsoft tiene en marcha. Necesita tres datos que da el administrador de
Microsoft 365 tras registrar una aplicación en Entra ID con permiso de aplicación `Mail.Send`:
*Directory (tenant) ID*, *Application (client) ID* y un *client secret*. Más el buzón desde el que
se envía.

**SMTP.** El de toda la vida: servidor, puerto, usuario y contraseña. Funciona con cualquier
proveedor, pero **muchos servidores bloquean los puertos de salida 25, 465 y 587**; DigitalOcean
entre ellos. Para comprobarlo desde el servidor:

```bash
timeout 5 bash -c 'cat < /dev/null > /dev/tcp/smtp.office365.com/587' && echo abierto || echo bloqueado
```

Si están bloqueados, SMTP no es una opción desde esa máquina y hay que usar Graph.

Mientras no haya ninguno de los dos configurado, cada mensaje se guarda como archivo `.eml` en
`data/correos_salida/`. Sirve para probar el flujo completo sin servidor de correo: nadie recibe
nada, pero puedes abrir los archivos y ver exactamente qué se habría enviado.

Todos los envíos, con su resultado y el error exacto si falló, quedan listados al final de la
página de configuración. El botón **Send test** verifica la configuración antes de depender de ella.

---

## 4. Estructura

```
app.py              arranque, CSRF, filtros y manejo de errores
auth.py             ingreso, sesión, roles
estudiante.py       cursos asignados, visor, control de avance, examen, certificado
administrador.py    usuarios, cursos, asignaciones, exámenes, reportes, configuración
media.py            procesa el material: pptx→pdf→imágenes, duración de video
certificados.py     genera el PDF del certificado
mailer.py           envío SMTP o bandeja local
database.py         esquema SQLite y utilidades
seed.py             datos de demostración
templates/          páginas HTML
static/             CSS y JavaScript del visor
data/               base de datos, material, certificados y correos (se crea sola)
```

Tablas: `usuarios`, `cursos`, `asignaciones`, `quices`, `preguntas`, `opciones`, `intentos`,
`certificados`, `configuracion`, `correos`.

Estados de una asignación: `pendiente` → `en_progreso` → `aprobado` o `reprobado`.

---

## 5. Puesta en producción

Para publicarla en tu dominio (`https://capacitacion.tuempresa.com`) sigue
**[despliegue/GUIA_DESPLIEGUE.md](despliegue/GUIA_DESPLIEGUE.md)**. Trae tres caminos: un servicio
que la ejecuta por ti, un servidor propio con Docker y HTTPS automático, o un servidor propio con
Nginx instalado por script. Los archivos ya vienen listos: `Dockerfile`, `docker-compose.yml`,
`render.yaml` y la carpeta `despliegue/`.

`python app.py` levanta el servidor de desarrollo de Flask, suficiente para probar en tu
computador. Para uso real siempre va detrás de gunicorn y de Nginx o Caddy, como indica la guía.
Tres cosas por hacer antes de exponerlo:

1. **HTTPS obligatorio.** Con TLS activo, define la variable de entorno `HTTPS=1` para que la
   cookie de sesión solo viaje cifrada.
2. **Llave de sesión.** Se genera sola en `data/secret.key`; si prefieres, define la variable de
   entorno `SECRET_KEY`.
3. **Respaldos.** Copia la carpeta `data/` completa: ahí están la base, el material y los
   certificados emitidos.

Ajustes de reglas en `config.py`: `PORCENTAJE_MIN_VIDEO`, `SEGUNDOS_MIN_POR_PAGINA`,
`SALTO_MAX_SEGUNDOS` y el tamaño máximo de archivo.

### Capacidad y rendimiento

El material pesado (video) lo entrega **Caddy**, no la aplicación. Antes de servir un archivo,
Caddy consulta `/api/authorize`, donde la aplicación confirma en milisegundos si esa persona tiene
el curso asignado. Así el número de personas viendo video a la vez depende del ancho de banda y no
de los procesos de la aplicación.

- `MEDIA_DIRECT=1` activa ese reparto (ya viene puesto en `docker-compose.yml`).
- Sin esa variable, la aplicación sirve el video como antes: útil al desarrollar en local.
- `WORKERS` y `THREADS` en `.env` ajustan la concurrencia. Con 2 GB de RAM, 4 × 4 va cómodo;
  con 4 GB puedes subir a 6 × 4.

Referencias de tamaño: un documento de 30 páginas ocupa unos 15 MB ya convertido a imágenes, y un
video de 10 minutos en 720p unos 100 MB. En 50 GB caben del orden de 400 videos de esa duración.
La base de datos es despreciable: 1 KB por usuario.

### Optimización del almacenamiento

El disco es lo que marca el precio del servidor, así que todo lo que entra se comprime:

| | Antes | Ahora |
|---|---|---|
| Página de documento | ~63 KB (PNG) | ~15 KB (WebP) |
| Video subido | tal cual | recomprimido a 720p H.264 |
| Archivo fuente (PDF/PPTX) | se guardaba | se borra tras renderizar |

La recompresión del video **no ocurre durante la subida**: el módulo queda listo de inmediato con
el archivo original y un proceso en segundo plano lo comprime unos minutos después, actualizando
solo el nombre del archivo. Así una subida grande no bloquea la plataforma ni expira. ffmpeg corre
con un hilo y prioridad baja, para no acaparar la CPU en servidores de un solo núcleo.

> Si el dominio pasa por el proxy de Cloudflare (nube naranja), las subidas fallan con error 524:
> Cloudflare corta a los 100 segundos y limita el archivo a 100 MB en el plan gratuito. Deja los
> registros A en **DNS only** (nube gris).

En material real esto libera alrededor de la mitad del espacio. Los parámetros están al
inicio de `media.py`: `RESOLUCION_VIDEO`, `CRF_VIDEO`, `ESCALA_PAGINA` y `CALIDAD_WEBP`.
Subir `CRF_VIDEO` a 30 o bajar `ESCALA_PAGINA` a 1.4 reduce más, con algo menos de nitidez.

Para el material que ya estaba subido:

```bash
docker compose exec app python optimizar.py --revisar    # cuánto se ahorraría
docker compose exec app python optimizar.py --aplicar    # aplicarlo
```

Ese script también borra las carpetas de material que quedaron sin curso asociado.

### Despliegue automático desde GitHub

`.github/workflows/deploy.yml` actualiza el servidor en cada push a `main`: respalda,
hace `git reset --hard`, reconstruye y comprueba que la aplicación responda. En GitHub,
**Settings → Secrets and variables → Actions**, define `SSH_HOST`, `SSH_USER`, `SSH_KEY`
(clave privada) y `APP_PATH`. En el servidor:

```bash
ssh-keygen -t ed25519 -C "github-deploy" -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
cat ~/.ssh/github_deploy      # el contenido va en el secreto SSH_KEY
```

### Respaldos

`despliegue/respaldo.sh` copia base, material y certificados, y conserva los últimos 14 días:

```bash
chmod +x despliegue/respaldo.sh
(crontab -l 2>/dev/null; echo "15 3 * * * /root/AlerionAcademy/plataforma_capacitacion/despliegue/respaldo.sh") | crontab -
```

### Sobre la protección del material
Las presentaciones y PDF se entregan como imágenes página por página, así que no se pueden editar
ni descargar el archivo original desde el visor. El video se sirve en streaming con el menú de
descarga desactivado. Ninguna plataforma web puede impedir del todo que alguien grabe la pantalla:
para material altamente confidencial se necesita DRM, que no está incluido aquí.


---

## 6. Identidad visual

Todos los colores corporativos están en un solo bloque, al inicio de
`static/css/app.css`:

```css
--brand-ink:  #0e1a28;   /* azul marino: barra lateral, títulos, certificado */
--brand-gold: #c6a15b;   /* dorado: estado activo, sellos, botones destacados */
```

Cambia esos valores y toda la plataforma se actualiza. Los mismos dos colores están
declarados como `NAVY` y `GOLD` al inicio de `certificados.py`, para que el PDF quede igual.

### Logos

| Archivo | Dónde aparece | Recomendación |
|---|---|---|
| `static/img/logo.png` | barra lateral y pantalla de ingreso | PNG con fondo transparente, versión clara, alto ≥ 60 px |
| `static/img/certificate_logo.png` | encabezado del certificado PDF | PNG con fondo transparente, versión oscura, alto ≥ 300 px |
| `static/img/favicon.png` | pestaña del navegador | PNG cuadrado de 64×64 px |

Si un archivo no existe, la plataforma no se rompe: muestra el nombre en texto.
Para subirlos al servidor basta con dejarlos en esas rutas y reconstruir con
`docker compose up -d --build`.

El nombre que aparece en la barra lateral, en los certificados y en los correos se
edita desde **Settings**, sin tocar el código.
