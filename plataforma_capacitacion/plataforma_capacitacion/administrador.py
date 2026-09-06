"""Vista de administrador: usuarios, cursos, asignaciones, quices y reportes."""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   send_file, url_for)

import config
import mailer
import media
from auth import admin_requerido, hash_password, usuario_actual
from database import (ahora, consultar, db, ejecutar, guardar_config, obtener_config)

bp = Blueprint("admin", __name__, url_prefix="/admin")

ESTADOS = {
    "pendiente": "Not started",
    "en_progreso": "In progress",
    "reprobado": "Not passed",
    "aprobado": "Completed",
}


def _volver(destino):
    return redirect(url_for(destino))


# --------------------------------------------------------------------------- panel
@bp.route("/")
@admin_requerido
def panel():
    yo = usuario_actual()
    mis_cursos = consultar(
        "SELECT a.*, c.titulo, c.tipo_material FROM asignaciones a"
        " JOIN cursos c ON c.id = a.curso_id"
        " WHERE a.usuario_id = ? AND a.estado <> 'aprobado' AND c.activo = 1"
        " ORDER BY a.fecha_limite IS NULL, a.fecha_limite", (yo["id"],))
    resumen = consultar(
        "SELECT COUNT(*) AS total,"
        " SUM(estado = 'aprobado') AS aprobados,"
        " SUM(estado <> 'aprobado') AS pendientes FROM asignaciones", uno=True)
    usuarios_activos = consultar(
        "SELECT COUNT(*) AS n FROM usuarios WHERE activo = 1", uno=True)["n"]
    cursos_activos = consultar(
        "SELECT COUNT(*) AS n FROM cursos WHERE activo = 1", uno=True)["n"]
    vencidos = consultar(
        "SELECT a.*, c.titulo, u.nombre AS usuario FROM asignaciones a"
        " JOIN cursos c ON c.id = a.curso_id JOIN usuarios u ON u.id = a.usuario_id"
        " WHERE a.estado <> 'aprobado' AND a.fecha_limite IS NOT NULL"
        " AND a.fecha_limite < ? ORDER BY a.fecha_limite", (ahora()[:10],))
    ultimos = consultar(
        "SELECT ce.*, u.nombre AS usuario, c.titulo FROM certificados ce"
        " JOIN usuarios u ON u.id = ce.usuario_id JOIN cursos c ON c.id = ce.curso_id"
        " ORDER BY ce.emitido_en DESC LIMIT 6")
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
        sql += " WHERE u.nombre LIKE ? OR u.email LIKE ? OR IFNULL(u.documento,'') LIKE ?"
        comodin = f"%{busqueda}%"
        parametros = (comodin, comodin, comodin)
    sql += " ORDER BY u.activo DESC, u.nombre"
    return render_template("admin/usuarios.html", usuarios=consultar(sql, parametros), q=busqueda)


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
    ejecutar("INSERT INTO usuarios (nombre, email, documento, cargo, password_hash, rol,"
             " activo, creado_en) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
             (nombre, email, (request.form.get("documento") or "").strip(),
              (request.form.get("cargo") or "").strip(), hash_password(clave),
              "admin" if request.form.get("rol") == "admin" else "estudiante", ahora()))
    flash(f"User {nombre} created.", "ok")
    return _volver("admin.usuarios")


@bp.post("/users/<int:usuario_id>/edit")
@admin_requerido
def editar_usuario(usuario_id):
    usuario = consultar("SELECT * FROM usuarios WHERE id = ?", (usuario_id,), uno=True)
    if usuario is None:
        abort(404)
    nombre = (request.form.get("nombre") or usuario["nombre"]).strip()
    rol = "admin" if request.form.get("rol") == "admin" else "estudiante"
    if usuario["rol"] == "admin" and rol != "admin" and _solo_queda_un_admin(usuario_id):
        flash("At least one active administrator must remain.", "error")
        return _volver("admin.usuarios")
    ejecutar("UPDATE usuarios SET nombre = ?, documento = ?, cargo = ?, rol = ? WHERE id = ?",
             (nombre, (request.form.get("documento") or "").strip(),
              (request.form.get("cargo") or "").strip(), rol, usuario_id))
    nueva = request.form.get("password") or ""
    if nueva:
        if len(nueva) < 8:
            flash("The password must be at least 8 characters long.", "error")
            return _volver("admin.usuarios")
        ejecutar("UPDATE usuarios SET password_hash = ? WHERE id = ?",
                 (hash_password(nueva), usuario_id))
    flash(f"{nombre} updated.", "ok")
    return _volver("admin.usuarios")


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
@admin_requerido
def cursos():
    lista = consultar(
        "SELECT c.*, (SELECT COUNT(*) FROM asignaciones a WHERE a.curso_id = c.id) AS asignados,"
        " (SELECT COUNT(*) FROM asignaciones a WHERE a.curso_id = c.id AND a.estado='aprobado')"
        " AS aprobados, q.id AS quiz_id,"
        " (SELECT COUNT(*) FROM preguntas p WHERE p.quiz_id = q.id) AS preguntas"
        " FROM cursos c LEFT JOIN quices q ON q.curso_id = c.id ORDER BY c.activo DESC, c.titulo")
    return render_template("admin/cursos.html", cursos=lista,
                           extensiones=sorted(config.EXT_PERMITIDAS))


