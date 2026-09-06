"""Vista del estudiante: cursos asignados, material, examen y certificado."""
import json
import time

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template,
                   request, send_file, session, url_for)

import certificados as cert
import config
import mailer
import media
from auth import login_requerido, usuario_actual
from database import ahora, consultar, ejecutar, obtener_config

bp = Blueprint("estudiante", __name__)

SQL_ASIGNACION = """
SELECT a.*, c.titulo, c.descripcion, c.categoria, c.tipo_material, c.carpeta,
       c.archivo, c.duracion_segundos, c.paginas, c.horas, c.vigencia_meses,
       q.id AS quiz_id, q.titulo AS quiz_titulo, q.puntaje_minimo, q.intentos_max
FROM asignaciones a
JOIN cursos c ON c.id = a.curso_id
LEFT JOIN quices q ON q.curso_id = c.id
"""


def _asignacion_del_usuario(asignacion_id, usuario_id):
    fila = consultar(SQL_ASIGNACION + " WHERE a.id = ? AND a.usuario_id = ?",
                     (asignacion_id, usuario_id), uno=True)
    if fila is None:
        abort(404)
    return fila


def _intentos_usados(asignacion_id) -> int:
    fila = consultar("SELECT COUNT(*) AS n FROM intentos WHERE asignacion_id = ?",
                     (asignacion_id,), uno=True)
    return fila["n"]


def _preguntas_del_quiz(quiz_id):
    preguntas = consultar(
        "SELECT * FROM preguntas WHERE quiz_id = ? ORDER BY orden, id", (quiz_id,))
    resultado = []
    for pregunta in preguntas:
        opciones = consultar(
            "SELECT id, texto FROM opciones WHERE pregunta_id = ? ORDER BY orden, id",
            (pregunta["id"],))
        resultado.append({"id": pregunta["id"], "enunciado": pregunta["enunciado"],
                          "opciones": opciones})
    return resultado


@bp.route("/my-training")
@login_requerido
def panel():
    usuario = usuario_actual()
    asignaciones = consultar(
        SQL_ASIGNACION + " WHERE a.usuario_id = ? AND c.activo = 1"
        " ORDER BY CASE a.estado WHEN 'aprobado' THEN 1 ELSE 0 END,"
        " a.fecha_limite IS NULL, a.fecha_limite, a.asignado_en DESC",
        (usuario["id"],))
    certificados_usuario = {
        fila["curso_id"]: fila for fila in consultar(
            "SELECT * FROM certificados WHERE usuario_id = ?", (usuario["id"],))
    }
    pendientes = [a for a in asignaciones if a["estado"] != "aprobado"]
    return render_template("estudiante/panel.html", asignaciones=asignaciones,
                           certificados=certificados_usuario, pendientes=len(pendientes),
                           hoy=ahora()[:10])


@bp.route("/course/<int:asignacion_id>")
@login_requerido
def curso(asignacion_id):
    usuario = usuario_actual()
    asignacion = _asignacion_del_usuario(asignacion_id, usuario["id"])
    if asignacion["estado"] == "pendiente":
        ejecutar("UPDATE asignaciones SET estado = 'en_progreso' WHERE id = ?", (asignacion_id,))
        asignacion = _asignacion_del_usuario(asignacion_id, usuario["id"])

    certificado = consultar(
        "SELECT * FROM certificados WHERE usuario_id = ? AND curso_id = ?",
        (usuario["id"], asignacion["curso_id"]), uno=True)
    intentos = consultar(
        "SELECT * FROM intentos WHERE asignacion_id = ? ORDER BY finalizado_en DESC",
        (asignacion_id,))
    return render_template("estudiante/curso.html", a=asignacion, certificado=certificado,
                           intentos=intentos, intentos_usados=len(intentos),
                           minimo_video=config.PORCENTAJE_MIN_VIDEO,
                           segundos_pagina=config.SEGUNDOS_MIN_POR_PAGINA)


# --------------------------------------------------------------------------- material
@bp.route("/material/<int:asignacion_id>/video")
@login_requerido
def video(asignacion_id):
    asignacion = _asignacion_del_usuario(asignacion_id, usuario_actual()["id"])
    if asignacion["tipo_material"] != "video":
        abort(404)
    ruta = media.ruta_video(asignacion)
    if not ruta.exists():
        abort(404)
    respuesta = send_file(ruta, conditional=True, max_age=0)
    respuesta.headers["Content-Disposition"] = "inline"
    return respuesta


