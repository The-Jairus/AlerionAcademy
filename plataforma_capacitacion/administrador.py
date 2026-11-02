"""Vista de administrador: usuarios, cursos, asignaciones, quices y reportes."""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   send_file, url_for)

import config
import mailer
import media
import certificados as cert
from auth import (admin_requerido, es_admin, hash_password, instructor_requerido,
                  puede_editar_curso, usuario_actual)
from database import (ahora, consultar, db, ejecutar, guardar_config, obtener_config,
                      sumar_meses)

bp = Blueprint("admin", __name__, url_prefix="/admin")

ESTADOS = {
    "pendiente": "Not started",
    "en_progreso": "In progress",
    "reprobado": "Not passed",
    "aprobado": "Completed",
}


def _volver(destino):
    return redirect(url_for(destino))


# --------------------------------------------------------------------------- permisos
def _mis_cursos_sql(alias="c"):
    """Filtro SQL: el admin ve todos los cursos; el instructor solo los suyos.

    Devuelve (fragmento, parámetros) para pegar después de un WHERE.
    """
    yo = usuario_actual()
    if es_admin(yo):
        return "", ()
    return f" AND {alias}.creado_por = ?", (yo["id"],)


def _curso_editable(curso_id):
    """Carga el curso y corta el paso si el usuario no puede editarlo."""
    curso = consultar("SELECT * FROM cursos WHERE id = ?", (curso_id,), uno=True)
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
        (ahora()[:10],) + parametros)
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
    flash(f"User {nombre} created.", "ok")
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
        " (SELECT COUNT(*) FROM modulos m JOIN quices q ON q.modulo_id = m.id"
        "   WHERE m.curso_id = c.id) AS con_examen,"
        " (SELECT u.nombre FROM usuarios u WHERE u.id = c.creado_por) AS autor"
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
    flash(f"Course “{titulo}” created. Now add its modules.", "ok")
    return redirect(url_for("admin.curso_detalle", curso_id=curso_id))


@bp.route("/courses/<int:curso_id>")
@instructor_requerido
def curso_detalle(curso_id):
    curso = _curso_editable(curso_id)
    modulos = consultar(
        "SELECT m.*, q.id AS quiz_id, q.titulo AS quiz_titulo, q.puntaje_minimo,"
        " q.intentos_max, q.preguntas_por_intento,"
        " (SELECT COUNT(*) FROM preguntas p WHERE p.quiz_id = q.id) AS preguntas"
        " FROM modulos m LEFT JOIN quices q ON q.modulo_id = m.id"
        " WHERE m.curso_id = ? ORDER BY m.orden, m.id", (curso_id,))
    total = len(modulos)
    return render_template("admin/curso_detalle.html", curso=curso, modulos=modulos,
                           total_modulos=total,
                           extensiones=sorted(config.EXT_PERMITIDAS))


@bp.post("/courses/<int:curso_id>/modules")
@instructor_requerido
def agregar_modulo(curso_id):
    _curso_editable(curso_id)
    titulo = (request.form.get("titulo") or "").strip()
    archivo = request.files.get("material")
    if not titulo or archivo is None or not archivo.filename:
        flash("A module needs a title and a material file.", "error")
        return redirect(url_for("admin.curso_detalle", curso_id=curso_id))
    try:
        info = media.guardar_material(archivo)
    except media.ErrorMaterial as error:
        flash(str(error), "error")
        return redirect(url_for("admin.curso_detalle", curso_id=curso_id))
    except Exception as error:
        flash(f"The material could not be processed: {error}", "error")
        return redirect(url_for("admin.curso_detalle", curso_id=curso_id))

    orden = consultar("SELECT IFNULL(MAX(orden), 0) + 1 AS siguiente FROM modulos"
                      " WHERE curso_id = ?", (curso_id,), uno=True)["siguiente"]
    modulo_id = ejecutar(
        "INSERT INTO modulos (curso_id, titulo, descripcion, orden, tipo_material, carpeta,"
        " archivo, archivo_original, duracion_segundos, paginas, creado_en)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (curso_id, titulo, (request.form.get("descripcion") or "").strip(), orden,
         info["tipo_material"], info["carpeta"], info["archivo"], info["archivo_original"],
         info["duracion_segundos"], info["paginas"], ahora()))
    if info["tipo_material"] == "video":
        # La recompresión sigue en segundo plano: la subida ya respondió.
        media.optimizar_en_segundo_plano(modulo_id, info["carpeta"], info["archivo"])
        detalle = f"{info['duracion_segundos'] / 60:.0f} minutes of video"
    else:
        detalle = f"{info['paginas']} pages"
    flash(f"Module “{titulo}” added with {detalle}. Now set up its exam.", "ok")
    return redirect(url_for("admin.editar_quiz", modulo_id=modulo_id))