@bp.post("/courses/create")
@admin_requerido
def crear_curso():
    titulo = (request.form.get("titulo") or "").strip()
    archivo = request.files.get("material")
    if not titulo or archivo is None or not archivo.filename:
        flash("The course needs a title and a material file.", "error")
        return _volver("admin.cursos")
    try:
        info = media.guardar_material(archivo)
    except media.ErrorMaterial as error:
        flash(str(error), "error")
        return _volver("admin.cursos")
    except Exception as error:
        flash(f"The material could not be processed: {error}", "error")
        return _volver("admin.cursos")

    try:
        horas = float(request.form.get("horas") or 1)
    except ValueError:
        horas = 1
    try:
        vigencia = int(request.form.get("vigencia_meses") or 12)
    except ValueError:
        vigencia = 12

    curso_id = ejecutar(
        "INSERT INTO cursos (titulo, descripcion, categoria, tipo_material, carpeta, archivo,"
        " archivo_original, duracion_segundos, paginas, horas, vigencia_meses, activo, creado_en)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (titulo, (request.form.get("descripcion") or "").strip(),
         (request.form.get("categoria") or "").strip(), info["tipo_material"], info["carpeta"],
         info["archivo"], info["archivo_original"], info["duracion_segundos"], info["paginas"],
         horas, vigencia, ahora()))
    if info["tipo_material"] == "documento":
        detalle = f"{info['paginas']} pages"
    else:
        detalle = f"{info['duracion_segundos'] / 60:.0f} minutes of video"
    flash(f"Course “{titulo}” created with {detalle}. Now set up its exam.", "ok")
    return redirect(url_for("admin.editar_quiz", curso_id=curso_id))


@bp.post("/courses/<int:curso_id>/edit")
@admin_requerido
def editar_curso(curso_id):
    curso = consultar("SELECT * FROM cursos WHERE id = ?", (curso_id,), uno=True)
    if curso is None:
        abort(404)
    ejecutar("UPDATE cursos SET titulo = ?, descripcion = ?, categoria = ?, horas = ?,"
             " vigencia_meses = ? WHERE id = ?",
             ((request.form.get("titulo") or curso["titulo"]).strip(),
              (request.form.get("descripcion") or "").strip(),
              (request.form.get("categoria") or "").strip(),
              float(request.form.get("horas") or curso["horas"]),
              int(request.form.get("vigencia_meses") or curso["vigencia_meses"]), curso_id))
    flash("Course updated.", "ok")
    return _volver("admin.cursos")


@bp.post("/courses/<int:curso_id>/status")
@admin_requerido
def cambiar_estado_curso(curso_id):
    curso = consultar("SELECT * FROM cursos WHERE id = ?", (curso_id,), uno=True)
    if curso is None:
        abort(404)
    ejecutar("UPDATE cursos SET activo = ? WHERE id = ?", (0 if curso["activo"] else 1, curso_id))
    flash("Course visibility updated.", "ok")
    return _volver("admin.cursos")


@bp.post("/courses/<int:curso_id>/delete")
@admin_requerido
def eliminar_curso(curso_id):
    curso = consultar("SELECT * FROM cursos WHERE id = ?", (curso_id,), uno=True)
    if curso is None:
        abort(404)
    media.eliminar_material(curso["carpeta"])
    ejecutar("DELETE FROM cursos WHERE id = ?", (curso_id,))
    flash(f"Course “{curso['titulo']}” deleted.", "ok")
    return _volver("admin.cursos")


