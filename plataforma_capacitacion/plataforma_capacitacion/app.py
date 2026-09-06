"""Plataforma de capacitación: aplicación Flask."""
import os
from datetime import datetime, timedelta

from flask import (Flask, jsonify, redirect, render_template, request, session,
                   url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

import administrador
import auth
import config
import database
import estudiante

METODOS_SEGUROS = {"GET", "HEAD", "OPTIONS"}


def crear_app():
    app = Flask(__name__)
    # Detrás de Nginx/Caddy: respeta el dominio y el https reales del visitante.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.update(
        SECRET_KEY=config.SECRET_KEY,
        MAX_CONTENT_LENGTH=config.MAX_CONTENT_LENGTH,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Con HTTPS activo (producción), la cookie solo viaja cifrada.
        SESSION_COOKIE_SECURE=os.environ.get("HTTPS", "0") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=10),
        TEMPLATES_AUTO_RELOAD=True,
    )

    database.inicializar()
    app.teardown_appcontext(database.cerrar_db)

    app.register_blueprint(auth.bp)
    app.register_blueprint(estudiante.bp)
    app.register_blueprint(administrador.bp)

    @app.before_request
    def proteger_csrf():
        if request.method in METODOS_SEGUROS:
            return None
        if not auth.csrf_valido():
            if request.is_json:
                return jsonify(error="Sesión expirada. Vuelve a ingresar."), 400
            return render_template("error.html", codigo=400,
                                   titulo="No pudimos verificar el formulario",
                                   detalle="Tu sesión expiró o el formulario se envió desde otra "
                                           "pestaña. Vuelve a ingresar e inténtalo de nuevo."), 400
        return None

    @app.context_processor
    def variables_globales():
        return {
            "usuario": auth.usuario_actual(),
            "csrf_token": auth.token_csrf,
            "ajustes": database.obtener_config() if session.get("usuario_id") else {},
            "año": datetime.now().year,
        }

    @app.route("/")
    def inicio():
        usuario = auth.usuario_actual()
        if usuario is None:
            return redirect(url_for("auth.login"))
        if usuario["rol"] == "admin":
            return redirect(url_for("admin.panel"))
        return redirect(url_for("estudiante.panel"))

    # ---------------------------------------------------------------- filtros
    @app.template_filter("fecha")
    def _fecha(valor):
        if not valor:
            return "—"
        try:
            return datetime.fromisoformat(str(valor)).strftime("%d/%m/%Y")
        except ValueError:
            return str(valor)[:10]

    @app.template_filter("fecha_hora")
    def _fecha_hora(valor):
        if not valor:
            return "—"
        try:
            return datetime.fromisoformat(str(valor)).strftime("%d/%m/%Y %H:%M")
        except ValueError:
            return str(valor)

    @app.template_filter("duracion")
    def _duracion(segundos):
        segundos = int(float(segundos or 0))
        if segundos <= 0:
            return "—"
        horas, resto = divmod(segundos, 3600)
        minutos, seg = divmod(resto, 60)
        if horas:
            return f"{horas} h {minutos:02d} min"
        if minutos:
            return f"{minutos} min {seg:02d} s"
        return f"{seg} s"

    # ---------------------------------------------------------------- errores
    @app.errorhandler(403)
    def _403(_e):
        return render_template("error.html", codigo=403, titulo="Sin permiso",
                               detalle="Tu usuario no tiene acceso a este contenido."), 403

    @app.errorhandler(404)
    def _404(_e):
        return render_template("error.html", codigo=404, titulo="Página no encontrada",
                               detalle="Revisa el enlace o vuelve al inicio."), 404

    @app.errorhandler(413)
    def _413(_e):
        limite = config.MAX_CONTENT_LENGTH // (1024 * 1024)
        return render_template("error.html", codigo=413, titulo="Archivo demasiado grande",
                               detalle=f"El material no puede superar {limite} MB. "
                                       "Comprime el video o divídelo en dos cursos."), 413

    @app.errorhandler(500)
    def _500(_e):
        return render_template("error.html", codigo=500, titulo="Error del servidor",
                               detalle="Ocurrió un problema procesando la solicitud. "
                                       "Revisa la consola del servidor."), 500

    return app


app = crear_app()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("DEBUG") == "1", threaded=True)