@bp.route("/material/<int:asignacion_id>/page/<int:numero>.png")
@login_requerido
def pagina(asignacion_id, numero):
    asignacion = _asignacion_del_usuario(asignacion_id, usuario_actual()["id"])
    if asignacion["tipo_material"] != "documento":
        abort(404)
    # Solo se entregan las páginas ya alcanzadas (o la siguiente).
    if numero < 1 or numero > asignacion["paginas"]:
        abort(404)
    if numero > max(asignacion["max_posicion"], 0) + 1 and not asignacion["material_completo"]:
        abort(403)
    ruta = media.ruta_pagina(asignacion, numero)
    if not ruta.exists():
        abort(404)
    return send_file(ruta, mimetype="image/png", max_age=0)


@bp.post("/api/progress/<int:asignacion_id>")
@login_requerido
def avance(asignacion_id):
    asignacion = _asignacion_del_usuario(asignacion_id, usuario_actual()["id"])
    datos = request.get_json(silent=True) or {}
    maximo = float(asignacion["max_posicion"] or 0)
    completo = bool(asignacion["material_completo"])

    if asignacion["tipo_material"] == "video":
        total = float(asignacion["duracion_segundos"] or 0)
        posicion = float(datos.get("posicion") or 0)
        if total <= 0:
            return jsonify(error="This course has no recorded duration."), 400
        if posicion > maximo + config.SALTO_MAX_SEGUNDOS:
            return jsonify(avance=round(100 * maximo / total, 2), max_posicion=maximo,
                           habilitado=completo,
                           mensaje="The video must be watched in order; skipping is not recorded."), 200
        maximo = min(max(maximo, posicion), total)
        porcentaje = min(100.0, 100 * maximo / total)
        completo = completo or porcentaje >= config.PORCENTAJE_MIN_VIDEO
    else:
        total_paginas = int(asignacion["paginas"] or 0)
        pagina_actual = int(datos.get("pagina") or 0)
        if total_paginas <= 0:
            return jsonify(error="This course has no recorded pages."), 400
        if pagina_actual > maximo + 1:
            return jsonify(mensaje="Move through the pages one at a time."), 200
        clave = f"pag_{asignacion_id}"
        if pagina_actual > maximo:
            ultimo = session.get(clave, 0)
            if time.time() - ultimo < config.SEGUNDOS_MIN_POR_PAGINA - 1:
                return jsonify(avance=round(100 * maximo / total_paginas, 2),
                               max_posicion=maximo, habilitado=completo,
                               mensaje="Take a few seconds to read this page."), 200
            session[clave] = time.time()
        maximo = min(max(maximo, pagina_actual), total_paginas)
        porcentaje = min(100.0, 100 * maximo / total_paginas)
        completo = completo or maximo >= total_paginas

    estado = asignacion["estado"]
    if estado == "pendiente":
        estado = "en_progreso"
    ejecutar("UPDATE asignaciones SET max_posicion = ?, avance = ?, material_completo = ?,"
             " estado = ? WHERE id = ?",
             (maximo, round(porcentaje, 2), int(completo), estado, asignacion_id))
    return jsonify(avance=round(porcentaje, 2), max_posicion=maximo, habilitado=completo)


# --------------------------------------------------------------------------- examen
@bp.route("/course/<int:asignacion_id>/exam", methods=["GET", "POST"])
@login_requerido
def examen(asignacion_id):
    usuario = usuario_actual()
    asignacion = _asignacion_del_usuario(asignacion_id, usuario["id"])

    if not asignacion["quiz_id"]:
        flash("This course has no exam configured yet.", "error")
        return redirect(url_for("estudiante.curso", asignacion_id=asignacion_id))
    if not asignacion["material_completo"]:
        flash("Finish the course content before taking the exam.", "error")
        return redirect(url_for("estudiante.curso", asignacion_id=asignacion_id))
    if asignacion["estado"] == "aprobado":
        flash("You already passed this course.", "ok")
        return redirect(url_for("estudiante.curso", asignacion_id=asignacion_id))

    usados = _intentos_usados(asignacion_id)
    if usados >= asignacion["intentos_max"]:
        flash("You have used all attempts. Ask the administrator to reopen this training.",
              "error")
        return redirect(url_for("estudiante.curso", asignacion_id=asignacion_id))

    preguntas = _preguntas_del_quiz(asignacion["quiz_id"])
    if not preguntas:
        flash("This exam has no questions loaded yet.", "error")
        return redirect(url_for("estudiante.curso", asignacion_id=asignacion_id))

    if request.method == "POST":
        return _calificar(asignacion, preguntas, usados)

    return render_template("estudiante/examen.html", a=asignacion, preguntas=preguntas,
                           intento_numero=usados + 1)