# --------------------------------------------------------------------------- asignaciones
@bp.route("/assignments")
@admin_requerido
def asignaciones():
    lista = consultar(
        "SELECT a.*, u.nombre AS usuario, u.email, c.titulo AS curso FROM asignaciones a"
        " JOIN usuarios u ON u.id = a.usuario_id JOIN cursos c ON c.id = a.curso_id"
        " ORDER BY a.asignado_en DESC")
    return render_template(
        "admin/asignaciones.html", asignaciones=lista,
        usuarios=consultar("SELECT * FROM usuarios WHERE activo = 1 ORDER BY nombre"),
        cursos=consultar("SELECT * FROM cursos WHERE activo = 1 ORDER BY titulo"),
        estados=ESTADOS)


@bp.post("/assignments/create")
@admin_requerido
def crear_asignacion():
    curso_ids = request.form.getlist("curso_id")
    usuario_ids = request.form.getlist("usuario_id")
    fecha_limite = (request.form.get("fecha_limite") or "").strip() or None
    if not curso_ids or not usuario_ids:
        flash("Select at least one course and one person.", "error")
        return _volver("admin.asignaciones")

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
@admin_requerido
def eliminar_asignacion(asignacion_id):
    ejecutar("DELETE FROM asignaciones WHERE id = ?", (asignacion_id,))
    flash("Assignment removed.", "ok")
    return redirect(request.referrer or url_for("admin.asignaciones"))


@bp.post("/assignments/<int:asignacion_id>/reopen")
@admin_requerido
def reabrir_asignacion(asignacion_id):
    ejecutar("UPDATE asignaciones SET estado = 'pendiente', avance = 0, max_posicion = 0,"
             " material_completo = 0, completado_en = NULL WHERE id = ?", (asignacion_id,))
    ejecutar("DELETE FROM intentos WHERE asignacion_id = ?", (asignacion_id,))
    flash("Training reopened: the person must review the content and retake the exam.", "ok")
    return redirect(request.referrer or url_for("admin.asignaciones"))


# --------------------------------------------------------------------------- quices
@bp.route("/exams")
@admin_requerido
def quices():
    lista = consultar(
        "SELECT c.id AS curso_id, c.titulo, c.activo, q.id AS quiz_id, q.titulo AS quiz_titulo,"
        " q.puntaje_minimo, q.intentos_max,"
        " (SELECT COUNT(*) FROM preguntas p WHERE p.quiz_id = q.id) AS preguntas"
        " FROM cursos c LEFT JOIN quices q ON q.curso_id = c.id ORDER BY c.titulo")
    return render_template("admin/quices.html", cursos=lista)


@bp.route("/exams/<int:curso_id>")
@admin_requerido
def editar_quiz(curso_id):
    curso = consultar("SELECT * FROM cursos WHERE id = ?", (curso_id,), uno=True)
    if curso is None:
        abort(404)
    quiz = consultar("SELECT * FROM quices WHERE curso_id = ?", (curso_id,), uno=True)
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
    return render_template("admin/quiz_editor.html", curso=curso, quiz=quiz, preguntas=preguntas)


@bp.post("/exams/<int:curso_id>/save")
@admin_requerido
def guardar_quiz(curso_id):
    titulo = (request.form.get("titulo") or "").strip() or "Course exam"
    try:
        minimo = max(1, min(100, int(request.form.get("puntaje_minimo") or 70)))
        intentos = max(1, min(10, int(request.form.get("intentos_max") or 3)))
    except ValueError:
        minimo, intentos = 70, 3
    quiz = consultar("SELECT * FROM quices WHERE curso_id = ?", (curso_id,), uno=True)
    if quiz:
        ejecutar("UPDATE quices SET titulo = ?, puntaje_minimo = ?, intentos_max = ? WHERE id = ?",
                 (titulo, minimo, intentos, quiz["id"]))
    else:
        ejecutar("INSERT INTO quices (curso_id, titulo, puntaje_minimo, intentos_max, creado_en)"
                 " VALUES (?, ?, ?, ?, ?)", (curso_id, titulo, minimo, intentos, ahora()))
    flash("Exam saved.", "ok")
    return redirect(url_for("admin.editar_quiz", curso_id=curso_id))


