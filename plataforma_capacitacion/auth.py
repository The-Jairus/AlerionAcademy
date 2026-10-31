"""Ingreso, cierre de sesión y control de acceso por rol."""
import secrets
import time
from functools import wraps

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from database import consultar

bp = Blueprint("auth", __name__)

_INTENTOS: dict[str, list] = {}      # email -> [conteo, momento_del_bloqueo]
MAX_INTENTOS = 8
BLOQUEO_SEGUNDOS = 300


def hash_password(clave: str) -> str:
    return generate_password_hash(clave, method="pbkdf2:sha256:260000")


def usuario_actual():
    if "usuario" not in g:
        g.usuario = None
        uid = session.get("usuario_id")
        if uid:
            g.usuario = consultar(
                "SELECT * FROM usuarios WHERE id = ? AND activo = 1", (uid,), uno=True
            )
            if g.usuario is None:
                session.clear()
    return g.usuario


def token_csrf() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def csrf_valido() -> bool:
    enviado = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    return bool(enviado) and secrets.compare_digest(enviado, session.get("csrf", ""))


def login_requerido(vista):
    @wraps(vista)
    def envoltura(*args, **kwargs):
        if usuario_actual() is None:
            return redirect(url_for("auth.login", siguiente=request.path))
        return vista(*args, **kwargs)
    return envoltura


def admin_requerido(vista):
    @wraps(vista)
    def envoltura(*args, **kwargs):
        usuario = usuario_actual()
        if usuario is None:
            return redirect(url_for("auth.login", siguiente=request.path))
        if usuario["rol"] != "admin":
            flash("That section is for administrators only.", "error")
            return redirect(url_for("estudiante.panel"))
        return vista(*args, **kwargs)
    return envoltura


def _bloqueado(email: str) -> int:
    registro = _INTENTOS.get(email)
    if not registro:
        return 0
    conteo, ultimo = registro
    if conteo >= MAX_INTENTOS:
        restante = int(BLOQUEO_SEGUNDOS - (time.time() - ultimo))
        if restante > 0:
            return restante
        _INTENTOS.pop(email, None)
    return 0


@bp.route("/login", methods=["GET", "POST"])
def login():
    if usuario_actual():
        return redirect(url_for("inicio"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        clave = request.form.get("password") or ""
        espera = _bloqueado(email)
        if espera:
            flash(f"Too many failed attempts. Try again in {espera // 60 + 1} minutes.",
                  "error")
            return render_template("login.html", email=email), 429

        usuario = consultar("SELECT * FROM usuarios WHERE email = ?", (email,), uno=True)
        if usuario and usuario["activo"] and check_password_hash(usuario["password_hash"], clave):
            _INTENTOS.pop(email, None)
            session.clear()
            session["usuario_id"] = usuario["id"]
            session.permanent = True
            siguiente = request.args.get("siguiente") or request.form.get("siguiente") or ""
            if siguiente.startswith("/") and not siguiente.startswith("//"):
                return redirect(siguiente)
            return redirect(url_for("inicio"))

        conteo, _ = _INTENTOS.get(email, [0, 0])
        _INTENTOS[email] = [conteo + 1, time.time()]
        if usuario and not usuario["activo"]:
            flash("Your account is inactive. Please contact the administrator.", "error")
        else:
            flash("Incorrect email or password.", "error")
        return render_template("login.html", email=email), 401

    return render_template("login.html", email="")


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    flash("You have been signed out.", "ok")
    return redirect(url_for("auth.login"))


@bp.route("/change-password", methods=["POST"])
@login_requerido
def cambiar_clave():
    from database import ejecutar
    usuario = usuario_actual()
    actual = request.form.get("actual") or ""
    nueva = request.form.get("nueva") or ""
    if not check_password_hash(usuario["password_hash"], actual):
        flash("The current password does not match.", "error")
    elif len(nueva) < 8:
        flash("The new password must be at least 8 characters long.", "error")
    else:
        ejecutar("UPDATE usuarios SET password_hash = ? WHERE id = ?",
                 (hash_password(nueva), usuario["id"]))
        flash("Password updated.", "ok")
    return redirect(request.referrer or url_for("inicio"))