@bp.post("/modules/<int:modulo_id>/delete")
@instructor_requerido
def eliminar_modulo(modulo_id):
    modulo = _modulo_editable(modulo_id)
    media.eliminar_material(modulo["carpeta"])
    ejecutar("DELETE FROM modulos WHERE id = ?", (modulo_id,))
    _renumerar_modulos(modulo["curso_id"])
    flash("Module deleted.", "ok")
    return redirect(url_for("admin.curso_detalle", curso_id=modulo["curso_id"]))


@bp.post("/modules/<int:modulo_id>/edit")
@instructor_requerido
def editar_modulo(modulo_id):
    """Cambia el título, la descripción y, si se envía uno, el material."""
    modulo = _modulo_editable(modulo_id)
    titulo = (request.form.get("titulo") or "").strip()
    if not titulo:
        flash("The module needs a title.", "error")
        return redirect(url_for("admin.curso_detalle", curso_id=modulo["curso_id"]))

    ejecutar("UPDATE modulos SET titulo = ?, descripcion = ? WHERE id = ?",
             (titulo, (request.form.get("descripcion") or "").strip(), modulo_id))

    archivo = request.files.get("material")
    if archivo is not None and archivo.filename:
        try:
            info = media.guardar_material(archivo)
        except media.ErrorMaterial as error:
            flash(str(error), "error")
            return redirect(url_for("admin.curso_detalle", curso_id=modulo["curso_id"]))
        except Exception as error:
            flash(f"The material could not be processed: {error}", "error")
            return redirect(url_for("admin.curso_detalle", curso_id=modulo["curso_id"]))
        media.eliminar_material(modulo["carpeta"])
        ejecutar("UPDATE modulos SET tipo_material = ?, carpeta = ?, archivo = ?,"
                 " archivo_original = ?, duracion_segundos = ?, paginas = ? WHERE id = ?",
                 (info["tipo_material"], info["carpeta"], info["archivo"],
                  info["archivo_original"], info["duracion_segundos"], info["paginas"],
                  modulo_id))
        if info["tipo_material"] == "video":
            media.optimizar_en_segundo_plano(modulo_id, info["carpeta"], info["archivo"])
        # El material cambió: quien ya lo había visto debe volver a recorrerlo.
        ejecutar("UPDATE avance_modulos SET max_posicion = 0, completo = 0"
                 " WHERE modulo_id = ?", (modulo_id,))
        flash(f"Module “{titulo}” updated with new material. "
              "Learners will have to review it again.", "ok")
    else:
        flash(f"Module “{titulo}” updated.", "ok")
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


# --------------------------------------------------------------------------- asignaciones
@bp.route("/assignments")
@instructor_requerido
def asignaciones():
    # El instructor solo asigna y ve los cursos que diseñó.
    filtro, parametros = _mis_cursos_sql()
    lista = consultar(
        "SELECT a.*, u.nombre AS usuario, u.email, c.titulo AS curso FROM asignaciones a"
        " JOIN usuarios u ON u.id = a.usuario_id JOIN cursos c ON c.id = a.curso_id"
        " WHERE 1 = 1" + filtro + " ORDER BY a.asignado_en DESC", parametros)
    return render_template(
        "admin/asignaciones.html", asignaciones=lista,
        usuarios=consultar("SELECT * FROM usuarios WHERE activo = 1 ORDER BY nombre"),
        cursos=consultar("SELECT * FROM cursos c WHERE c.activo = 1" + filtro
                         + " ORDER BY c.titulo", parametros),
        estados=ESTADOS)


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
    mensaje = f"{nuevas} assignment(s) created."
    if repetidas:
        mensaje += f" {repetidas} already existed and were left untouched."
    flash(mensaje, "ok")
    return _volver("admin.asignaciones")


def _notificar_asignacion(curso_ids, usuario_ids, fecha_limite):
    ajustes = obtener_config()
    titulos = [consultar("SELECT titulo FROM cursos WHERE id = ?", (c,), uno=True)["titulo"]
               for c in curso_ids]
    for usuario_id in usuario_ids:
        usuario = consultar("SELECT * FROM usuarios WHERE id = ?", (usuario_id,), uno=True)
        if not usuario:
            continue
        cuerpo = (f"Hi {usuario['nombre'].split()[0]},\n\n"
                  "The following training has been assigned to you in the training platform:\n"
                  + "\n".join(f"  · {t}" for t in titulos)
                  + (f"\n\nDue date: {fecha_limite}" if fecha_limite else "")
                  + f"\n\n{ajustes.get('org_name', '')} · {ajustes.get('org_area', '')}")
        mailer.enviar(ajustes, usuario["email"], "New training assigned", cuerpo)


