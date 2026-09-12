"""Student view: assigned courses, modules, material, exams and certificate."""
import json
import random
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
SELECT a.*, c.titulo, c.descripcion, c.categoria, c.horas, c.vigencia_meses
FROM asignaciones a
JOIN cursos c ON c.id = a.curso_id
"""


# --------------------------------------------------------------------------- helpers
def _asignacion(asignacion_id, usuario_id):
    fila = consultar(SQL_ASIGNACION + " WHERE a.id = ? AND a.usuario_id = ?",
                     (asignacion_id, usuario_id), uno=True)
    if fila is None:
        abort(404)
    return fila


def modulos_con_avance(asignacion_id, curso_id):
    """Módulos del curso, cada uno con su avance, su quiz y sus intentos."""
    modulos = consultar(
        "SELECT m.*, q.id AS quiz_id, q.titulo AS quiz_titulo, q.puntaje_minimo,"
        " q.intentos_max, q.preguntas_por_intento, q.mezclar,"
        " (SELECT COUNT(*) FROM preguntas p WHERE p.quiz_id = q.id) AS banco"
        " FROM modulos m LEFT JOIN quices q ON q.modulo_id = m.id"
        " WHERE m.curso_id = ? ORDER BY m.orden, m.id", (curso_id,))

    resultado = []
    for modulo in modulos:
        avance = consultar(
            "SELECT * FROM avance_modulos WHERE asignacion_id = ? AND modulo_id = ?",
            (asignacion_id, modulo["id"]), uno=True)
        if avance is None:
            ejecutar("INSERT OR IGNORE INTO avance_modulos (asignacion_id, modulo_id)"
                     " VALUES (?, ?)", (asignacion_id, modulo["id"]))
            avance = consultar(
                "SELECT * FROM avance_modulos WHERE asignacion_id = ? AND modulo_id = ?",
                (asignacion_id, modulo["id"]), uno=True)
        intentos = consultar(
            "SELECT * FROM intentos WHERE asignacion_id = ? AND modulo_id = ?"
            " ORDER BY finalizado_en DESC", (asignacion_id, modulo["id"]))
        resultado.append({
            "m": modulo,
            "avance": avance,
            "intentos": intentos,
            "aprobado": avance["estado"] == "aprobado",
            "mejor": max([i["puntaje"] for i in intentos], default=None),
        })
    return resultado


def recalcular_curso(asignacion_id, curso_id):
    """Actualiza el estado global del curso a partir de sus módulos."""
    modulos = modulos_con_avance(asignacion_id, curso_id)
    if not modulos:
        return False
    total = sum(m["avance"]["avance"] for m in modulos) / len(modulos)
    listo = all(
        m["aprobado"] if m["m"]["quiz_id"] else m["avance"]["material_completo"]
        for m in modulos)
    reprobado = any(m["avance"]["estado"] == "reprobado" for m in modulos)

    estado = "aprobado" if listo else ("reprobado" if reprobado
                                       else ("en_progreso" if total > 0 else "pendiente"))
    actual = consultar("SELECT * FROM asignaciones WHERE id = ?", (asignacion_id,), uno=True)
    ejecutar("UPDATE asignaciones SET avance = ?, estado = ?, completado_en = ? WHERE id = ?",
             (round(total, 2), estado,
              actual["completado_en"] or (ahora() if listo else None), asignacion_id))
    return listo and actual["estado"] != "aprobado"


# --------------------------------------------------------------------------- panel
@bp.route("/my-training")
@login_requerido
def panel():
    usuario = usuario_actual()
    asignaciones = consultar(
        SQL_ASIGNACION + " WHERE a.usuario_id = ? AND c.activo = 1"
        " ORDER BY CASE a.estado WHEN 'aprobado' THEN 1 ELSE 0 END,"
        " a.fecha_limite IS NULL, a.fecha_limite, a.asignado_en DESC",
        (usuario["id"],))
    conteo = {
        fila["curso_id"]: fila["n"] for fila in consultar(
            "SELECT curso_id, COUNT(*) AS n FROM modulos GROUP BY curso_id")
    }
    certificados_usuario = {
        fila["curso_id"]: fila for fila in consultar(
            "SELECT * FROM certificados WHERE usuario_id = ?", (usuario["id"],))
    }
    pendientes = [a for a in asignaciones if a["estado"] != "aprobado"]
    return render_template("estudiante/panel.html", asignaciones=asignaciones,
                           certificados=certificados_usuario, pendientes=len(pendientes),
                           modulos_por_curso=conteo, hoy=ahora()[:10])


@bp.route("/course/<int:asignacion_id>")
@login_requerido
def curso(asignacion_id):
    usuario = usuario_actual()
    asignacion = _asignacion(asignacion_id, usuario["id"])
    modulos = modulos_con_avance(asignacion_id, asignacion["curso_id"])
    recalcular_curso(asignacion_id, asignacion["curso_id"])
    asignacion = _asignacion(asignacion_id, usuario["id"])
    certificado = consultar(
        "SELECT * FROM certificados WHERE usuario_id = ? AND curso_id = ?",
        (usuario["id"], asignacion["curso_id"]), uno=True)
    return render_template("estudiante/curso.html", a=asignacion, modulos=modulos,
                           certificado=certificado)


@bp.route("/course/<int:asignacion_id>/module/<int:modulo_id>")
@login_requerido
def modulo(asignacion_id, modulo_id):
    usuario = usuario_actual()
    asignacion = _asignacion(asignacion_id, usuario["id"])
    modulos = modulos_con_avance(asignacion_id, asignacion["curso_id"])
    actual = next((m for m in modulos if m["m"]["id"] == modulo_id), None)
    if actual is None:
        abort(404)
    posicion = [m["m"]["id"] for m in modulos].index(modulo_id)
    return render_template("estudiante/modulo.html", a=asignacion, bloque=actual,
                           modulos=modulos, posicion=posicion,
                           minimo_video=config.PORCENTAJE_MIN_VIDEO,
                           segundos_pagina=config.SEGUNDOS_MIN_POR_PAGINA)


# --------------------------------------------------------------------------- material
def _modulo_autorizado(asignacion_id, modulo_id, usuario_id):
    fila = consultar(
        "SELECT m.*, v.max_posicion, v.material_completo FROM modulos m"
        " JOIN asignaciones a ON a.curso_id = m.curso_id"
        " LEFT JOIN avance_modulos v ON v.asignacion_id = a.id AND v.modulo_id = m.id"
        " WHERE m.id = ? AND a.id = ? AND a.usuario_id = ?",
        (modulo_id, asignacion_id, usuario_id), uno=True)
    if fila is None:
        abort(404)
    return fila


@bp.route("/material/<int:asignacion_id>/<int:modulo_id>/video")
@login_requerido
def video(asignacion_id, modulo_id):
    fila = _modulo_autorizado(asignacion_id, modulo_id, usuario_actual()["id"])
    if fila["tipo_material"] != "video":
        abort(404)
    ruta = media.ruta_video(fila)
    if not ruta.exists():
        abort(404)
    respuesta = send_file(ruta, conditional=True, max_age=0)
    respuesta.headers["Content-Disposition"] = "inline"
    return respuesta


@bp.route("/material/<int:asignacion_id>/<int:modulo_id>/page/<int:numero>.png")
@login_requerido
def pagina(asignacion_id, modulo_id, numero):
    fila = _modulo_autorizado(asignacion_id, modulo_id, usuario_actual()["id"])
    if fila["tipo_material"] != "documento":
        abort(404)
    if numero < 1 or numero > fila["paginas"]:
        abort(404)
    if numero > (fila["max_posicion"] or 0) + 1 and not fila["material_completo"]:
        abort(403)
    ruta = media.ruta_pagina(fila, numero)
    if not ruta.exists():
        abort(404)
    return send_file(ruta, mimetype="image/png", max_age=0)


@bp.post("/api/progress/<int:asignacion_id>/<int:modulo_id>")
@login_requerido
def avance(asignacion_id, modulo_id):
    usuario = usuario_actual()
    fila = _modulo_autorizado(asignacion_id, modulo_id, usuario["id"])
    datos = request.get_json(silent=True) or {}
    maximo = float(fila["max_posicion"] or 0)
    completo = bool(fila["material_completo"])

    if fila["tipo_material"] == "video":
        total = float(fila["duracion_segundos"] or 0)
        posicion = float(datos.get("posicion") or 0)
        if total <= 0:
            return jsonify(error="This module has no recorded duration."), 400
        if posicion > maximo + config.SALTO_MAX_SEGUNDOS:
            return jsonify(avance=round(100 * maximo / total, 2), max_posicion=maximo,
                           habilitado=completo,
                           mensaje="The video must be watched in order; skipping is not recorded."), 200
        maximo = min(max(maximo, posicion), total)
        porcentaje = min(100.0, 100 * maximo / total)
        completo = completo or porcentaje >= config.PORCENTAJE_MIN_VIDEO
    else:
        total_paginas = int(fila["paginas"] or 0)
        pagina_actual = int(datos.get("pagina") or 0)
        if total_paginas <= 0:
            return jsonify(error="This module has no recorded pages."), 400
        if pagina_actual > maximo + 1:
            return jsonify(mensaje="Move through the pages one at a time."), 200
        clave = f"pag_{asignacion_id}_{modulo_id}"
        if pagina_actual > maximo:
            if time.time() - session.get(clave, 0) < config.SEGUNDOS_MIN_POR_PAGINA - 1:
                return jsonify(avance=round(100 * maximo / total_paginas, 2),
                               max_posicion=maximo, habilitado=completo,
                               mensaje="Take a few seconds to read this page."), 200
            session[clave] = time.time()
        maximo = min(max(maximo, pagina_actual), total_paginas)
        porcentaje = min(100.0, 100 * maximo / total_paginas)
        completo = completo or maximo >= total_paginas

    estado = consultar("SELECT estado FROM avance_modulos WHERE asignacion_id = ? AND"
                       " modulo_id = ?", (asignacion_id, modulo_id), uno=True)["estado"]
    if estado == "pendiente":
        estado = "en_progreso"
    ejecutar("UPDATE avance_modulos SET max_posicion = ?, avance = ?, material_completo = ?,"
             " estado = ? WHERE asignacion_id = ? AND modulo_id = ?",
             (maximo, round(porcentaje, 2), int(completo), estado, asignacion_id, modulo_id))
    recalcular_curso(asignacion_id, fila["curso_id"])
    return jsonify(avance=round(porcentaje, 2), max_posicion=maximo, habilitado=completo)


# --------------------------------------------------------------------------- exam
@bp.route("/course/<int:asignacion_id>/module/<int:modulo_id>/exam", methods=["GET", "POST"])
@login_requerido
def examen(asignacion_id, modulo_id):
    usuario = usuario_actual()
    asignacion = _asignacion(asignacion_id, usuario["id"])
    bloque = next((m for m in modulos_con_avance(asignacion_id, asignacion["curso_id"])
                   if m["m"]["id"] == modulo_id), None)
    if bloque is None:
        abort(404)

    quiz_id = bloque["m"]["quiz_id"]
    if not quiz_id:
        flash("This module has no exam configured yet.", "error")
        return redirect(url_for("estudiante.modulo", asignacion_id=asignacion_id, modulo_id=modulo_id))
    if not bloque["avance"]["material_completo"]:
        flash("Finish the module content before taking the exam.", "error")
        return redirect(url_for("estudiante.modulo", asignacion_id=asignacion_id, modulo_id=modulo_id))
    if bloque["aprobado"]:
        flash("You already passed this module.", "ok")
        return redirect(url_for("estudiante.modulo", asignacion_id=asignacion_id, modulo_id=modulo_id))

    usados = len(bloque["intentos"])
    if usados >= bloque["m"]["intentos_max"]:
        flash("You have used all attempts. Ask the administrator to reopen this training.", "error")
        return redirect(url_for("estudiante.modulo", asignacion_id=asignacion_id, modulo_id=modulo_id))

    banco = consultar("SELECT * FROM preguntas WHERE quiz_id = ? ORDER BY orden, id", (quiz_id,))
    if not banco:
        flash("This exam has no questions loaded yet.", "error")
        return redirect(url_for("estudiante.modulo", asignacion_id=asignacion_id, modulo_id=modulo_id))

    if request.method == "POST":
        return _calificar(asignacion, bloque, usados)

    # Selección aleatoria del banco y mezcla de opciones
    limite = bloque["m"]["preguntas_por_intento"] or 0
    seleccion = list(banco)
    if bloque["m"]["mezclar"]:
        random.shuffle(seleccion)
    if 0 < limite < len(seleccion):
        seleccion = seleccion[:limite]

    preguntas = []
    for pregunta in seleccion:
        opciones = consultar(
            "SELECT id, texto FROM opciones WHERE pregunta_id = ? ORDER BY orden, id",
            (pregunta["id"],))
        opciones = list(opciones)
        if bloque["m"]["mezclar"]:
            random.shuffle(opciones)
        preguntas.append({"id": pregunta["id"], "enunciado": pregunta["enunciado"],
                          "opciones": opciones})

    return render_template("estudiante/examen.html", a=asignacion, bloque=bloque,
                           preguntas=preguntas, intento_numero=usados + 1,
                           ids=",".join(str(p["id"]) for p in preguntas))


def _calificar(asignacion, bloque, usados):
    quiz_id = bloque["m"]["quiz_id"]
    modulo_id = bloque["m"]["id"]
    ids = [int(x) for x in (request.form.get("ids") or "").split(",") if x.strip().isdigit()]
    validas = {p["id"] for p in consultar(
        "SELECT id FROM preguntas WHERE quiz_id = ?", (quiz_id,))}
    ids = [i for i in ids if i in validas]
    if not ids:
        flash("The exam session expired. Please start it again.", "error")
        return redirect(url_for("estudiante.examen", asignacion_id=asignacion["id"],
                                modulo_id=modulo_id))

    respuestas, aciertos = {}, 0
    for pregunta_id in ids:
        correcta = consultar("SELECT id FROM opciones WHERE pregunta_id = ? AND correcta = 1",
                             (pregunta_id,), uno=True)
        elegida = request.form.get(f"p{pregunta_id}")
        elegida = int(elegida) if (elegida or "").isdigit() else None
        respuestas[str(pregunta_id)] = elegida
        if correcta and elegida == correcta["id"]:
            aciertos += 1

    puntaje = round(100 * aciertos / len(ids), 2)
    aprobado = puntaje >= bloque["m"]["puntaje_minimo"]

    ejecutar("INSERT INTO intentos (asignacion_id, modulo_id, quiz_id, usuario_id, puntaje,"
             " aprobado, respuestas, finalizado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
             (asignacion["id"], modulo_id, quiz_id, asignacion["usuario_id"], puntaje,
              int(aprobado), json.dumps(respuestas), ahora()))
    ejecutar("UPDATE avance_modulos SET estado = ?, completado_en = ? WHERE asignacion_id = ?"
             " AND modulo_id = ?",
             ("aprobado" if aprobado else "reprobado", ahora() if aprobado else None,
              asignacion["id"], modulo_id))

    curso_listo = recalcular_curso(asignacion["id"], asignacion["curso_id"])
    codigo = emitir_certificado(asignacion, puntaje) if curso_listo else None
    certificado = consultar("SELECT codigo FROM certificados WHERE usuario_id = ? AND"
                            " curso_id = ?", (asignacion["usuario_id"], asignacion["curso_id"]),
                            uno=True)

    return render_template("estudiante/resultado.html", a=asignacion, bloque=bloque,
                           puntaje=puntaje, aprobado=aprobado, aciertos=aciertos,
                           total=len(ids), codigo=codigo or (certificado["codigo"] if certificado else None),
                           curso_listo=curso_listo,
                           intentos_restantes=bloque["m"]["intentos_max"] - usados - 1)


def emitir_certificado(asignacion, puntaje) -> str:
    """Nota final del curso = promedio de los mejores intentos de cada módulo."""
    usuario = consultar("SELECT * FROM usuarios WHERE id = ?", (asignacion["usuario_id"],), uno=True)
    curso_ = consultar("SELECT * FROM cursos WHERE id = ?", (asignacion["curso_id"],), uno=True)
    ajustes = obtener_config()

    notas = consultar(
        "SELECT MAX(puntaje) AS mejor FROM intentos WHERE asignacion_id = ?"
        " GROUP BY modulo_id", (asignacion["id"],))
    final = round(sum(n["mejor"] for n in notas) / len(notas), 2) if notas else puntaje

    existente = consultar("SELECT * FROM certificados WHERE usuario_id = ? AND curso_id = ?",
                          (usuario["id"], curso_["id"]), uno=True)
    codigo = existente["codigo"] if existente else cert.nuevo_codigo()
    # La fecha del certificado es la de ejecución del curso, no la de generación del PDF.
    completado = consultar("SELECT completado_en FROM asignaciones WHERE id = ?",
                           (asignacion["id"],), uno=True)["completado_en"]
    ruta = cert.generar(usuario, curso_, final, codigo, ajustes, completado_en=completado)

    if existente:
        ejecutar("UPDATE certificados SET puntaje = ?, archivo = ?, emitido_en = ?,"
                 " origen = 'plataforma' WHERE id = ?",
                 (final, ruta.name, ahora(), existente["id"]))
    else:
        ejecutar("INSERT INTO certificados (codigo, usuario_id, curso_id, intento_id, puntaje,"
                 " archivo, origen, emitido_en) VALUES (?, ?, ?, NULL, ?, ?, 'plataforma', ?)",
                 (codigo, usuario["id"], curso_["id"], final, ruta.name, ahora()))

    asunto = f"Certificate of Training · {curso_['titulo']}"
    mailer.enviar(ajustes, usuario["email"], asunto,
                  mailer.cuerpo_certificado(usuario, curso_, final, codigo, ajustes), ruta)
    if ajustes.get("admin_email"):
        mailer.enviar(ajustes, ajustes["admin_email"], f"[Copy] {asunto}",
                      mailer.cuerpo_certificado(usuario, curso_, final, codigo, ajustes,
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
    if not ruta.exists():
        if certificado["origen"] == "importado":
            abort(404)
        datos_usuario = consultar("SELECT * FROM usuarios WHERE id = ?",
                                  (certificado["usuario_id"],), uno=True)
        curso_ = consultar("SELECT * FROM cursos WHERE id = ?", (certificado["curso_id"],), uno=True)
        ruta = cert.generar(datos_usuario, curso_, certificado["puntaje"],
                            certificado["codigo"], obtener_config(),
                            completado_en=certificado["emitido_en"])
    return send_file(ruta, mimetype="application/pdf", as_attachment=True,
                     download_name=f"{codigo}.pdf")


# --------------------------------------------------------------------------- account
@bp.route("/account")
@login_requerido
def cuenta():
    usuario = usuario_actual()
    certificados_usuario = consultar(
        "SELECT ce.*, c.titulo FROM certificados ce JOIN cursos c ON c.id = ce.curso_id"
        " WHERE ce.usuario_id = ? ORDER BY ce.emitido_en DESC", (usuario["id"],))
    return render_template("estudiante/cuenta.html", certificados=certificados_usuario)