def _calificar(asignacion, preguntas, usados):
    correctas_por_pregunta = {}
    for pregunta in preguntas:
        fila = consultar("SELECT id FROM opciones WHERE pregunta_id = ? AND correcta = 1",
                         (pregunta["id"],), uno=True)
        correctas_por_pregunta[pregunta["id"]] = fila["id"] if fila else None

    respuestas, aciertos = {}, 0
    for pregunta in preguntas:
        elegida = request.form.get(f"p{pregunta['id']}")
        elegida = int(elegida) if (elegida or "").isdigit() else None
        respuestas[str(pregunta["id"])] = elegida
        if elegida is not None and elegida == correctas_por_pregunta[pregunta["id"]]:
            aciertos += 1

    puntaje = round(100 * aciertos / len(preguntas), 2)
    aprobado = puntaje >= asignacion["puntaje_minimo"]

    intento_id = ejecutar(
        "INSERT INTO intentos (asignacion_id, quiz_id, usuario_id, puntaje, aprobado,"
        " respuestas, finalizado_en) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (asignacion["id"], asignacion["quiz_id"], asignacion["usuario_id"], puntaje,
         int(aprobado), json.dumps(respuestas), ahora()))

    if aprobado:
        ejecutar("UPDATE asignaciones SET estado = 'aprobado', completado_en = ?,"
                 " avance = 100 WHERE id = ?", (ahora(), asignacion["id"]))
        codigo = emitir_certificado(asignacion, intento_id, puntaje)
    else:
        ejecutar("UPDATE asignaciones SET estado = 'reprobado' WHERE id = ?", (asignacion["id"],))
        codigo = None

    return render_template("estudiante/resultado.html", a=asignacion, puntaje=puntaje,
                           aprobado=aprobado, aciertos=aciertos, total=len(preguntas),
                           codigo=codigo, intentos_restantes=asignacion["intentos_max"] - usados - 1)


def emitir_certificado(asignacion, intento_id, puntaje) -> str:
    """Genera el PDF, lo guarda y lo envía al estudiante y al administrador."""
    usuario = consultar("SELECT * FROM usuarios WHERE id = ?", (asignacion["usuario_id"],), uno=True)
    curso_ = consultar("SELECT * FROM cursos WHERE id = ?", (asignacion["curso_id"],), uno=True)
    ajustes = obtener_config()

    existente = consultar("SELECT * FROM certificados WHERE usuario_id = ? AND curso_id = ?",
                          (usuario["id"], curso_["id"]), uno=True)
    codigo = existente["codigo"] if existente else cert.nuevo_codigo()
    ruta = cert.generar_certificado(usuario, curso_, puntaje, codigo, ajustes)

    if existente:
        ejecutar("UPDATE certificados SET intento_id = ?, puntaje = ?, archivo = ?,"
                 " emitido_en = ? WHERE id = ?",
                 (intento_id, puntaje, ruta.name, ahora(), existente["id"]))
    else:
        ejecutar("INSERT INTO certificados (codigo, usuario_id, curso_id, intento_id, puntaje,"
                 " archivo, emitido_en) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (codigo, usuario["id"], curso_["id"], intento_id, puntaje, ruta.name, ahora()))

    asunto = f"Certificate of Training · {curso_['titulo']}"
    mailer.enviar(ajustes, usuario["email"], asunto,
                  mailer.cuerpo_certificado(usuario, curso_, puntaje, codigo, ajustes), ruta)
    admin_email = ajustes.get("admin_email")
    if admin_email:
        mailer.enviar(ajustes, admin_email, f"[Copy] {asunto}",
                      mailer.cuerpo_certificado(usuario, curso_, puntaje, codigo, ajustes,
                                                para_admin=True), ruta)
    return codigo


@bp.route("/certificate/<codigo>.pdf")
@login_requerido
def descargar_certificado(codigo):
    usuario = usuario_actual()
    certificado = consultar("SELECT * FROM certificados WHERE codigo = ?", (codigo,), uno=True)
    if certificado is None:
        abort(404)
    if usuario["rol"] != "admin" and certificado["usuario_id"] != usuario["id"]:
        abort(403)
    ruta = config.CERT_DIR / certificado["archivo"]
    if not ruta.exists():   # se regenera si el archivo se perdió
        datos_usuario = consultar("SELECT * FROM usuarios WHERE id = ?",
                                  (certificado["usuario_id"],), uno=True)
        curso_ = consultar("SELECT * FROM cursos WHERE id = ?", (certificado["curso_id"],), uno=True)
        ruta = cert.generar_certificado(datos_usuario, curso_, certificado["puntaje"],
                                        certificado["codigo"], obtener_config())
    return send_file(ruta, mimetype="application/pdf", as_attachment=True,
                     download_name=f"{codigo}.pdf")
