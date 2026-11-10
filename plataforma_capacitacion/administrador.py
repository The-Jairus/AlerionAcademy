"""Vista de administrador: usuarios, cursos, asignaciones, quices y reportes."""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   send_file, url_for)

import config
import mailer
import media
import certificados as cert
from auth import (admin_requerido, es_admin, hash_password, instructor_requerido,
                  puede_editar_curso, registrar, usuario_actual)
from database import (ahora, consultar, db, ejecutar, en_zona, guardar_config,
                      hoy_local, obtener_config, sumar_meses, zona_horaria)

bp = Blueprint("admin", __name__, url_prefix="/admin")

# Zonas donde opera o puede operar la organización. Lista corta a propósito:
# un desplegable con las 600 de la base de datos no ayuda a nadie.
ZONAS_HORARIAS = [
    "America/Bogota", "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Mexico_City", "America/Panama", "America/Lima",
    "America/Santiago", "America/Sao_Paulo", "America/Argentina/Buenos_Aires",
    "Europe/Madrid", "Europe/London", "UTC",
]

ESTADOS = {
    "pendiente": "Not started",
    "en_progreso": "In progress",
    "reprobado": "Not passed",
    "aprobado": "Completed",
}


def _volver(destino):
    return redirect(url_for(destino))


# --------------------------------------------------------------------------- bitácora
def _tocar_curso(curso_id):
    """Marca quién modificó el curso y cuándo."""
    yo = usuario_actual()
    ejecutar("UPDATE cursos SET actualizado_en = ?, actualizado_por = ? WHERE id = ?",
             (ahora(), yo["id"] if yo else None, curso_id))


# --------------------------------------------------------------------------- permisos
def _mis_cursos_sql(alias="c"):
    """Filtro SQL: el admin ve todos los cursos; el instructor solo los suyos.

    Devuelve (fragmento, parámetros) para pegar después de un WHERE.
    """
    yo = usuario_actual()
    if es_admin(yo):
        return "", ()
    return (f" AND ({alias}.creado_por = ? OR EXISTS (SELECT 1 FROM curso_instructores ci"
            f"   WHERE ci.curso_id = {alias}.id AND ci.usuario_id = ?))"), (yo["id"], yo["id"])


def _curso_editable(curso_id):
    """Carga el curso y corta el paso si el usuario no puede editarlo."""
    curso = consultar("SELECT c.*, c.id AS curso_id FROM cursos c WHERE c.id = ?",
                      (curso_id,), uno=True)
    if curso is None:
        abort(404)
    if not puede_editar_curso(curso):
        abort(403)
    return curso


def _modulo_editable(modulo_id):
    """Igual que _curso_editable, pero partiendo de un módulo."""
    modulo = consultar(
        "SELECT m.*, c.titulo AS curso, c.creado_por FROM modulos m"
        " JOIN cursos c ON c.id = m.curso_id WHERE m.id = ?", (modulo_id,), uno=True)
    if modulo is None:
        abort(404)
    if not puede_editar_curso(modulo):
        abort(403)
    return modulo


def _datos_certificado(curso=None):
    """Lee del formulario el tipo de certificado y la firma (punto 3)."""
    firmado = request.form.get("cert_tipo") == "signed"
    datos = {
        "cert_tipo": "signed" if firmado else "unsigned",
        "firma_nombre": (request.form.get("firma_nombre") or "").strip() if firmado else "",
        "firma_cargo": (request.form.get("firma_cargo") or "").strip() if firmado else "",
        "firma_archivo": (curso["firma_archivo"] if curso else None) or "",
    }
    imagen = request.files.get("firma_imagen")
    if firmado and imagen is not None and imagen.filename:
        nuevo = media.guardar_firma(imagen)          # puede lanzar ErrorMaterial
        media.eliminar_firma(datos["firma_archivo"])
        datos["firma_archivo"] = nuevo
    elif not firmado:
        media.eliminar_firma(datos["firma_archivo"])
        datos["firma_archivo"] = ""
    return datos


def _datos_vigencia(curso=None):
    """Lee validez y la casilla one time (punto 6)."""
    one_time = 1 if request.form.get("one_time") else 0
    if one_time:
        return 0, 1                       # sin vencimiento
    por_defecto = curso["vigencia_meses"] if curso else 12
    try:
        meses = int(request.form.get("vigencia_meses") or por_defecto or 12)
    except ValueError:
        meses = por_defecto or 12
    return max(1, min(600, meses)), 0


# --------------------------------------------------------------------------- panel
@bp.route("/")
@instructor_requerido
def panel():
    yo = usuario_actual()
    mis_cursos = consultar(
        "SELECT a.*, c.titulo, c.tipo_material FROM asignaciones a"
        " JOIN cursos c ON c.id = a.curso_id"
        " WHERE a.usuario_id = ? AND a.estado <> 'aprobado' AND c.activo = 1"
        " ORDER BY a.fecha_limite IS NULL, a.fecha_limite", (yo["id"],))
    # El instructor ve las cifras de los cursos que diseñó; el admin, las de todos.
    filtro, parametros = _mis_cursos_sql()
    resumen = consultar(
        "SELECT COUNT(*) AS total,"
        " SUM(a.estado = 'aprobado') AS aprobados,"
        " SUM(a.estado <> 'aprobado') AS pendientes FROM asignaciones a"
        " JOIN cursos c ON c.id = a.curso_id WHERE 1 = 1" + filtro,
        parametros, uno=True)
    usuarios_activos = consultar(
        "SELECT COUNT(*) AS n FROM usuarios WHERE activo = 1", uno=True)["n"]
    cursos_activos = consultar(
        "SELECT COUNT(*) AS n FROM cursos c WHERE c.activo = 1" + filtro,
        parametros, uno=True)["n"]
    vencidos = consultar(
        "SELECT a.*, c.titulo, u.nombre AS usuario FROM asignaciones a"
        " JOIN cursos c ON c.id = a.curso_id JOIN usuarios u ON u.id = a.usuario_id"
        " WHERE a.estado <> 'aprobado' AND a.fecha_limite IS NOT NULL"
        # Un curso one time sigue teniendo fecha límite de ejecución, pero
        # nunca caduca después de aprobarse.
        " AND a.fecha_limite < ?" + filtro + " ORDER BY a.fecha_limite",
        (hoy_local(),) + parametros)
    ultimos = consultar(
        "SELECT ce.*, u.nombre AS usuario, c.titulo FROM certificados ce"
        " JOIN usuarios u ON u.id = ce.usuario_id JOIN cursos c ON c.id = ce.curso_id"
        " WHERE 1 = 1" + filtro + " ORDER BY ce.emitido_en DESC LIMIT 6", parametros)
    return render_template("admin/panel.html", mis_cursos=mis_cursos, resumen=resumen,
                           usuarios_activos=usuarios_activos, cursos_activos=cursos_activos,
                           vencidos=vencidos, ultimos=ultimos)


# --------------------------------------------------------------------------- usuarios
@bp.route("/users")
@admin_requerido
def usuarios():
    busqueda = (request.args.get("q") or "").strip()
    sql = ("SELECT u.*, (SELECT COUNT(*) FROM asignaciones a WHERE a.usuario_id = u.id)"
           " AS cursos FROM usuarios u")
    parametros = ()
    if busqueda:
        sql += " WHERE u.nombre LIKE ? OR u.email LIKE ?"
        comodin = f"%{busqueda}%"
        parametros = (comodin, comodin)
    sql += " ORDER BY u.activo DESC, u.nombre"
    return render_template("admin/usuarios.html", usuarios=consultar(sql, parametros),
                           q=busqueda, roles=config.ROLES)