@bp.post("/assignments/<int:asignacion_id>/delete")
@instructor_requerido
def eliminar_asignacion(asignacion_id):
    _asignacion_editable(asignacion_id)
    ejecutar("DELETE FROM asignaciones WHERE id = ?", (asignacion_id,))
    flash("Assignment removed.", "ok")
    return redirect(request.referrer or url_for("admin.asignaciones"))


def _asignacion_editable(asignacion_id):
    fila = consultar(
        "SELECT a.id, c.creado_por FROM asignaciones a JOIN cursos c ON c.id = a.curso_id"
        " WHERE a.id = ?", (asignacion_id,), uno=True)
    if fila is None:
        abort(404)
    if not puede_editar_curso(fila):
        abort(403)
    return fila


@bp.post("/assignments/<int:asignacion_id>/reopen")
@instructor_requerido
def reabrir_asignacion(asignacion_id):
    _asignacion_editable(asignacion_id)
    ejecutar("UPDATE asignaciones SET estado = 'pendiente', avance = 0, max_posicion = 0,"
             " material_completo = 0, completado_en = NULL WHERE id = ?", (asignacion_id,))
    ejecutar("DELETE FROM intentos WHERE asignacion_id = ?", (asignacion_id,))
    flash("Training reopened: the person must review the content and retake the exam.", "ok")
    return redirect(request.referrer or url_for("admin.asignaciones"))


# --------------------------------------------------------------------------- quices
@bp.route("/exams")
@instructor_requerido
def quices():
    filtro, parametros = _mis_cursos_sql()
    lista = consultar(
        "SELECT c.id AS curso_id, c.titulo AS curso, c.activo, m.id AS modulo_id,"
        " m.titulo AS modulo, m.orden, q.id AS quiz_id, q.titulo AS quiz_titulo,"
        " q.puntaje_minimo, q.intentos_max, q.preguntas_por_intento,"
        " (SELECT COUNT(*) FROM preguntas p WHERE p.quiz_id = q.id) AS preguntas"
        " FROM cursos c JOIN modulos m ON m.curso_id = c.id"
        " LEFT JOIN quices q ON q.modulo_id = m.id"
        " WHERE 1 = 1" + filtro + " ORDER BY c.titulo, m.orden", parametros)
    return render_template("admin/quices.html", filas=lista)


@bp.route("/exams/<int:modulo_id>")
@instructor_requerido
def editar_quiz(modulo_id):
    modulo = _modulo_editable(modulo_id)
    quiz = consultar("SELECT * FROM quices WHERE modulo_id = ?", (modulo_id,), uno=True)
    preguntas = []
    if quiz:
        for pregunta in consultar("SELECT * FROM preguntas WHERE quiz_id = ? ORDER BY orden, id",
                                  (quiz["id"],)):
            preguntas.append({
                "fila": pregunta,
                "opciones": consultar(
                    "SELECT * FROM opciones WHERE pregunta_id = ? ORDER BY orden, id",
                    (pregunta["id"],)),
            })
    return render_template("admin/quiz_editor.html", modulo=modulo, quiz=quiz, preguntas=preguntas)


