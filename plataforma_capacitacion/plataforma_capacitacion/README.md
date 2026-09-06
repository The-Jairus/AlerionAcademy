# Plataforma de capacitación

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
| Administrador | admin@empresa.com | Admin123* |
| Estudiante | carlos@empresa.com | Curso123* |
| Estudiante | diana@empresa.com | Curso123* |
| Estudiante | jorge@empresa.com | Curso123* |

`python seed.py --reiniciar` borra todo y vuelve a generar la demo.
Para arrancar con la base vacía: borra la carpeta `data/` y ejecuta `python app.py`; luego crea
el primer administrador con `python crear_admin.py`.

---

## 2. Cómo funciona

### Ingreso
Usuario y contraseña contra la tabla `usuarios`. Las contraseñas se guardan con hash PBKDF2-SHA256
(nunca en texto plano). Hay bloqueo temporal tras 8 intentos fallidos y protección CSRF en todos
los formularios.

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

### Vista de administrador
- **Panel**: indicadores, sus propios cursos pendientes, vencidos y últimos certificados.
- **Usuarios**: crear, editar, cambiar contraseña, activar/desactivar y eliminar.
- **Cursos**: crear subiendo el material (video, PDF, PPTX, ODP), editar, ocultar y eliminar.
- **Asignar cursos**: varios cursos a varias personas de una vez, con fecha límite y aviso por correo.
- **Exámenes**: crear el examen de cada curso, definir puntaje mínimo e intentos, y cargar preguntas
  de selección única.
- **Resumen de capacitaciones**: todo lo asignado con filtros; lo pendiente aparece en rojo pastel
  y lo aprobado en blanco.
- **Certificados**: buscador por persona (nombre, correo o documento) y por curso, con descarga del
  PDF y reenvío por correo.
- **Configuración**: datos que salen en el certificado y servidor SMTP.

---

## 3. Correo

En **Configuración** defines el correo del administrador y el servidor SMTP.

- Con SMTP configurado, los certificados salen por ahí (STARTTLS en 587, SSL en 465).
- Sin SMTP, cada mensaje se guarda como archivo `.eml` en `data/correos_salida/` para que puedas
  abrirlo o reenviarlo. Así puedes probar todo el flujo sin servidor de correo.

Todos los envíos, con su resultado, quedan listados al final de la página de configuración.
El botón **Enviar prueba** verifica la configuración antes de depender de ella.

Ejemplo con Microsoft 365: servidor `smtp.office365.com`, puerto `587`, STARTTLS activo, usuario y
contraseña de una cuenta con permiso de envío. Con Gmail hay que usar una contraseña de aplicación.

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

### Sobre la protección del material
Las presentaciones y PDF se entregan como imágenes página por página, así que no se pueden editar
ni descargar el archivo original desde el visor. El video se sirve en streaming con el menú de
descarga desactivado. Ninguna plataforma web puede impedir del todo que alguien grabe la pantalla:
para material altamente confidencial se necesita DRM, que no está incluido aquí.