@bp.post("/users/create")
@admin_requerido
def crear_usuario():
    nombre = (request.form.get("nombre") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    clave = request.form.get("password") or ""
    if not nombre or not email or len(clave) < 8:
        flash("Name, email and a password of at least 8 characters are required.", "error")
        return _volver("admin.usuarios")
    if consultar("SELECT id FROM usuarios WHERE email = ?", (email,), uno=True):
        flash(f"A user with the email {email} already exists.", "error")
        return _volver("admin.usuarios")
    ejecutar("INSERT INTO usuarios (nombre, email, cargo, password_hash, rol,"
             " activo, creado_en) VALUES (?, ?, ?, ?, ?, 1, ?)",
             (nombre, email, (request.form.get("cargo") or "").strip(),
              hash_password(clave), _rol_del_formulario(), ahora()))

    # La contraseña va en el correo porque es temporal: el mensaje le pide a la
    # persona cambiarla en cuanto entre.
    ajustes = obtener_config()
    if request.form.get("bienvenida") and mailer.activo(ajustes, "email_bienvenida"):
        mailer.enviar(ajustes, email, mailer.asunto_bienvenida(ajustes),
                      mailer.cuerpo_bienvenida({"nombre": nombre, "email": email},
                                               clave, ajustes))
        flash(f"User {nombre} created. A welcome email with the temporary password was "
              f"sent to {email}.", "ok")
    else:
        flash(f"User {nombre} created. No welcome email was sent: give them the password "
              "yourself.", "ok")
    registrar("usuario.crear", nombre, None, f"{email} · {_rol_del_formulario()}")
    return _volver("admin.usuarios")


@bp.post("/users/<int:usuario_id>/edit")
@admin_requerido
def editar_usuario(usuario_id):
    usuario = consultar("SELECT * FROM usuarios WHERE id = ?", (usuario_id,), uno=True)
    if usuario is None:
        abort(404)
    nombre = (request.form.get("nombre") or usuario["nombre"]).strip()
    rol = _rol_del_formulario(usuario["rol"])
    if usuario["rol"] == "admin" and rol != "admin" and _solo_queda_un_admin(usuario_id):
        flash("At least one active administrator must remain.", "error")
        return _volver("admin.usuarios")
    ejecutar("UPDATE usuarios SET nombre = ?, cargo = ?, rol = ? WHERE id = ?",
             (nombre, (request.form.get("cargo") or "").strip(), rol, usuario_id))
    nueva = request.form.get("password") or ""
    if nueva:
        if len(nueva) < 8:
            flash("The password must be at least 8 characters long.", "error")
            return _volver("admin.usuarios")
        ejecutar("UPDATE usuarios SET password_hash = ? WHERE id = ?",
                 (hash_password(nueva), usuario_id))
        # La contraseña nueva también es temporal: se avisa con el mismo texto.
        ajustes = obtener_config()
        if request.form.get("avisar") and mailer.activo(ajustes, "email_bienvenida"):
            mailer.enviar(ajustes, usuario["email"], "Your training platform password was reset",
                          mailer.cuerpo_bienvenida(
                              {"nombre": nombre, "email": usuario["email"]}, nueva, ajustes))
            flash(f"{nombre} updated. The new password was emailed to "
                  f"{usuario['email']}.", "ok")
            return _volver("admin.usuarios")
    registrar("usuario.editar", nombre, usuario_id)
    flash(f"{nombre} updated.", "ok")
    return _volver("admin.usuarios")


def _rol_del_formulario(por_defecto="learner"):
    rol = (request.form.get("rol") or "").strip()
    return rol if rol in config.ROLES else por_defecto


def _solo_queda_un_admin(excluyendo_id):
    fila = consultar("SELECT COUNT(*) AS n FROM usuarios WHERE rol = 'admin' AND activo = 1"
                     " AND id <> ?", (excluyendo_id,), uno=True)
    return fila["n"] == 0


@bp.post("/users/<int:usuario_id>/status")
@admin_requerido
def cambiar_estado_usuario(usuario_id):
    usuario = consultar("SELECT * FROM usuarios WHERE id = ?", (usuario_id,), uno=True)
    if usuario is None:
        abort(404)
    nuevo = 0 if usuario["activo"] else 1
    if not nuevo and usuario["rol"] == "admin" and _solo_queda_un_admin(usuario_id):
        flash("At least one active administrator must remain.", "error")
        return _volver("admin.usuarios")
    ejecutar("UPDATE usuarios SET activo = ? WHERE id = ?", (nuevo, usuario_id))
    # Sin esto la red de respaldo lo anota con el nombre interno de la ruta y
    # en el registro se lee "admin.cambiar_estado_usuario" en vez de la frase.
    registrar("usuario.estado", usuario["nombre"], "activated" if nuevo else "deactivated")
    flash(f"{usuario['nombre']} is now {'active' if nuevo else 'inactive'}.", "ok")
    return _volver("admin.usuarios")


@bp.post("/users/<int:usuario_id>/delete")
@admin_requerido
def eliminar_usuario(usuario_id):
    usuario = consultar("SELECT * FROM usuarios WHERE id = ?", (usuario_id,), uno=True)
    if usuario is None:
        abort(404)
    if usuario["id"] == usuario_actual()["id"]:
        flash("You cannot delete your own account.", "error")
        return _volver("admin.usuarios")
    if usuario["rol"] == "admin" and _solo_queda_un_admin(usuario_id):
        flash("At least one active administrator must remain.", "error")
        return _volver("admin.usuarios")
    ejecutar("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
    flash(f"{usuario['nombre']} and their training history were deleted.", "ok")
    return _volver("admin.usuarios")


# --------------------------------------------------------------------------- cursos
@bp.route("/courses")
@instructor_requerido
def cursos():
    filtro, parametros = _mis_cursos_sql()
    lista = consultar(
        "SELECT c.*, (SELECT COUNT(*) FROM modulos m WHERE m.curso_id = c.id) AS modulos,"
        " (SELECT COUNT(*) FROM asignaciones a WHERE a.curso_id = c.id) AS asignados,"
        " (SELECT COUNT(*) FROM asignaciones a WHERE a.curso_id = c.id AND a.estado='aprobado')"
        " AS aprobados,"
        " (SELECT COUNT(*) FROM modulos m WHERE m.curso_id = c.id"
        "   AND m.quiz_id IS NOT NULL) AS con_examen,"
        " (SELECT u.nombre FROM usuarios u WHERE u.id = c.creado_por) AS autor,"
        " (SELECT u.nombre FROM usuarios u WHERE u.id = c.actualizado_por) AS editor,"
        " (SELECT COUNT(*) FROM contenidos co JOIN modulos m2 ON m2.id = co.modulo_id"
        "   WHERE m2.curso_id = c.id) AS materiales"
        " FROM cursos c WHERE 1 = 1" + filtro + " ORDER BY c.activo DESC, c.titulo",
        parametros)
    return render_template("admin/cursos.html", cursos=lista)


@bp.post("/courses/create")
@instructor_requerido
def crear_curso():
    titulo = (request.form.get("titulo") or "").strip()
    if not titulo:
        flash("The course needs a title.", "error")
        return _volver("admin.cursos")
    try:
        horas = float(request.form.get("horas") or 1)
    except ValueError:
        horas = 1
    vigencia, one_time = _datos_vigencia()
    try:
        firma = _datos_certificado()
    except media.ErrorMaterial as error:
        flash(str(error), "error")
        return _volver("admin.cursos")

    curso_id = ejecutar(
        "INSERT INTO cursos (titulo, descripcion, categoria, horas, vigencia_meses,"
        " one_time, creado_por, cert_tipo, firma_nombre, firma_cargo, firma_archivo,"
        " activo, creado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (titulo, (request.form.get("descripcion") or "").strip(),
         (request.form.get("categoria") or "").strip(), horas, vigencia, one_time,
         usuario_actual()["id"], firma["cert_tipo"], firma["firma_nombre"],
         firma["firma_cargo"], firma["firma_archivo"], ahora()))
    _tocar_curso(curso_id)
    registrar("curso.crear", titulo, curso_id)
    flash(f"Course “{titulo}” created. Now add its modules.", "ok")
    return redirect(url_for("admin.curso_detalle", curso_id=curso_id))


@bp.route("/courses/<int:curso_id>")
@instructor_requerido
def curso_detalle(curso_id):
    curso = _curso_editable(curso_id)
    curso = dict(curso)
    curso["actualizado_nombre"] = (consultar(
        "SELECT nombre FROM usuarios WHERE id = ?", (curso["actualizado_por"],),
        uno=True) or {"nombre": None})["nombre"] if curso.get("actualizado_por") else None
    modulos = consultar(
        "SELECT m.*, q.titulo AS quiz_titulo, q.puntaje_minimo,"
        " q.intentos_max, q.preguntas_por_intento,"
        " (SELECT COUNT(*) FROM preguntas p WHERE p.quiz_id = q.id) AS preguntas,"
        " (SELECT COUNT(*) FROM modulos o WHERE o.quiz_id = m.quiz_id) AS comparten"
        " FROM modulos m LEFT JOIN quices q ON q.id = m.quiz_id"
        " WHERE m.curso_id = ? ORDER BY m.orden, m.id", (curso_id,))
    contenidos = {}
    for fila in consultar(
            "SELECT co.* FROM contenidos co JOIN modulos m ON m.id = co.modulo_id"
            " WHERE m.curso_id = ? ORDER BY co.modulo_id, co.orden, co.id", (curso_id,)):
        contenidos.setdefault(fila["modulo_id"], []).append(fila)
    opiniones = consultar(
        "SELECT e.*, u.nombre AS usuario FROM encuestas e JOIN usuarios u ON u.id = e.usuario_id"
        " WHERE e.curso_id = ? ORDER BY e.creado_en DESC LIMIT 25", (curso_id,))
    resumen_encuesta = consultar(
        "SELECT COUNT(*) AS n, AVG(estrellas) AS promedio FROM encuestas WHERE curso_id = ?",
        (curso_id,), uno=True)
    return render_template("admin/curso_detalle.html", curso=curso, modulos=modulos,
                           opiniones=opiniones, resumen_encuesta=resumen_encuesta,
                           contenidos=contenidos, total_modulos=len(modulos),
                           examenes=_examenes_disponibles(),
                           instructores=_instructores_del_curso(curso_id),
                           candidatos=_instructores_posibles(curso_id),
                           extensiones=sorted(config.EXT_PERMITIDAS))


def _instructores_del_curso(curso_id):
    """Dueño del curso primero, luego los instructores agregados."""
    return consultar(
        "SELECT u.id, u.nombre, u.email, 1 AS es_dueno FROM cursos c"
        "   JOIN usuarios u ON u.id = c.creado_por WHERE c.id = ?"
        " UNION ALL"
        " SELECT u.id, u.nombre, u.email, 0 AS es_dueno FROM curso_instructores ci"
        "   JOIN usuarios u ON u.id = ci.usuario_id WHERE ci.curso_id = ?"
        " ORDER BY es_dueno DESC, nombre", (curso_id, curso_id))


def _instructores_posibles(curso_id):
    """Instructores y admins que todavía no están en el curso."""
    return consultar(
        "SELECT id, nombre, email, rol FROM usuarios"
        " WHERE activo = 1 AND rol IN ('instructor', 'admin')"
        "   AND id <> IFNULL((SELECT creado_por FROM cursos WHERE id = ?), 0)"
        "   AND id NOT IN (SELECT usuario_id FROM curso_instructores WHERE curso_id = ?)"
        " ORDER BY nombre", (curso_id, curso_id))


@bp.post("/courses/<int:curso_id>/instructors")
@instructor_requerido
def agregar_instructor(curso_id):
    """Suma otro instructor que también podrá editar este curso."""
    _curso_editable(curso_id)
    usuario_id = (request.form.get("usuario_id") or "").strip()
    if not usuario_id.isdigit():
        flash("Choose an instructor to add.", "error")
        return redirect(url_for("admin.curso_detalle", curso_id=curso_id))
    persona = consultar("SELECT * FROM usuarios WHERE id = ? AND activo = 1",
                        (int(usuario_id),), uno=True)
    if persona is None or persona["rol"] not in ("instructor", "admin"):
        flash("Only instructors and administrators can be added to a course.", "error")
        return redirect(url_for("admin.curso_detalle", curso_id=curso_id))
    ejecutar("INSERT OR IGNORE INTO curso_instructores (curso_id, usuario_id, agregado_en)"
             " VALUES (?, ?, ?)", (curso_id, persona["id"], ahora()))
    registrar("curso.instructor_agregar", persona["nombre"], curso_id)
    flash(f"{persona['nombre']} can now edit this course.", "ok")
    return redirect(url_for("admin.curso_detalle", curso_id=curso_id))


@bp.post("/courses/<int:curso_id>/instructors/<int:usuario_id>/delete")
@instructor_requerido
def quitar_instructor(curso_id, usuario_id):
    _curso_editable(curso_id)
    persona = consultar("SELECT nombre FROM usuarios WHERE id = ?", (usuario_id,), uno=True)
    ejecutar("DELETE FROM curso_instructores WHERE curso_id = ? AND usuario_id = ?",
             (curso_id, usuario_id))
    registrar("curso.instructor_quitar", persona["nombre"] if persona else "", curso_id)
    flash("Instructor removed from this course.", "ok")
    return redirect(url_for("admin.curso_detalle", curso_id=curso_id))


@bp.post("/courses/<int:curso_id>/modules")
@instructor_requerido
def agregar_modulo(curso_id):
    """Crea el módulo con su primer material. Los demás se agregan después."""
    _curso_editable(curso_id)
    titulo = (request.form.get("titulo") or "").strip()
    archivo = request.files.get("material")
    if not titulo or archivo is None or not archivo.filename:
        flash("A module needs a title and at least one material file.", "error")
        return redirect(url_for("admin.curso_detalle", curso_id=curso_id))

    orden = consultar("SELECT IFNULL(MAX(orden), 0) + 1 AS siguiente FROM modulos"
                      " WHERE curso_id = ?", (curso_id,), uno=True)["siguiente"]
    modulo_id = ejecutar(
        "INSERT INTO modulos (curso_id, titulo, descripcion, orden, creado_en)"
        " VALUES (?, ?, ?, ?, ?)",
        (curso_id, titulo, (request.form.get("descripcion") or "").strip(), orden, ahora()))

    try:
        detalle = _agregar_contenido(modulo_id, archivo, request.form.get("contenido_titulo"))
    except media.ErrorMaterial as error:
        ejecutar("DELETE FROM modulos WHERE id = ?", (modulo_id,))   # no dejar basura
        flash(str(error), "error")
        return redirect(url_for("admin.curso_detalle", curso_id=curso_id))
    except Exception as error:
        ejecutar("DELETE FROM modulos WHERE id = ?", (modulo_id,))
        flash(f"The material could not be processed: {error}", "error")
        return redirect(url_for("admin.curso_detalle", curso_id=curso_id))

    _tocar_curso(curso_id)
    registrar("modulo.crear", titulo, modulo_id, f"Course #{curso_id} · {detalle}")

    quiz_id = _aplicar_examen(modulo_id, titulo)
    if quiz_id is None:
        flash(f"Module \u201c{titulo}\u201d added with {detalle}. You can add more material to "
              "it below. It has no exam: finishing its content is enough to complete it.", "ok")
        return redirect(url_for("admin.curso_detalle", curso_id=curso_id))
    flash(f"Module \u201c{titulo}\u201d added with {detalle}.", "ok")
    return redirect(url_for("admin.editar_quiz", quiz_id=quiz_id))


# --------------------------------------------------------------------------- contenidos
def _agregar_contenido(modulo_id, archivo, titulo=None):
    """Procesa el archivo y lo cuelga del módulo. Devuelve cómo describirlo."""
    info = media.guardar_material(archivo)
    orden = consultar("SELECT IFNULL(MAX(orden), 0) + 1 AS siguiente FROM contenidos"
                      " WHERE modulo_id = ?", (modulo_id,), uno=True)["siguiente"]
    contenido_id = ejecutar(
        "INSERT INTO contenidos (modulo_id, titulo, orden, tipo_material, carpeta, archivo,"
        " archivo_original, duracion_segundos, paginas, creado_en)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (modulo_id, (titulo or "").strip() or None, orden, info["tipo_material"],
         info["carpeta"], info["archivo"], info["archivo_original"],
         info["duracion_segundos"], info["paginas"], ahora()))
    if info["tipo_material"] == "video":
        # La recompresión sigue en segundo plano: la subida ya respondió.
        media.optimizar_en_segundo_plano(contenido_id, info["carpeta"], info["archivo"])
        return f"{info['duracion_segundos'] / 60:.0f} minutes of video"
    return f"{info['paginas']} pages"