@bp.post("/exams/<int:modulo_id>/save")
@instructor_requerido
def guardar_quiz(modulo_id):
    _modulo_editable(modulo_id)
    titulo = (request.form.get("titulo") or "").strip() or "Module exam"
    try:
        minimo = max(1, min(100, int(request.form.get("puntaje_minimo") or 70)))
        intentos = max(1, min(10, int(request.form.get("intentos_max") or 3)))
        limite = max(0, int(request.form.get("preguntas_por_intento") or 0))
    except ValueError:
        minimo, intentos, limite = 70, 3, 0
    mezclar = 1 if request.form.get("mezclar") else 0

    quiz = consultar("SELECT * FROM quices WHERE modulo_id = ?", (modulo_id,), uno=True)
    if quiz:
        ejecutar("UPDATE quices SET titulo = ?, puntaje_minimo = ?, intentos_max = ?,"
                 " preguntas_por_intento = ?, mezclar = ? WHERE id = ?",
                 (titulo, minimo, intentos, limite, mezclar, quiz["id"]))
    else:
        ejecutar("INSERT INTO quices (modulo_id, titulo, puntaje_minimo, intentos_max,"
                 " preguntas_por_intento, mezclar, creado_en) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (modulo_id, titulo, minimo, intentos, limite, mezclar, ahora()))
    flash("Exam saved.", "ok")
    return redirect(url_for("admin.editar_quiz", modulo_id=modulo_id))


@bp.post("/exams/<int:modulo_id>/questions")
@instructor_requerido
def agregar_pregunta(modulo_id):
    _modulo_editable(modulo_id)
    quiz = consultar("SELECT * FROM quices WHERE modulo_id = ?", (modulo_id,), uno=True)
    if quiz is None:
        flash("Save the exam settings first.", "error")
        return redirect(url_for("admin.editar_quiz", modulo_id=modulo_id))

    enunciado = (request.form.get("enunciado") or "").strip()
    opciones = [(request.form.get(f"opcion{i}") or "").strip() for i in range(1, 5)]
    opciones_validas = [(i, texto) for i, texto in enumerate(opciones, start=1) if texto]
    correcta = request.form.get("correcta")

    if not enunciado or len(opciones_validas) < 2:
        flash("Write the question and at least two answer options.", "error")
        return redirect(url_for("admin.editar_quiz", modulo_id=modulo_id))
    if correcta not in {str(i) for i, _ in opciones_validas}:
        flash("Mark which of the written options is the correct one.", "error")
        return redirect(url_for("admin.editar_quiz", modulo_id=modulo_id))

    orden = consultar("SELECT IFNULL(MAX(orden), 0) + 1 AS siguiente FROM preguntas"
                      " WHERE quiz_id = ?", (quiz["id"],), uno=True)["siguiente"]
    pregunta_id = ejecutar("INSERT INTO preguntas (quiz_id, enunciado, orden) VALUES (?, ?, ?)",
                           (quiz["id"], enunciado, orden))
    for indice, (numero, texto) in enumerate(opciones_validas, start=1):
        ejecutar("INSERT INTO opciones (pregunta_id, texto, correcta, orden) VALUES (?, ?, ?, ?)",
                 (pregunta_id, texto, int(str(numero) == correcta), indice))
    flash("Question added to the bank.", "ok")
    return redirect(url_for("admin.editar_quiz", modulo_id=modulo_id))


def _pregunta_editable(pregunta_id):
    pregunta = consultar(
        "SELECT p.*, q.modulo_id, c.creado_por FROM preguntas p"
        " JOIN quices q ON q.id = p.quiz_id JOIN modulos m ON m.id = q.modulo_id"
        " JOIN cursos c ON c.id = m.curso_id WHERE p.id = ?", (pregunta_id,), uno=True)
    if pregunta is None:
        abort(404)
    if not puede_editar_curso(pregunta):
        abort(403)
    return pregunta


@bp.post("/questions/<int:pregunta_id>/delete")
@instructor_requerido
def eliminar_pregunta(pregunta_id):
    pregunta = _pregunta_editable(pregunta_id)
    ejecutar("DELETE FROM preguntas WHERE id = ?", (pregunta_id,))
    _renumerar_preguntas(pregunta["quiz_id"])
    flash("Question deleted.", "ok")
    return redirect(url_for("admin.editar_quiz", modulo_id=pregunta["modulo_id"]))


def _renumerar_preguntas(quiz_id):
    for posicion, fila in enumerate(
            consultar("SELECT id FROM preguntas WHERE quiz_id = ? ORDER BY orden, id",
                      (quiz_id,)), start=1):
        ejecutar("UPDATE preguntas SET orden = ? WHERE id = ?", (posicion, fila["id"]))


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
    return redirect(url_for("admin.editar_quiz", modulo_id=pregunta["modulo_id"]))


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
    return redirect(url_for("admin.editar_quiz", modulo_id=pregunta["modulo_id"]))


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
                           curso_id=curso_id, estado=estado, q=busqueda, hoy=ahora()[:10])


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
        datos["vencido"] = datos["vence_en"][:10] < ahora()[:10]
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
                           correos=consultar("SELECT * FROM correos ORDER BY id DESC LIMIT 25"),
                           bandeja=str(config.OUTBOX_DIR),
                           marcadores=config.MARCADORES,
                           extensiones_plantilla=sorted(config.EXT_PLANTILLA))


@bp.post("/settings")
@admin_requerido
def guardar_configuracion():
    campos = ["org_name", "org_short", "org_area", "org_signer", "org_signer_title",
              "admin_email", "smtp_host", "smtp_port", "smtp_user", "smtp_sender"]
    datos = {campo: (request.form.get(campo) or "").strip() for campo in campos}
    datos["smtp_tls"] = "1" if request.form.get("smtp_tls") else "0"
    if request.form.get("smtp_password"):
        datos["smtp_password"] = request.form["smtp_password"]
    guardar_config(datos)
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
    return send_file(ruta, mimetype="application/pdf", as_attachment=True,
                     download_name=f"{codigo}.pdf")