@bp.post("/exams/<int:curso_id>/questions")
@admin_requerido
def agregar_pregunta(curso_id):
    quiz = consultar("SELECT * FROM quices WHERE curso_id = ?", (curso_id,), uno=True)
    if quiz is None:
        flash("Save the exam settings first.", "error")
        return redirect(url_for("admin.editar_quiz", curso_id=curso_id))

    enunciado = (request.form.get("enunciado") or "").strip()
    opciones = [(request.form.get(f"opcion{i}") or "").strip() for i in range(1, 5)]
    opciones_validas = [(i, texto) for i, texto in enumerate(opciones, start=1) if texto]
    correcta = request.form.get("correcta")

    if not enunciado or len(opciones_validas) < 2:
        flash("Write the question and at least two answer options.", "error")
        return redirect(url_for("admin.editar_quiz", curso_id=curso_id))
    if correcta not in {str(i) for i, _ in opciones_validas}:
        flash("Mark which of the written options is the correct one.", "error")
        return redirect(url_for("admin.editar_quiz", curso_id=curso_id))

    orden = consultar("SELECT IFNULL(MAX(orden), 0) + 1 AS siguiente FROM preguntas"
                      " WHERE quiz_id = ?", (quiz["id"],), uno=True)["siguiente"]
    pregunta_id = ejecutar("INSERT INTO preguntas (quiz_id, enunciado, orden) VALUES (?, ?, ?)",
                           (quiz["id"], enunciado, orden))
    for indice, (numero, texto) in enumerate(opciones_validas, start=1):
        ejecutar("INSERT INTO opciones (pregunta_id, texto, correcta, orden) VALUES (?, ?, ?, ?)",
                 (pregunta_id, texto, int(str(numero) == correcta), indice))
    flash("Question added.", "ok")
    return redirect(url_for("admin.editar_quiz", curso_id=curso_id))


@bp.post("/questions/<int:pregunta_id>/delete")
@admin_requerido
def eliminar_pregunta(pregunta_id):
    pregunta = consultar(
        "SELECT p.*, q.curso_id FROM preguntas p JOIN quices q ON q.id = p.quiz_id"
        " WHERE p.id = ?", (pregunta_id,), uno=True)
    if pregunta is None:
        abort(404)
    ejecutar("DELETE FROM preguntas WHERE id = ?", (pregunta_id,))
    flash("Question deleted.", "ok")
    return redirect(url_for("admin.editar_quiz", curso_id=pregunta["curso_id"]))


# --------------------------------------------------------------------------- reportes
@bp.route("/summary")
@admin_requerido
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
    parametros = []
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
                           cursos=consultar("SELECT id, titulo FROM cursos ORDER BY titulo"),
                           curso_id=curso_id, estado=estado, q=busqueda, hoy=ahora()[:10])


@bp.route("/certificates")
@admin_requerido
def certificados():
    usuario = (request.args.get("usuario") or "").strip()
    curso = (request.args.get("curso") or "").strip()
    sql = ("SELECT ce.*, u.nombre AS usuario, u.email, u.documento, c.titulo AS curso"
           " FROM certificados ce JOIN usuarios u ON u.id = ce.usuario_id"
           " JOIN cursos c ON c.id = ce.curso_id WHERE 1 = 1")
    parametros = []
    if usuario:
        sql += " AND (u.nombre LIKE ? OR u.email LIKE ? OR IFNULL(u.documento,'') LIKE ?)"
        parametros += [f"%{usuario}%"] * 3
    if curso:
        sql += " AND c.titulo LIKE ?"
        parametros.append(f"%{curso}%")
    sql += " ORDER BY ce.emitido_en DESC"
    return render_template("admin/certificados.html", certificados=consultar(sql, tuple(parametros)),
                           usuario=usuario, curso=curso)


@bp.post("/certificates/<codigo>/resend")
@admin_requerido
def reenviar_certificado(codigo):
    certificado = consultar(
        "SELECT ce.*, u.nombre, u.email, u.documento, c.titulo FROM certificados ce"
        " JOIN usuarios u ON u.id = ce.usuario_id JOIN cursos c ON c.id = ce.curso_id"
        " WHERE ce.codigo = ?", (codigo,), uno=True)
    if certificado is None:
        abort(404)
    ajustes = obtener_config()
    usuario = consultar("SELECT * FROM usuarios WHERE id = ?", (certificado["usuario_id"],), uno=True)
    curso = consultar("SELECT * FROM cursos WHERE id = ?", (certificado["curso_id"],), uno=True)
    ruta = config.CERT_DIR / certificado["archivo"]
    if not ruta.exists():
        import certificados as generador
        ruta = generador.generar_certificado(usuario, curso, certificado["puntaje"], codigo, ajustes)
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
@bp.route("/settings")
@admin_requerido
def configuracion():
    return render_template("admin/configuracion.html", cfg=obtener_config(),
                           correos=consultar("SELECT * FROM correos ORDER BY id DESC LIMIT 25"),
                           bandeja=str(config.OUTBOX_DIR))


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