def _renumerar_contenidos(modulo_id):
    for posicion, fila in enumerate(
            consultar("SELECT id FROM contenidos WHERE modulo_id = ? ORDER BY orden, id",
                      (modulo_id,)), start=1):
        ejecutar("UPDATE contenidos SET orden = ? WHERE id = ?", (posicion, fila["id"]))


def _contenido_editable(contenido_id):
    fila = consultar(
        "SELECT co.*, m.curso_id, c.creado_por FROM contenidos co"
        " JOIN modulos m ON m.id = co.modulo_id JOIN cursos c ON c.id = m.curso_id"
        " WHERE co.id = ?", (contenido_id,), uno=True)
    if fila is None:
        abort(404)
    if not puede_editar_curso(fila):
        abort(403)
    return fila


@bp.post("/modules/<int:modulo_id>/content")
@instructor_requerido
def agregar_contenido(modulo_id):
    """Suma otro material al módulo: una presentación y un video, por ejemplo."""
    modulo = _modulo_editable(modulo_id)
    archivo = request.files.get("material")
    if archivo is None or not archivo.filename:
        flash("Choose a file to add to this module.", "error")
        return redirect(url_for("admin.curso_detalle", curso_id=modulo["curso_id"]))
    try:
        detalle = _agregar_contenido(modulo_id, archivo, request.form.get("contenido_titulo"))
    except media.ErrorMaterial as error:
        flash(str(error), "error")
        return redirect(url_for("admin.curso_detalle", curso_id=modulo["curso_id"]))
    except Exception as error:
        flash(f"The material could not be processed: {error}", "error")
        return redirect(url_for("admin.curso_detalle", curso_id=modulo["curso_id"]))

    # Hay material nuevo que nadie ha visto: el módulo vuelve a estar incompleto
    # para quien ya lo había recorrido.
    ejecutar("UPDATE avance_modulos SET material_completo = 0 WHERE modulo_id = ?", (modulo_id,))
    _tocar_curso(modulo["curso_id"])
    registrar("contenido.agregar", modulo["titulo"], modulo_id, detalle)
    flash(f"Material added to \u201c{modulo['titulo']}\u201d ({detalle}). Anyone who had already "
          "finished this module will have to review the new material.", "ok")
    return redirect(url_for("admin.curso_detalle", curso_id=modulo["curso_id"]))


@bp.post("/content/<int:contenido_id>/delete")
@instructor_requerido
def eliminar_contenido(contenido_id):
    contenido = _contenido_editable(contenido_id)
    cuantos = consultar("SELECT COUNT(*) AS n FROM contenidos WHERE modulo_id = ?",
                        (contenido["modulo_id"],), uno=True)["n"]
    if cuantos <= 1:
        flash("A module needs at least one material. Add another one before removing this.",
              "error")
        return redirect(url_for("admin.curso_detalle", curso_id=contenido["curso_id"]))
    media.eliminar_material(contenido["carpeta"])
    ejecutar("DELETE FROM contenidos WHERE id = ?", (contenido_id,))
    _renumerar_contenidos(contenido["modulo_id"])
    _tocar_curso(contenido["curso_id"])
    registrar("contenido.eliminar", contenido["archivo_original"], contenido_id)
    flash("Material removed from the module.", "ok")
    return redirect(url_for("admin.curso_detalle", curso_id=contenido["curso_id"]))


@bp.post("/content/<int:contenido_id>/move")
@instructor_requerido
def mover_contenido(contenido_id):
    contenido = _contenido_editable(contenido_id)
    arriba = request.form.get("direccion") == "up"
    vecino = consultar(
        "SELECT id, orden FROM contenidos WHERE modulo_id = ? AND orden "
        + ("< ? ORDER BY orden DESC" if arriba else "> ? ORDER BY orden ASC") + " LIMIT 1",
        (contenido["modulo_id"], contenido["orden"]), uno=True)
    if vecino:
        ejecutar("UPDATE contenidos SET orden = ? WHERE id = ?", (vecino["orden"], contenido_id))
        ejecutar("UPDATE contenidos SET orden = ? WHERE id = ?",
                 (contenido["orden"], vecino["id"]))
        _renumerar_contenidos(contenido["modulo_id"])
        _tocar_curso(contenido["curso_id"])
    return redirect(url_for("admin.curso_detalle", curso_id=contenido["curso_id"]))


@bp.post("/content/<int:contenido_id>/replace")
@instructor_requerido
def reemplazar_contenido(contenido_id):
    """Cambia el archivo de un material conservando su lugar y su nombre.

    Se procesa el archivo nuevo ANTES de borrar el viejo: si la conversión
    falla, el módulo se queda como estaba en vez de perder el material.
    """
    contenido = _contenido_editable(contenido_id)
    volver = redirect(url_for("admin.curso_detalle", curso_id=contenido["curso_id"]))
    archivo = request.files.get("material")
    if archivo is None or not archivo.filename:
        flash("Choose the file that replaces this material.", "error")
        return volver
    try:
        info = media.guardar_material(archivo)
    except media.ErrorMaterial as error:
        flash(str(error), "error")
        return volver
    except Exception as error:
        flash(f"The material could not be processed: {error}", "error")
        return volver

    anterior = contenido["carpeta"]
    ejecutar("UPDATE contenidos SET tipo_material = ?, carpeta = ?, archivo = ?,"
             " archivo_original = ?, duracion_segundos = ?, paginas = ? WHERE id = ?",
             (info["tipo_material"], info["carpeta"], info["archivo"],
              info["archivo_original"], info["duracion_segundos"], info["paginas"],
              contenido_id))
    media.eliminar_material(anterior)
    if info["tipo_material"] == "video":
        media.optimizar_en_segundo_plano(contenido_id, info["carpeta"], info["archivo"])

    # El contenido es otro: quien ya lo había visto tiene que volver a recorrerlo,
    # y su módulo deja de estar completo.
    ejecutar("UPDATE avance_contenidos SET max_posicion = 0, avance = 0, completo = 0,"
             " completado_en = NULL WHERE contenido_id = ?", (contenido_id,))
    ejecutar("UPDATE avance_modulos SET material_completo = 0 WHERE modulo_id = ?",
             (contenido["modulo_id"],))

    _tocar_curso(contenido["curso_id"])
    registrar("contenido.reemplazar", contenido["titulo"] or info["archivo_original"],
              contenido_id, f"was {contenido['archivo_original']}")
    detalle = (f"{info['duracion_segundos'] / 60:.0f} minutes of video"
               if info["tipo_material"] == "video" else f"{info['paginas']} pages")
    flash(f"Material replaced ({detalle}). Anyone who had already gone through it will have to "
          "review the new version.", "ok")
    return volver


@bp.post("/content/<int:contenido_id>/rename")
@instructor_requerido
def renombrar_contenido(contenido_id):
    contenido = _contenido_editable(contenido_id)
    ejecutar("UPDATE contenidos SET titulo = ? WHERE id = ?",
             ((request.form.get("titulo") or "").strip() or None, contenido_id))
    _tocar_curso(contenido["curso_id"])
    flash("Material renamed.", "ok")
    return redirect(url_for("admin.curso_detalle", curso_id=contenido["curso_id"]))


@bp.post("/modules/<int:modulo_id>/delete")
@instructor_requerido
def eliminar_modulo(modulo_id):
    modulo = _modulo_editable(modulo_id)
    # Cada contenido tiene su propia carpeta de material: se borran todas.
    for contenido in consultar("SELECT carpeta FROM contenidos WHERE modulo_id = ?",
                               (modulo_id,)):
        media.eliminar_material(contenido["carpeta"])
    media.eliminar_material(modulo["carpeta"])      # material viejo, de antes de contenidos
    ejecutar("DELETE FROM modulos WHERE id = ?", (modulo_id,))
    _renumerar_modulos(modulo["curso_id"])
    _tocar_curso(modulo["curso_id"])
    registrar("modulo.eliminar", modulo["titulo"], modulo_id)
    flash("Module deleted.", "ok")
    return redirect(url_for("admin.curso_detalle", curso_id=modulo["curso_id"]))


@bp.post("/modules/<int:modulo_id>/edit")
@instructor_requerido
def editar_modulo(modulo_id):
    """Cambia el título, la descripción y el examen del módulo.

    El material ya no se toca aquí: cada contenido se agrega, reordena o quita
    por su cuenta, porque un módulo puede llevar varios.
    """
    modulo = _modulo_editable(modulo_id)
    titulo = (request.form.get("titulo") or "").strip()
    if not titulo:
        flash("The module needs a title.", "error")
        return redirect(url_for("admin.curso_detalle", curso_id=modulo["curso_id"]))

    ejecutar("UPDATE modulos SET titulo = ?, descripcion = ? WHERE id = ?",
             (titulo, (request.form.get("descripcion") or "").strip(), modulo_id))
    if "examen" in request.form:
        _aplicar_examen(modulo_id, titulo)
    _tocar_curso(modulo["curso_id"])
    registrar("modulo.editar", titulo, modulo_id)
    flash(f"Module \u201c{titulo}\u201d updated.", "ok")
    return redirect(url_for("admin.curso_detalle", curso_id=modulo["curso_id"]))


def _renumerar_modulos(curso_id):
    """Deja el orden en 1, 2, 3... sin huecos después de borrar o mover."""
    for posicion, fila in enumerate(
            consultar("SELECT id FROM modulos WHERE curso_id = ? ORDER BY orden, id",
                      (curso_id,)), start=1):
        ejecutar("UPDATE modulos SET orden = ? WHERE id = ?", (posicion, fila["id"]))


@bp.post("/modules/<int:modulo_id>/move")
@instructor_requerido
def mover_modulo(modulo_id):
    modulo = _modulo_editable(modulo_id)
    direccion = -1 if request.form.get("direccion") == "up" else 1
    vecino = consultar(
        "SELECT * FROM modulos WHERE curso_id = ? AND orden " +
        ("< ? ORDER BY orden DESC" if direccion < 0 else "> ? ORDER BY orden ASC") + " LIMIT 1",
        (modulo["curso_id"], modulo["orden"]), uno=True)
    if vecino:
        ejecutar("UPDATE modulos SET orden = ? WHERE id = ?", (vecino["orden"], modulo_id))
        ejecutar("UPDATE modulos SET orden = ? WHERE id = ?", (modulo["orden"], vecino["id"]))
        _renumerar_modulos(modulo["curso_id"])
    return redirect(url_for("admin.curso_detalle", curso_id=modulo["curso_id"]))


@bp.post("/courses/<int:curso_id>/edit")
@instructor_requerido
def editar_curso(curso_id):
    curso = _curso_editable(curso_id)
    vigencia, one_time = _datos_vigencia(curso)
    try:
        firma = _datos_certificado(curso)
    except media.ErrorMaterial as error:
        flash(str(error), "error")
        return redirect(request.referrer or url_for("admin.cursos"))
    ejecutar("UPDATE cursos SET titulo = ?, descripcion = ?, categoria = ?, horas = ?,"
             " vigencia_meses = ?, one_time = ?, cert_tipo = ?, firma_nombre = ?,"
             " firma_cargo = ?, firma_archivo = ? WHERE id = ?",
             ((request.form.get("titulo") or curso["titulo"]).strip(),
              (request.form.get("descripcion") or "").strip(),
              (request.form.get("categoria") or "").strip(),
              float(request.form.get("horas") or curso["horas"]),
              vigencia, one_time, firma["cert_tipo"], firma["firma_nombre"],
              firma["firma_cargo"], firma["firma_archivo"], curso_id))
    flash("Course updated.", "ok")
    return redirect(request.referrer or url_for("admin.cursos"))


@bp.post("/courses/<int:curso_id>/status")
@instructor_requerido
def cambiar_estado_curso(curso_id):
    curso = _curso_editable(curso_id)
    ejecutar("UPDATE cursos SET activo = ? WHERE id = ?", (0 if curso["activo"] else 1, curso_id))
    _tocar_curso(curso_id)
    registrar("curso.estado", curso["titulo"], curso_id,
              "hidden" if curso["activo"] else "published")
    flash("Course visibility updated.", "ok")
    return _volver("admin.cursos")


@bp.post("/courses/<int:curso_id>/delete")
@instructor_requerido
def eliminar_curso(curso_id):
    curso = _curso_editable(curso_id)
    for modulo in consultar("SELECT carpeta FROM modulos WHERE curso_id = ?", (curso_id,)):
        media.eliminar_material(modulo["carpeta"])
    media.eliminar_material(curso["carpeta"])
    media.eliminar_firma(curso["firma_archivo"])
    ejecutar("DELETE FROM cursos WHERE id = ?", (curso_id,))
    flash(f"Course “{curso['titulo']}” deleted.", "ok")
    return _volver("admin.cursos")


# --------------------------------------------------------------------------- preview
@bp.route("/courses/<int:curso_id>/preview")
@instructor_requerido
def previsualizar_curso(curso_id):
    """El curso tal como lo verá el aprendiz, sin registrar ningún avance.

    No se crea asignación ni avance: todo se arma en memoria, así que mirar el
    curso no ensucia los reportes ni emite certificados.
    """
    curso = _curso_editable(curso_id)
    modulos = consultar(
        "SELECT m.*, q.titulo AS quiz_titulo, q.puntaje_minimo, q.intentos_max,"
        " q.preguntas_por_intento,"
        " (SELECT COUNT(*) FROM preguntas p WHERE p.quiz_id = q.id) AS banco"
        " FROM modulos m LEFT JOIN quices q ON q.id = m.quiz_id"
        " WHERE m.curso_id = ? ORDER BY m.orden, m.id", (curso_id,))
    bloques = []
    for modulo in modulos:
        bloques.append({
            "m": modulo,
            "contenidos": consultar(
                "SELECT * FROM contenidos WHERE modulo_id = ? ORDER BY orden, id",
                (modulo["id"],)),
            "preguntas": consultar(
                "SELECT p.id, p.enunciado FROM preguntas p WHERE p.quiz_id = ?"
                " ORDER BY p.orden, p.id", (modulo["quiz_id"],)) if modulo["quiz_id"] else [],
        })
    # Se vuelve por donde se entró: desde la lista de cursos, a la lista; desde
    # el curso, al curso. Solo se admiten rutas propias, no URLs externas.
    volver = request.args.get("volver") or ""
    if not volver.startswith("/admin/") or volver.startswith("//"):
        volver = url_for("admin.cursos")
    return render_template("admin/curso_preview.html", curso=curso, bloques=bloques,
                           volver=volver,
                           total_contenidos=sum(len(b["contenidos"]) for b in bloques))


@bp.route("/preview/<int:contenido_id>/page/<int:numero>.png")
@instructor_requerido
def preview_pagina(contenido_id, numero):
    """Una página del material, para el preview. Sin bloqueo de avance."""
    contenido = _contenido_editable(contenido_id)
    if contenido["tipo_material"] != "documento":
        abort(404)
    if numero < 1 or numero > (contenido["paginas"] or 0):
        abort(404)
    ruta = media.ruta_pagina(contenido, numero)
    if not ruta.exists():
        abort(404)
    return send_file(ruta, mimetype="image/webp" if ruta.suffix == ".webp" else "image/png",
                     max_age=3600)


@bp.route("/preview/<int:contenido_id>/video")
@instructor_requerido
def preview_video(contenido_id):
    contenido = _contenido_editable(contenido_id)
    if contenido["tipo_material"] != "video":
        abort(404)
    ruta = media.ruta_video(contenido)
    if not ruta.exists():
        abort(404)
    respuesta = send_file(ruta, conditional=True, max_age=0)
    respuesta.headers["Content-Disposition"] = "inline"
    return respuesta


# --------------------------------------------------------------------------- asignaciones
@bp.route("/assignments")
@instructor_requerido
def asignaciones():
    """Asignaciones agrupadas por curso: una fila por curso, desplegable."""
    filtro, parametros = _mis_cursos_sql()
    busqueda = (request.args.get("q") or "").strip()
    hoy = hoy_local()

    sql = ("SELECT a.*, u.nombre AS usuario, u.email, u.cargo, c.id AS curso_id,"
           " c.titulo AS curso, c.activo AS curso_activo,"
           " ce.codigo AS certificado,"
           " (SELECT MAX(i.puntaje) FROM intentos i WHERE i.asignacion_id = a.id) AS puntaje"
           " FROM asignaciones a"
           " JOIN usuarios u ON u.id = a.usuario_id"
           " JOIN cursos c ON c.id = a.curso_id"
           " LEFT JOIN certificados ce ON ce.usuario_id = a.usuario_id"
           "   AND ce.curso_id = a.curso_id"
           " WHERE 1 = 1" + filtro)
    valores = list(parametros)
    if busqueda:
        sql += " AND (u.nombre LIKE ? OR u.email LIKE ? OR c.titulo LIKE ?)"
        valores += [f"%{busqueda}%"] * 3
    sql += " ORDER BY c.titulo, u.nombre"

    grupos, orden = {}, []
    for fila in consultar(sql, tuple(valores)):
        clave = fila["curso_id"]
        if clave not in grupos:
            grupos[clave] = {"curso_id": clave, "titulo": fila["curso"],
                             "activo": fila["curso_activo"], "personas": [],
                             "aprobados": 0, "vencidos": 0}
            orden.append(clave)
        vencido = bool(fila["fecha_limite"] and fila["fecha_limite"] < hoy
                       and fila["estado"] != "aprobado")
        grupos[clave]["personas"].append({"a": fila, "vencido": vencido})
        if fila["estado"] == "aprobado":
            grupos[clave]["aprobados"] += 1
        if vencido:
            grupos[clave]["vencidos"] += 1

    return render_template(
        "admin/asignaciones.html", grupos=[grupos[k] for k in orden],
        usuarios=consultar("SELECT id, nombre, email, cargo, rol FROM usuarios"
                           " WHERE activo = 1 ORDER BY nombre"),
        cursos=consultar("SELECT * FROM cursos c WHERE c.activo = 1" + filtro
                         + " ORDER BY c.titulo", parametros),
        estados=ESTADOS, q=busqueda, hoy=hoy,
        total=sum(len(g["personas"]) for g in grupos.values()))


@bp.post("/assignments/create")
@instructor_requerido
def crear_asignacion():
    curso_ids = request.form.getlist("curso_id")
    usuario_ids = request.form.getlist("usuario_id")
    fecha_limite = (request.form.get("fecha_limite") or "").strip() or None
    if not curso_ids or not usuario_ids:
        flash("Select at least one course and one person.", "error")
        return _volver("admin.asignaciones")
    for curso_id in curso_ids:
        _curso_editable(curso_id)       # nadie asigna un curso ajeno

    nuevas, repetidas = 0, 0
    conexion = db()
    for curso_id in curso_ids:
        for usuario_id in usuario_ids:
            existe = conexion.execute(
                "SELECT id FROM asignaciones WHERE usuario_id = ? AND curso_id = ?",
                (usuario_id, curso_id)).fetchone()
            if existe:
                repetidas += 1
                continue
            conexion.execute(
                "INSERT INTO asignaciones (usuario_id, curso_id, asignado_en, fecha_limite)"
                " VALUES (?, ?, ?, ?)", (usuario_id, curso_id, ahora(), fecha_limite))
            nuevas += 1
    conexion.commit()

    if request.form.get("notificar") and nuevas:
        _notificar_asignacion(curso_ids, usuario_ids, fecha_limite)
    registrar("asignacion.crear", f"{len(curso_ids)} course(s)", None,
              f"{nuevas} new assignment(s) for {len(usuario_ids)} person(s)")
    mensaje = f"{nuevas} assignment(s) created."
    if repetidas:
        mensaje += f" {repetidas} already existed and were left untouched."
    flash(mensaje, "ok")
    return _volver("admin.asignaciones")


def _notificar_asignacion(curso_ids, usuario_ids, fecha_limite):
    ajustes = obtener_config()
    if not mailer.activo(ajustes, "email_asignacion"):
        return
    titulos = [consultar("SELECT titulo FROM cursos WHERE id = ?", (c,), uno=True)["titulo"]
               for c in curso_ids]
    for usuario_id in usuario_ids:
        usuario = consultar("SELECT * FROM usuarios WHERE id = ?", (usuario_id,), uno=True)
        if not usuario:
            continue
        mailer.enviar(ajustes, usuario["email"], "New training assigned",
                      mailer.cuerpo_asignacion(usuario, titulos, fecha_limite, ajustes))


@bp.post("/assignments/<int:asignacion_id>/delete")
@instructor_requerido
def eliminar_asignacion(asignacion_id):
    """Desmatricula: la persona deja de tener este curso asignado."""
    fila = _asignacion_editable(asignacion_id)
    ejecutar("DELETE FROM asignaciones WHERE id = ?", (asignacion_id,))
    registrar("asignacion.eliminar", fila["usuario"], asignacion_id, fila["curso"])
    flash(f"{fila['usuario']} was unenrolled from \u201c{fila['curso']}\u201d. "
          "Any certificate already issued is kept.", "ok")
    return redirect(request.referrer or url_for("admin.asignaciones"))


def _asignacion_editable(asignacion_id):
    fila = consultar(
        "SELECT a.id, a.usuario_id, a.curso_id, c.creado_por, c.titulo AS curso,"
        " u.nombre AS usuario, u.email"
        " FROM asignaciones a JOIN cursos c ON c.id = a.curso_id"
        " JOIN usuarios u ON u.id = a.usuario_id WHERE a.id = ?", (asignacion_id,), uno=True)
    if fila is None:
        abort(404)
    if not puede_editar_curso(fila):
        abort(403)
    return fila


@bp.post("/assignments/<int:asignacion_id>/reopen")
@instructor_requerido
def reabrir_asignacion(asignacion_id):
    """Reset: la persona vuelve a empezar el curso desde cero."""
    fila = _asignacion_editable(asignacion_id)
    ejecutar("UPDATE asignaciones SET estado = 'pendiente', avance = 0, max_posicion = 0,"
             " material_completo = 0, completado_en = NULL WHERE id = ?", (asignacion_id,))
    ejecutar("DELETE FROM intentos WHERE asignacion_id = ?", (asignacion_id,))
    # El avance vive ahora por contenido: hay que limpiar los tres niveles.
    ejecutar("DELETE FROM avance_contenidos WHERE asignacion_id = ?", (asignacion_id,))
    ejecutar("UPDATE avance_modulos SET max_posicion = 0, avance = 0, material_completo = 0,"
             " estado = 'pendiente', completado_en = NULL WHERE asignacion_id = ?",
             (asignacion_id,))
    ejecutar("DELETE FROM encuestas WHERE asignacion_id = ?", (asignacion_id,))
    registrar("asignacion.reiniciar", fila["usuario"], asignacion_id, fila["curso"])
    flash(f"\u201c{fila['curso']}\u201d was reset for {fila['usuario']}: they start again from "
          "the first module. The certificate already issued, if any, is kept until they finish "
          "again.", "ok")
    return redirect(request.referrer or url_for("admin.asignaciones"))


# --------------------------------------------------------------------------- quices
def _quiz_editable(quiz_id):
    """Carga el examen y corta el paso si el usuario no puede editarlo."""
    quiz = consultar("SELECT * FROM quices WHERE id = ?", (quiz_id,), uno=True)
    if quiz is None:
        abort(404)
    if not puede_editar_curso(quiz):      # los exámenes también llevan creado_por
        abort(403)
    return quiz


def _examenes_disponibles():
    """Exámenes que el usuario puede usar o editar, con su uso actual."""
    yo = usuario_actual()
    filtro, parametros = ("", ()) if es_admin(yo) else (" AND q.creado_por = ?", (yo["id"],))
    return consultar(
        "SELECT q.*, (SELECT COUNT(*) FROM preguntas p WHERE p.quiz_id = q.id) AS preguntas,"
        " (SELECT COUNT(*) FROM modulos m WHERE m.quiz_id = q.id) AS usos,"
        " (SELECT u.nombre FROM usuarios u WHERE u.id = q.creado_por) AS autor"
        " FROM quices q WHERE 1 = 1" + filtro + " ORDER BY q.titulo", parametros)


def _donde_se_usa(quiz_id):
    return consultar(
        "SELECT m.id, m.titulo AS modulo, m.orden, c.id AS curso_id, c.titulo AS curso"
        " FROM modulos m JOIN cursos c ON c.id = m.curso_id"
        " WHERE m.quiz_id = ? ORDER BY c.titulo, m.orden", (quiz_id,))


def _crear_quiz(titulo, por_defecto="Module exam"):
    """Crea un examen vacío a nombre de quien lo pide y devuelve su id."""
    return ejecutar(
        "INSERT INTO quices (titulo, creado_por, puntaje_minimo, intentos_max,"
        " preguntas_por_intento, mezclar, creado_en) VALUES (?, ?, 70, 3, 0, 1, ?)",
        ((titulo or "").strip() or por_defecto, usuario_actual()["id"], ahora()))


def _aplicar_examen(modulo_id, titulo_modulo):
    """Lee del formulario qué examen lleva el módulo y lo engancha.

    El campo `examen` vale '' (sin examen), 'nuevo' (crear uno) o el id de uno
    que ya exista. Devuelve el id del examen, o None si el módulo no lleva.
    """
    eleccion = (request.form.get("examen") or "").strip()
    if not eleccion:
        ejecutar("UPDATE modulos SET quiz_id = NULL WHERE id = ?", (modulo_id,))
        return None
    if eleccion == "nuevo":
        quiz_id = _crear_quiz(f"{titulo_modulo} exam")
    elif eleccion.isdigit():
        quiz_id = _quiz_editable(int(eleccion))["id"]      # 403 si no es suyo
    else:
        return None
    ejecutar("UPDATE modulos SET quiz_id = ? WHERE id = ?", (quiz_id, modulo_id))
    return quiz_id


@bp.route("/exams")
@instructor_requerido
def quices():
    """Biblioteca de exámenes: existen por su cuenta y se reutilizan."""
    filtro, parametros = _mis_cursos_sql()
    sin_examen = consultar(
        "SELECT c.id AS curso_id, c.titulo AS curso, m.id AS modulo_id, m.titulo AS modulo,"
        " m.orden FROM cursos c JOIN modulos m ON m.curso_id = c.id"
        " WHERE m.quiz_id IS NULL" + filtro + " ORDER BY c.titulo, m.orden", parametros)
    return render_template("admin/quices.html", examenes=_examenes_disponibles(),
                           sin_examen=sin_examen)


@bp.post("/exams/create")
@instructor_requerido
def crear_quiz():
    titulo = (request.form.get("titulo") or "").strip()
    if not titulo:
        flash("The exam needs a title.", "error")
        return _volver("admin.quices")
    quiz_id = _crear_quiz(titulo)
    registrar("examen.crear", titulo, quiz_id)
    flash(f"Exam \u201c{titulo}\u201d created. Now load its question bank.", "ok")
    return redirect(url_for("admin.editar_quiz", quiz_id=quiz_id))


@bp.route("/exams/<int:quiz_id>")
@instructor_requerido
def editar_quiz(quiz_id):
    quiz = _quiz_editable(quiz_id)
    preguntas = []
    for pregunta in consultar("SELECT * FROM preguntas WHERE quiz_id = ? ORDER BY orden, id",
                              (quiz_id,)):
        preguntas.append({
            "fila": pregunta,
            "opciones": consultar(
                "SELECT * FROM opciones WHERE pregunta_id = ? ORDER BY orden, id",
                (pregunta["id"],)),
        })
    return render_template("admin/quiz_editor.html", quiz=quiz, preguntas=preguntas,
                           usos=_donde_se_usa(quiz_id))


@bp.post("/exams/<int:quiz_id>/save")
@instructor_requerido
def guardar_quiz(quiz_id):
    _quiz_editable(quiz_id)
    titulo = (request.form.get("titulo") or "").strip() or "Module exam"
    try:
        minimo = max(1, min(100, int(request.form.get("puntaje_minimo") or 70)))
        intentos = max(1, min(10, int(request.form.get("intentos_max") or 3)))
        limite = max(0, int(request.form.get("preguntas_por_intento") or 0))
    except ValueError:
        minimo, intentos, limite = 70, 3, 0
    mezclar = 1 if request.form.get("mezclar") else 0
    ejecutar("UPDATE quices SET titulo = ?, descripcion = ?, puntaje_minimo = ?,"
             " intentos_max = ?, preguntas_por_intento = ?, mezclar = ? WHERE id = ?",
             (titulo, (request.form.get("descripcion") or "").strip(), minimo, intentos,
              limite, mezclar, quiz_id))
    flash("Exam saved.", "ok")
    return redirect(url_for("admin.editar_quiz", quiz_id=quiz_id))


@bp.post("/exams/<int:quiz_id>/delete")
@instructor_requerido
def eliminar_quiz(quiz_id):
    _quiz_editable(quiz_id)
    usos = _donde_se_usa(quiz_id)
    if usos:
        donde = ", ".join(f"{u['curso']} · {u['modulo']}" for u in usos[:3])
        flash(f"This exam is still used by {len(usos)} module(s): {donde}. "
              "Detach it there first.", "error")
        return _volver("admin.quices")
    ejecutar("DELETE FROM quices WHERE id = ?", (quiz_id,))
    flash("Exam deleted.", "ok")
    return _volver("admin.quices")


@bp.post("/modules/<int:modulo_id>/exam")
@instructor_requerido
def examen_del_modulo(modulo_id):
    """Cambia el examen de un módulo: ninguno, uno nuevo o uno que ya existe."""
    modulo = _modulo_editable(modulo_id)
    quiz_id = _aplicar_examen(modulo_id, modulo["titulo"])
    if quiz_id is None:
        flash(f"\u201c{modulo['titulo']}\u201d no longer has an exam. "
              "Finishing its content is enough to complete it.", "ok")
        return redirect(url_for("admin.curso_detalle", curso_id=modulo["curso_id"]))
    return redirect(url_for("admin.editar_quiz", quiz_id=quiz_id))


@bp.post("/exams/<int:quiz_id>/questions")
@instructor_requerido
def agregar_pregunta(quiz_id):
    _quiz_editable(quiz_id)
    enunciado = (request.form.get("enunciado") or "").strip()
    opciones = [(request.form.get(f"opcion{i}") or "").strip() for i in range(1, 5)]
    opciones_validas = [(i, texto) for i, texto in enumerate(opciones, start=1) if texto]
    correcta = request.form.get("correcta")

    if not enunciado or len(opciones_validas) < 2:
        flash("Write the question and at least two answer options.", "error")
        return redirect(url_for("admin.editar_quiz", quiz_id=quiz_id))
    if correcta not in {str(i) for i, _ in opciones_validas}:
        flash("Mark which of the written options is the correct one.", "error")
        return redirect(url_for("admin.editar_quiz", quiz_id=quiz_id))

    orden = consultar("SELECT IFNULL(MAX(orden), 0) + 1 AS siguiente FROM preguntas"
                      " WHERE quiz_id = ?", (quiz_id,), uno=True)["siguiente"]
    pregunta_id = ejecutar("INSERT INTO preguntas (quiz_id, enunciado, orden) VALUES (?, ?, ?)",
                           (quiz_id, enunciado, orden))
    for indice, (numero, texto) in enumerate(opciones_validas, start=1):
        ejecutar("INSERT INTO opciones (pregunta_id, texto, correcta, orden) VALUES (?, ?, ?, ?)",
                 (pregunta_id, texto, int(str(numero) == correcta), indice))
    flash("Question added to the bank.", "ok")
    return redirect(url_for("admin.editar_quiz", quiz_id=quiz_id))


def _pregunta_editable(pregunta_id):
    pregunta = consultar(
        "SELECT p.*, q.creado_por FROM preguntas p JOIN quices q ON q.id = p.quiz_id"
        " WHERE p.id = ?", (pregunta_id,), uno=True)
    if pregunta is None:
        abort(404)
    if not puede_editar_curso(pregunta):
        abort(403)
    return pregunta


def _renumerar_preguntas(quiz_id):
    for posicion, fila in enumerate(
            consultar("SELECT id FROM preguntas WHERE quiz_id = ? ORDER BY orden, id",
                      (quiz_id,)), start=1):
        ejecutar("UPDATE preguntas SET orden = ? WHERE id = ?", (posicion, fila["id"]))


@bp.post("/questions/<int:pregunta_id>/delete")
@instructor_requerido
def eliminar_pregunta(pregunta_id):
    pregunta = _pregunta_editable(pregunta_id)
    ejecutar("DELETE FROM preguntas WHERE id = ?", (pregunta_id,))
    _renumerar_preguntas(pregunta["quiz_id"])
    flash("Question deleted.", "ok")
    return redirect(url_for("admin.editar_quiz", quiz_id=pregunta["quiz_id"]))


@bp.post("/questions/<int:pregunta_id>/move")
@instructor_requerido
def mover_pregunta(pregunta_id):
    """Sube o baja una pregunta dentro del banco del examen."""
    pregunta = _pregunta_editable(pregunta_id)
    arriba = request.form.get("direccion") == "up"
    vecino = consultar(
        "SELECT id, orden FROM preguntas WHERE quiz_id = ? AND orden "
        + ("< ? ORDER BY orden DESC" if arriba else "> ? ORDER BY orden ASC") + " LIMIT 1",
        (pregunta["quiz_id"], pregunta["orden"]), uno=True)
    if vecino:
        ejecutar("UPDATE preguntas SET orden = ? WHERE id = ?", (vecino["orden"], pregunta_id))
        ejecutar("UPDATE preguntas SET orden = ? WHERE id = ?",
                 (pregunta["orden"], vecino["id"]))
        _renumerar_preguntas(pregunta["quiz_id"])
    return redirect(url_for("admin.editar_quiz", quiz_id=pregunta["quiz_id"]))


@bp.post("/questions/<int:pregunta_id>/edit")
@instructor_requerido
def editar_pregunta(pregunta_id):
    """Reescribe el enunciado de una pregunta del banco."""
    pregunta = _pregunta_editable(pregunta_id)
    enunciado = (request.form.get("enunciado") or "").strip()
    if enunciado:
        ejecutar("UPDATE preguntas SET enunciado = ? WHERE id = ?", (enunciado, pregunta_id))
        flash("Question updated.", "ok")
    else:
        flash("The question cannot be empty.", "error")
    return redirect(url_for("admin.editar_quiz", quiz_id=pregunta["quiz_id"]))


# --------------------------------------------------------------------------- ficha de usuario
@bp.route("/users/<int:usuario_id>")
@instructor_requerido
def ficha_usuario(usuario_id):
    """Todo lo de una persona: estadísticas, cursos y certificados."""
    persona = consultar("SELECT * FROM usuarios WHERE id = ?", (usuario_id,), uno=True)
    if persona is None:
        abort(404)
    hoy = hoy_local()

    # El instructor solo ve lo que pasa en sus cursos; el admin lo ve todo.
    filtro, parametros = _mis_cursos_sql()
    asignaciones = consultar(
        "SELECT a.*, c.titulo AS curso, c.id AS curso_id, c.categoria, c.horas,"
        " c.one_time, c.vigencia_meses, ce.codigo AS certificado, ce.emitido_en,"
        " (SELECT MAX(i.puntaje) FROM intentos i WHERE i.asignacion_id = a.id) AS puntaje,"
        " (SELECT COUNT(*) FROM modulos m WHERE m.curso_id = c.id) AS modulos"
        " FROM asignaciones a JOIN cursos c ON c.id = a.curso_id"
        " LEFT JOIN certificados ce ON ce.usuario_id = a.usuario_id AND ce.curso_id = a.curso_id"
        " WHERE a.usuario_id = ?" + filtro + " ORDER BY a.asignado_en DESC",
        (usuario_id,) + parametros)

    filas = []
    for fila in asignaciones:
        filas.append({
            "a": fila,
            "vencido": bool(fila["fecha_limite"] and fila["fecha_limite"] < hoy
                            and fila["estado"] != "aprobado"),
        })

    certificados = consultar(
        "SELECT ce.*, c.titulo AS curso, c.one_time, c.vigencia_meses FROM certificados ce"
        " JOIN cursos c ON c.id = ce.curso_id WHERE ce.usuario_id = ?" + filtro
        + " ORDER BY ce.emitido_en DESC", (usuario_id,) + parametros)
    certificados = [_con_vencimiento(c) for c in certificados]

    total = len(filas)
    aprobados = sum(1 for f in filas if f["a"]["estado"] == "aprobado")
    notas = [f["a"]["puntaje"] for f in filas if f["a"]["puntaje"] is not None]
    estadisticas = {
        "total": total,
        "aprobados": aprobados,
        "pendientes": total - aprobados,
        "vencidos": sum(1 for f in filas if f["vencido"]),
        "cumplimiento": round(100 * aprobados / total) if total else 0,
        "promedio": round(sum(notas) / len(notas)) if notas else None,
        "horas": round(sum(f["a"]["horas"] or 0 for f in filas
                           if f["a"]["estado"] == "aprobado"), 1),
        "certificados": len(certificados),
    }
    return render_template("admin/usuario_ficha.html", persona=persona, filas=filas,
                           certificados=certificados, est=estadisticas, estados=ESTADOS,
                           hoy=hoy)


@bp.route("/certificate/<codigo>/image.png")
@instructor_requerido
def imagen_certificado(codigo):
    """El certificado como imagen, para la previsualización.

    Un PDF embebido depende de que el navegador traiga visor y de que no esté
    configurado para descargar; una imagen se ve siempre. Se guarda al lado del
    PDF para no volver a generarla en cada visita.
    """
    certificado = consultar(
        "SELECT ce.*, c.creado_por, c.id AS curso_id FROM certificados ce"
        " JOIN cursos c ON c.id = ce.curso_id WHERE ce.codigo = ?", (codigo,), uno=True)
    if certificado is None:
        abort(404)
    if not es_admin() and not puede_editar_curso(certificado):
        abort(403)
    pdf = config.CERT_DIR / certificado["archivo"]
    if not pdf.exists():
        abort(404)

    imagen = pdf.with_suffix(".vista.webp")
    if not imagen.exists() or imagen.stat().st_mtime < pdf.stat().st_mtime:
        try:
            import pypdfium2 as pdfium
            documento = pdfium.PdfDocument(str(pdf))
            documento[0].render(scale=2.0).to_pil().convert("RGB").save(
                imagen, format="WEBP", quality=88, method=4)
            documento.close()
        except Exception as error:
            print(f"[certificado] no se pudo dibujar {codigo}: {error}", flush=True)
            abort(404)
    return send_file(imagen, mimetype="image/webp", max_age=3600)


@bp.route("/certificate/<codigo>/preview")
@instructor_requerido
def previsualizar_certificado(codigo):
    """Pantalla con el certificado embebido, con descargar y cancelar."""
    certificado = consultar(
        "SELECT ce.*, u.nombre AS usuario, u.email, c.titulo AS curso, c.id AS curso_id,"
        " c.creado_por FROM certificados ce JOIN usuarios u ON u.id = ce.usuario_id"
        " JOIN cursos c ON c.id = ce.curso_id WHERE ce.codigo = ?", (codigo,), uno=True)
    if certificado is None:
        abort(404)
    if not es_admin() and not puede_editar_curso(certificado):
        abort(403)
    return render_template("admin/certificado_preview.html", c=certificado,
                           volver=request.args.get("volver") or
                           url_for("admin.ficha_usuario", usuario_id=certificado["usuario_id"]))


# --------------------------------------------------------------------------- bitácora
@bp.route("/activity")
@admin_requerido
def actividad():
    """Timeline de lo que se ha hecho en la plataforma."""
    busqueda = (request.args.get("q") or "").strip()
    quien = (request.args.get("usuario") or "").strip()
    sql = "SELECT * FROM bitacora WHERE 1 = 1"
    parametros = []
    if busqueda:
        sql += " AND (accion LIKE ? OR IFNULL(objeto,'') LIKE ? OR IFNULL(detalle,'') LIKE ?)"
        parametros += [f"%{busqueda}%"] * 3
    if quien.isdigit():
        sql += " AND usuario_id = ?"
        parametros.append(int(quien))
    # Filtro por tipo: el timeline completo mezcla cosas muy distintas.
    familias = {
        "learners": ("curso.iniciar", "curso.terminar", "examen.presentar",
                     "material.terminar", "encuesta.enviar"),
        "disen": ("curso.", "modulo.", "contenido.", "examen.crear", "examen.editar",
                  "examen.eliminar", "pregunta.", "admin.guardar_quiz",
                  "admin.agregar_pregunta", "admin.mover_pregunta",
                  "admin.editar_pregunta", "admin.eliminar_pregunta",
                  "admin.mover_modulo", "admin.mover_contenido",
                  "admin.renombrar_contenido", "admin.examen_del_modulo"),
        "gestion": ("usuario.", "asignacion.", "certificado.", "config.", "plantilla.",
                    "admin.cambiar_estado_usuario", "admin.eliminar_usuario",
                    "admin.importar", "admin.subir_plantilla", "admin.reenviar_certificado"),
        "sesion": ("sesion.", "clave."),
    }
    tipo = (request.args.get("tipo") or "").strip()
    if tipo in familias:
        prefijos = familias[tipo]
        sql += " AND (" + " OR ".join("accion LIKE ?" for _ in prefijos) + ")"
        parametros += [f"{pre}%" for pre in prefijos]
    sql += " ORDER BY creado_en DESC, id DESC LIMIT 400"

    filas = consultar(sql, tuple(parametros))
    # Agrupadas por día, que es como se lee un timeline.
    dias, orden = {}, []
    for fila in filas:
        # Se agrupa por el día de la organización, no por el día UTC: a las 7 de
        # la noche en Colombia el UTC ya pasó a la fecha siguiente y las
        # entradas de la tarde aparecían bajo el encabezado de mañana.
        local = en_zona(fila["creado_en"])
        clave = local.strftime("%Y-%m-%d") if local else (fila["creado_en"] or "")[:10]
        if clave not in dias:
            dias[clave] = []
            orden.append(clave)
        dias[clave].append(fila)
    return render_template("admin/actividad.html", dias=[(d, dias[d]) for d in orden],
                           total=len(filas), q=busqueda, usuario=quien, tipo=tipo,
                           zona=str(zona_horaria()),
                           personas=consultar("SELECT DISTINCT b.usuario_id AS id,"
                                              " b.usuario_nombre AS nombre FROM bitacora b"
                                              " WHERE b.usuario_id IS NOT NULL"
                                              " ORDER BY b.usuario_nombre"),
                           hoy=hoy_local())


# --------------------------------------------------------------------------- reportes
@bp.route("/summary")
@instructor_requerido
def resumen():
    curso_id = request.args.get("curso_id") or ""
    estado = request.args.get("estado") or ""
    busqueda = (request.args.get("q") or "").strip()

    sql = ("SELECT a.*, u.nombre AS usuario, u.email, u.cargo, c.titulo AS curso,"
           " c.categoria, ce.codigo AS certificado,"
           " (SELECT MAX(i.puntaje) FROM intentos i WHERE i.asignacion_id = a.id) AS puntaje"
           " FROM asignaciones a JOIN usuarios u ON u.id = a.usuario_id"
           " JOIN cursos c ON c.id = a.curso_id"
           " LEFT JOIN certificados ce ON ce.usuario_id = a.usuario_id AND ce.curso_id = a.curso_id"
           " WHERE 1 = 1")
    filtro, propios = _mis_cursos_sql()
    sql += filtro
    parametros = list(propios)
    if curso_id.isdigit():
        sql += " AND c.id = ?"
        parametros.append(int(curso_id))
    if estado in ESTADOS:
        sql += " AND a.estado = ?"
        parametros.append(estado)
    elif estado == "pendientes":
        sql += " AND a.estado <> 'aprobado'"
    if busqueda:
        sql += " AND (u.nombre LIKE ? OR u.email LIKE ? OR c.titulo LIKE ?)"
        parametros += [f"%{busqueda}%"] * 3
    sql += (" ORDER BY CASE a.estado WHEN 'aprobado' THEN 1 ELSE 0 END,"
            " a.fecha_limite IS NULL, a.fecha_limite, u.nombre")

    filas = consultar(sql, tuple(parametros))
    aprobados = sum(1 for f in filas if f["estado"] == "aprobado")
    return render_template("admin/resumen.html", filas=filas, aprobados=aprobados,
                           pendientes=len(filas) - aprobados, estados=ESTADOS,
                           cursos=consultar("SELECT c.id, c.titulo FROM cursos c"
                                            " WHERE 1 = 1" + filtro + " ORDER BY c.titulo",
                                            propios),
                           curso_id=curso_id, estado=estado, q=busqueda, hoy=hoy_local())


def _con_vencimiento(fila):
    """Añade la fecha de vencimiento del certificado.

    Un curso marcado one time no vence nunca, así que no entra en el control.
    """
    datos = dict(fila)
    if fila["one_time"] or not fila["vigencia_meses"]:
        datos["vence_en"] = None
        datos["vencido"] = False
        datos["una_vez"] = bool(fila["one_time"])
    else:
        datos["vence_en"] = sumar_meses(fila["emitido_en"], fila["vigencia_meses"])
        datos["vencido"] = datos["vence_en"][:10] < hoy_local()
        datos["una_vez"] = False
    return datos


@bp.route("/certificates")
@admin_requerido
def certificados():
    usuario = (request.args.get("usuario") or "").strip()
    curso = (request.args.get("curso") or "").strip()
    sql = ("SELECT ce.*, u.nombre AS usuario, u.email, c.titulo AS curso,"
           " c.vigencia_meses, c.one_time"
           " FROM certificados ce JOIN usuarios u ON u.id = ce.usuario_id"
           " JOIN cursos c ON c.id = ce.curso_id WHERE 1 = 1")
    parametros = []
    if usuario:
        sql += " AND (u.nombre LIKE ? OR u.email LIKE ?)"
        parametros += [f"%{usuario}%"] * 2
    if curso:
        sql += " AND c.titulo LIKE ?"
        parametros.append(f"%{curso}%")
    sql += " ORDER BY ce.emitido_en DESC"
    filas = [_con_vencimiento(fila) for fila in consultar(sql, tuple(parametros))]
    if request.args.get("estado") == "vencidos":
        filas = [f for f in filas if f["vencido"]]
    return render_template("admin/certificados.html", certificados=filas,
                           usuario=usuario, curso=curso,
                           estado=request.args.get("estado") or "",
                           vencidos=sum(1 for f in filas if f["vencido"]))


@bp.post("/certificates/<codigo>/resend")
@admin_requerido
def reenviar_certificado(codigo):
    certificado = consultar(
        "SELECT ce.*, u.nombre, u.email, c.titulo FROM certificados ce"
        " JOIN usuarios u ON u.id = ce.usuario_id JOIN cursos c ON c.id = ce.curso_id"
        " WHERE ce.codigo = ?", (codigo,), uno=True)
    if certificado is None:
        abort(404)
    ajustes = obtener_config()
    usuario = consultar("SELECT * FROM usuarios WHERE id = ?", (certificado["usuario_id"],), uno=True)
    curso = consultar("SELECT * FROM cursos WHERE id = ?", (certificado["curso_id"],), uno=True)
    ruta = config.CERT_DIR / certificado["archivo"]
    if not ruta.exists():
        ruta = cert.generar(usuario, curso, certificado["puntaje"], codigo, ajustes,
                            completado_en=certificado["emitido_en"])
    asunto = f"Certificate of Training · {curso['titulo']}"
    destinos = [usuario["email"]]
    if ajustes.get("admin_email"):
        destinos.append(ajustes["admin_email"])
    mailer.enviar(ajustes, destinos, asunto,
                  mailer.cuerpo_certificado(usuario, curso, certificado["puntaje"], codigo, ajustes),
                  ruta)
    flash(f"Certificate {codigo} resent to {', '.join(destinos)}.", "ok")
    return redirect(request.referrer or url_for("admin.certificados"))


# --------------------------------------------------------------------------- configuración
# --------------------------------------------------------------------------- importación
@bp.route("/import")
@admin_requerido
def importar():
    recientes = consultar(
        "SELECT ce.*, u.nombre AS usuario, c.titulo AS curso FROM certificados ce"
        " JOIN usuarios u ON u.id = ce.usuario_id JOIN cursos c ON c.id = ce.curso_id"
        " WHERE ce.origen = 'importado' ORDER BY ce.id DESC LIMIT 25")
    return render_template(
        "admin/importar.html", recientes=recientes,
        usuarios=consultar("SELECT * FROM usuarios WHERE activo = 1 ORDER BY nombre"),
        cursos=consultar("SELECT * FROM cursos ORDER BY titulo"))


@bp.post("/import")
@admin_requerido
def importar_registro():
    usuario_id = request.form.get("usuario_id")
    curso_id = request.form.get("curso_id")
    fecha = (request.form.get("fecha") or "").strip()
    if not usuario_id or not curso_id or not fecha:
        flash("Person, course and completion date are required.", "error")
        return _volver("admin.importar")

    usuario = consultar("SELECT * FROM usuarios WHERE id = ?", (usuario_id,), uno=True)
    curso = consultar("SELECT * FROM cursos WHERE id = ?", (curso_id,), uno=True)
    if usuario is None or curso is None:
        abort(404)
    try:
        puntaje = float(request.form.get("puntaje") or 100)
    except ValueError:
        puntaje = 100

    # La asignación se marca aprobada para que cuente en el training summary.
    asignacion = consultar("SELECT * FROM asignaciones WHERE usuario_id = ? AND curso_id = ?",
                           (usuario_id, curso_id), uno=True)
    if asignacion is None:
        asignacion_id = ejecutar(
            "INSERT INTO asignaciones (usuario_id, curso_id, asignado_en, fecha_limite, avance,"
            " estado, completado_en) VALUES (?, ?, ?, NULL, 100, 'aprobado', ?)",
            (usuario_id, curso_id, fecha, fecha))
    else:
        asignacion_id = asignacion["id"]
        ejecutar("UPDATE asignaciones SET avance = 100, estado = 'aprobado', completado_en = ?"
                 " WHERE id = ?", (fecha, asignacion_id))
    for modulo in consultar("SELECT id FROM modulos WHERE curso_id = ?", (curso_id,)):
        ejecutar("INSERT OR IGNORE INTO avance_modulos (asignacion_id, modulo_id) VALUES (?, ?)",
                 (asignacion_id, modulo["id"]))
        ejecutar("UPDATE avance_modulos SET avance = 100, material_completo = 1,"
                 " estado = 'aprobado', completado_en = ? WHERE asignacion_id = ? AND modulo_id = ?",
                 (fecha, asignacion_id, modulo["id"]))

    # El PDF es opcional: si no lo suben, queda el registro sin archivo adjunto.
    archivo = request.files.get("certificado")
    codigo = (request.form.get("codigo") or "").strip() or cert.nuevo_codigo()
    nombre_archivo = ""
    if archivo and archivo.filename:
        if not archivo.filename.lower().endswith(".pdf"):
            flash("The certificate file must be a PDF.", "error")
            return _volver("admin.importar")
        nombre_archivo = f"{codigo}.pdf"
        archivo.save(config.CERT_DIR / nombre_archivo)

    existente = consultar("SELECT * FROM certificados WHERE usuario_id = ? AND curso_id = ?",
                          (usuario_id, curso_id), uno=True)
    if existente:
        ejecutar("UPDATE certificados SET codigo = ?, puntaje = ?, archivo = ?,"
                 " origen = 'importado', emitido_en = ? WHERE id = ?",
                 (codigo, puntaje, nombre_archivo or existente["archivo"], fecha, existente["id"]))
    else:
        ejecutar("INSERT INTO certificados (codigo, usuario_id, curso_id, intento_id, puntaje,"
                 " archivo, origen, emitido_en) VALUES (?, ?, ?, NULL, ?, ?, 'importado', ?)",
                 (codigo, usuario_id, curso_id, puntaje, nombre_archivo, fecha))

    flash(f"Record imported for {usuario['nombre']} · {curso['titulo']}.", "ok")
    return _volver("admin.importar")


@bp.post("/import/bulk")
@admin_requerido
def importar_csv():
    """CSV con columnas: email,course,date,score,code — una fila por registro."""
    archivo = request.files.get("csv")
    if archivo is None or not archivo.filename:
        flash("Select a CSV file.", "error")
        return _volver("admin.importar")

    import csv as csv_lib
    import io
    texto = archivo.read().decode("utf-8-sig", "ignore")
    lector = csv_lib.DictReader(io.StringIO(texto))
    creados, errores = 0, []
    for numero, fila in enumerate(lector, start=2):
        email = (fila.get("email") or "").strip().lower()
        titulo = (fila.get("course") or "").strip()
        fecha = (fila.get("date") or "").strip()
        usuario = consultar("SELECT * FROM usuarios WHERE email = ?", (email,), uno=True)
        curso = consultar("SELECT * FROM cursos WHERE titulo = ?", (titulo,), uno=True)
        if not usuario or not curso or not fecha:
            errores.append(f"row {numero}")
            continue
        try:
            puntaje = float(fila.get("score") or 100)
        except ValueError:
            puntaje = 100
        codigo = (fila.get("code") or "").strip() or cert.nuevo_codigo()

        asignacion = consultar("SELECT * FROM asignaciones WHERE usuario_id = ? AND curso_id = ?",
                               (usuario["id"], curso["id"]), uno=True)
        if asignacion is None:
            asignacion_id = ejecutar(
                "INSERT INTO asignaciones (usuario_id, curso_id, asignado_en, avance, estado,"
                " completado_en) VALUES (?, ?, ?, 100, 'aprobado', ?)",
                (usuario["id"], curso["id"], fecha, fecha))
        else:
            asignacion_id = asignacion["id"]
            ejecutar("UPDATE asignaciones SET avance = 100, estado = 'aprobado',"
                     " completado_en = ? WHERE id = ?", (fecha, asignacion_id))
        for modulo in consultar("SELECT id FROM modulos WHERE curso_id = ?", (curso["id"],)):
            ejecutar("INSERT OR IGNORE INTO avance_modulos (asignacion_id, modulo_id)"
                     " VALUES (?, ?)", (asignacion_id, modulo["id"]))
            ejecutar("UPDATE avance_modulos SET avance = 100, material_completo = 1,"
                     " estado = 'aprobado', completado_en = ? WHERE asignacion_id = ?"
                     " AND modulo_id = ?", (fecha, asignacion_id, modulo["id"]))
        if not consultar("SELECT id FROM certificados WHERE usuario_id = ? AND curso_id = ?",
                         (usuario["id"], curso["id"]), uno=True):
            ejecutar("INSERT INTO certificados (codigo, usuario_id, curso_id, intento_id,"
                     " puntaje, archivo, origen, emitido_en)"
                     " VALUES (?, ?, ?, NULL, ?, '', 'importado', ?)",
                     (codigo, usuario["id"], curso["id"], puntaje, fecha))
        creados += 1

    mensaje = f"{creados} record(s) imported."
    if errores:
        mensaje += f" Skipped: {', '.join(errores[:8])}."
    flash(mensaje, "ok" if creados else "error")
    return _volver("admin.importar")


# --------------------------------------------------------------------------- plantilla
@bp.post("/settings/certificate-template")
@admin_requerido
def subir_plantilla():
    archivo = request.files.get("plantilla")
    if archivo is None or not archivo.filename:
        flash("Select a template file.", "error")
        return _volver("admin.configuracion")
    from pathlib import Path as _Path
    from werkzeug.utils import secure_filename
    nombre = secure_filename(archivo.filename)
    extension = _Path(nombre).suffix.lower()
    if extension not in config.EXT_PLANTILLA:
        flash(f"Template format not supported. Use one of: "
              f"{', '.join(sorted(config.EXT_PLANTILLA))}.", "error")
        return _volver("admin.configuracion")

    destino = f"certificate{extension}"
    archivo.save(config.TEMPLATE_DIR / destino)
    guardar_config({"cert_template": destino, "cert_template_name": nombre,
                    "cert_mode": "template"})
    flash("Certificate template uploaded. New certificates will use it.", "ok")
    return _volver("admin.configuracion")


@bp.post("/settings/certificate-mode")
@admin_requerido
def cambiar_modo_certificado():
    modo = "template" if request.form.get("modo") == "template" else "builtin"
    guardar_config({"cert_mode": modo})
    flash("Built-in certificate design in use." if modo == "builtin"
          else "Uploaded template in use.", "ok")
    return _volver("admin.configuracion")


@bp.post("/settings/certificate-preview")
@admin_requerido
def previsualizar_plantilla():
    """Genera un certificado de muestra con datos de ejemplo."""
    ajustes = obtener_config()
    usuario = {"nombre": "Sample Employee", "email": ajustes.get("admin_email", "")}
    # Muestra con firma, que es la plantilla base más completa.
    curso = {"titulo": "Sample Course", "horas": 2, "vigencia_meses": 12, "one_time": 0,
             "cert_tipo": "signed", "firma_nombre": ajustes.get("org_signer", ""),
             "firma_cargo": ajustes.get("org_signer_title", ""), "firma_archivo": ""}
    codigo = "ALR-0000-SAMPLE"
    try:
        ruta = cert.generar(usuario, curso, 100, codigo, ajustes)   # fechas = hoy
    except Exception as error:
        flash(f"The template could not be rendered: {error}", "error")
        return _volver("admin.configuracion")
    return send_file(ruta, mimetype="application/pdf", as_attachment=True,
                     download_name="certificate-preview.pdf")


@bp.route("/settings")
@admin_requerido
def configuracion():
    return render_template("admin/configuracion.html", cfg=obtener_config(),
                           zonas=ZONAS_HORARIAS,
                           correos=consultar("SELECT * FROM correos ORDER BY id DESC LIMIT 25"),
                           bandeja=str(config.OUTBOX_DIR),
                           marcadores=config.MARCADORES,
                           extensiones_plantilla=sorted(config.EXT_PLANTILLA))


@bp.post("/settings")
@admin_requerido
def guardar_configuracion():
    campos = ["org_name", "org_short", "org_area", "org_signer", "org_signer_title",
              "admin_email", "smtp_host", "smtp_port", "smtp_user", "smtp_sender",
              "site_url", "org_timezone", "mail_mode", "graph_tenant_id",
              "graph_client_id", "graph_sender"]
    # Solo se guarda lo que venga en el formulario: los campos del modo que no
    # está activo llegan deshabilitados, y si los tomáramos como vacíos
    # borraríamos la configuración del otro modo al cambiar de uno a otro.
    datos = {campo: (request.form.get(campo) or "").strip()
             for campo in campos if campo in request.form}
    if "smtp_tls" in request.form or "smtp_host" in request.form:
        datos["smtp_tls"] = "1" if request.form.get("smtp_tls") else "0"
    for interruptor in ("email_bienvenida", "email_asignacion", "email_vencidos"):
        datos[interruptor] = "1" if request.form.get(interruptor) else "0"
    # Las claves solo se tocan si se escribió una nueva: el formulario las
    # muestra en blanco para no exponerlas.
    if request.form.get("smtp_password"):
        datos["smtp_password"] = request.form["smtp_password"]
    if request.form.get("graph_client_secret"):
        datos["graph_client_secret"] = request.form["graph_client_secret"]
    guardar_config(datos)
    registrar("config.guardar", None, None, ", ".join(sorted(datos)))
    flash("Settings saved.", "ok")
    return _volver("admin.configuracion")


@bp.post("/settings/test-email")
@admin_requerido
def probar_correo():
    ajustes = obtener_config()
    destino = (request.form.get("destino") or ajustes.get("admin_email") or "").strip()
    if not destino:
        flash("Enter a destination address for the test.", "error")
        return _volver("admin.configuracion")
    mailer.enviar(ajustes, destino, "Test message · training platform",
                  "If you received this message, certificate delivery is working.",
                  en_segundo_plano=False)
    flash(f"Test sent to {destino}. Check the log below for the result.", "ok")
    return _volver("admin.configuracion")


@bp.route("/certificate/<codigo>.pdf")
@admin_requerido
def ver_certificado(codigo):
    certificado = consultar("SELECT * FROM certificados WHERE codigo = ?", (codigo,), uno=True)
    if certificado is None:
        abort(404)
    ruta = config.CERT_DIR / certificado["archivo"]
    if not ruta.exists():
        abort(404)
    # Por defecto se entrega para verlo dentro de la página. Solo con ?download=1
    # se manda como adjunto: si no, el visor de la previsualización no puede
    # mostrarlo y el navegador lo baja directo.
    descargar = request.args.get("download") == "1"
    respuesta = send_file(ruta, mimetype="application/pdf", as_attachment=descargar,
                          download_name=f"{codigo}.pdf")
    if not descargar:
        respuesta.headers["Content-Disposition"] = f'inline; filename="{codigo}.pdf"'
    return respuesta
