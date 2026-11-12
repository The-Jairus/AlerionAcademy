"""Envío de correo con el certificado adjunto.

Si hay servidor SMTP configurado, el correo sale por ahí. Si no lo hay, el
mensaje se guarda como archivo .eml en data/correos_salida para que puedas
abrirlo o reenviarlo. En ambos casos queda registro en la tabla `correos`.
"""
import base64
import json
import smtplib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import config
import database


def _registrar(destinatario, asunto, estado, detalle, adjunto):
    con = database.conectar()
    con.execute(
        "INSERT INTO correos (destinatario, asunto, estado, detalle, adjunto, creado_en) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (destinatario, asunto, estado, detalle, adjunto,
         datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
    )
    con.commit()
    con.close()


def _construir(remitente, destinatario, asunto, cuerpo, adjunto: Path | None):
    mensaje = EmailMessage()
    mensaje["From"] = remitente
    mensaje["To"] = destinatario
    mensaje["Subject"] = asunto
    mensaje.set_content(cuerpo)
    if adjunto and adjunto.exists():
        mensaje.add_attachment(
            adjunto.read_bytes(), maintype="application", subtype="pdf",
            filename=adjunto.name,
        )
    return mensaje


# ---------------------------------------------------------------------------
# Envío por Microsoft Graph (HTTPS, puerto 443)
#
# Muchos proveedores de servidores bloquean los puertos de SMTP de salida.
# Graph envía por HTTPS, que nunca se bloquea, y además no usa autenticación
# básica: Microsoft la está retirando. Solo biblioteca estándar, para no
# agregarle dependencias al despliegue.
# ---------------------------------------------------------------------------
GRAPH_LOGIN = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
GRAPH_ENVIO = "https://graph.microsoft.com/v1.0/users/{buzon}/sendMail"

_token = {"valor": None, "vence": 0.0, "clave": None}
_candado = threading.Lock()


class ErrorGraph(Exception):
    """Microsoft rechazó la petición."""


def _pedir(url, datos, cabeceras, timeout=30):
    """POST sencillo que devuelve (código, texto)."""
    peticion = urllib.request.Request(url, data=datos, headers=cabeceras, method="POST")
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            return respuesta.status, respuesta.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", "ignore")
        raise ErrorGraph(f"HTTP {error.code}: {_resumen_error(detalle)}") from None
    except urllib.error.URLError as error:
        raise ErrorGraph(f"Could not reach Microsoft: {error.reason}") from None


def _resumen_error(cuerpo):
    """Saca el mensaje legible de la respuesta de error de Microsoft."""
    try:
        datos = json.loads(cuerpo)
    except Exception:
        return cuerpo[:300]
    if isinstance(datos.get("error"), dict):
        return datos["error"].get("message", "")[:300]
    return (datos.get("error_description") or str(datos))[:300]


def _token_graph(ajustes):
    """Token de aplicación, reutilizado hasta poco antes de que expire."""
    tenant = (ajustes.get("graph_tenant_id") or "").strip()
    cliente = (ajustes.get("graph_client_id") or "").strip()
    secreto = ajustes.get("graph_client_secret") or ""
    if not (tenant and cliente and secreto):
        raise ErrorGraph("Microsoft Graph is missing the tenant ID, the client ID or the "
                         "client secret.")

    clave = (tenant, cliente, secreto)
    with _candado:
        if _token["valor"] and _token["clave"] == clave and time.time() < _token["vence"]:
            return _token["valor"]

        cuerpo = urllib.parse.urlencode({
            "client_id": cliente,
            "client_secret": secreto,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }).encode()
        _, texto = _pedir(GRAPH_LOGIN.format(tenant=urllib.parse.quote(tenant)), cuerpo,
                          {"Content-Type": "application/x-www-form-urlencoded"})
        datos = json.loads(texto)
        _token["valor"] = datos["access_token"]
        # Se renueva 5 minutos antes de tiempo, por si el reloj va corrido.
        _token["vence"] = time.time() + int(datos.get("expires_in", 3600)) - 300
        _token["clave"] = clave
        return _token["valor"]


def _entregar_graph(ajustes, destinatario, asunto, cuerpo, adjunto):
    buzon = (ajustes.get("graph_sender") or ajustes.get("smtp_sender")
             or ajustes.get("admin_email") or "").strip()
    if not buzon:
        _registrar(destinatario, asunto, "error",
                   "No sender mailbox configured for Microsoft Graph.", None)
        return

    mensaje = {
        "message": {
            "subject": asunto,
            "body": {"contentType": "Text", "content": cuerpo},
            "toRecipients": [{"emailAddress": {"address": destinatario}}],
        },
        "saveToSentItems": True,
    }
    if adjunto and adjunto.exists():
        mensaje["message"]["attachments"] = [{
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": adjunto.name,
            "contentType": "application/pdf",
            "contentBytes": base64.b64encode(adjunto.read_bytes()).decode(),
        }]

    try:
        token = _token_graph(ajustes)
        _pedir(GRAPH_ENVIO.format(buzon=urllib.parse.quote(buzon)),
               json.dumps(mensaje).encode("utf-8"),
               {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
               timeout=60)
        _registrar(destinatario, asunto, "enviado", f"Microsoft Graph, as {buzon}",
                   adjunto.name if adjunto else None)
    except ErrorGraph as error:
        _registrar(destinatario, asunto, "error", str(error),
                   adjunto.name if adjunto else None)
    except Exception as error:
        _registrar(destinatario, asunto, "error", f"{type(error).__name__}: {error}",
                   adjunto.name if adjunto else None)


def _entregar(ajustes, destinatario, asunto, cuerpo, adjunto):
    if (ajustes.get("mail_mode") or "smtp").strip() == "graph":
        _entregar_graph(ajustes, destinatario, asunto, cuerpo, adjunto)
        return
    _entregar_smtp(ajustes, destinatario, asunto, cuerpo, adjunto)


def _entregar_smtp(ajustes, destinatario, asunto, cuerpo, adjunto):
    remitente = ajustes.get("smtp_sender") or ajustes.get("admin_email") or "no-reply@localhost"
    mensaje = _construir(remitente, destinatario, asunto, cuerpo, adjunto)
    host = (ajustes.get("smtp_host") or "").strip()

    if not host:
        nombre = f"{datetime.now():%Y%m%d-%H%M%S}-{destinatario.replace('@', '_at_')}.eml"
        (config.OUTBOX_DIR / nombre).write_bytes(bytes(mensaje))
        _registrar(destinatario, asunto, "bandeja_local",
                   "No SMTP server configured: the message was saved to data/correos_salida.",
                   nombre)
        return

    try:
        puerto = int(ajustes.get("smtp_port") or 587)
        usa_tls = str(ajustes.get("smtp_tls", "1")) == "1"
        if puerto == 465:
            servidor = smtplib.SMTP_SSL(host, puerto, timeout=30)
        else:
            servidor = smtplib.SMTP(host, puerto, timeout=30)
        with servidor:
            servidor.ehlo()
            if usa_tls and puerto != 465:
                servidor.starttls()
                servidor.ehlo()
            if ajustes.get("smtp_user"):
                servidor.login(ajustes["smtp_user"], ajustes.get("smtp_password", ""))
            servidor.send_message(mensaje)
        _registrar(destinatario, asunto, "enviado", f"Delivered through {host}",
                   adjunto.name if adjunto else None)
    except Exception as error:
        _registrar(destinatario, asunto, "error", f"{type(error).__name__}: {error}",
                   adjunto.name if adjunto else None)


def enviar(ajustes: dict, destinatarios, asunto: str, cuerpo: str, adjunto: Path | None = None,
           en_segundo_plano: bool = True):
    """Envía el mismo mensaje a uno o varios destinatarios."""
    lista = [d.strip() for d in
             (destinatarios if isinstance(destinatarios, (list, tuple)) else [destinatarios])
             if d and d.strip()]
    if not lista:
        return

    def tarea():
        for destinatario in lista:
            _entregar(ajustes, destinatario, asunto, cuerpo, adjunto)

    if en_segundo_plano:
        threading.Thread(target=tarea, daemon=True).start()
    else:
        tarea()


def activo(ajustes: dict, clave: str) -> bool:
    """¿Está encendido este correo automático? Por defecto, sí."""
    return str(ajustes.get(clave, "1")).strip() not in ("0", "", "no", "false")


def enlace(ajustes: dict, ruta: str = "/") -> str:
    """URL absoluta de la plataforma. Los correos no pueden usar rutas relativas."""
    base = (ajustes.get("site_url") or "").strip().rstrip("/")
    if not base:
        base = "https://alerionacademy.com"
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    return base + ("/" + ruta.lstrip("/") if ruta and ruta != "/" else "")


def _firma(ajustes: dict) -> str:
    return (f"\n{ajustes.get('org_name', '')} · {ajustes.get('org_area', '')}\n"
            "This message was generated automatically by the training platform. "
            "Please do not reply to it.")


def cuerpo_bienvenida(usuario, clave_temporal, ajustes):
    """Bienvenida con el usuario, la clave temporal y cómo cambiarla."""
    portal = enlace(ajustes, "/login")
    cuenta = enlace(ajustes, "/account")
    return (
        f"Hi {usuario['nombre'].split()[0]},\n\n"
        f"An account has been created for you in the {ajustes.get('org_name', '')} "
        "training platform. This is where you will find the training assigned to you, "
        "take the exams and download your certificates.\n\n"
        "YOUR SIGN-IN DETAILS\n"
        f"  Platform:           {portal}\n"
        f"  Username (email):   {usuario['email']}\n"
        f"  Temporary password: {clave_temporal}\n\n"
        "PLEASE CHANGE YOUR PASSWORD NOW\n"
        "This password is temporary and was set by an administrator, so change it the first "
        "time you sign in:\n"
        f"  1. Open {portal} and sign in with the details above.\n"
        "  2. Click your name at the bottom left of the screen to open “My account”.\n"
        f"     (Or go straight to {cuenta}.)\n"
        "  3. Under “Change password”, type the temporary password above in "
        "“Current password”.\n"
        "  4. Type your new password twice and save. It must be at least 8 characters long.\n\n"
        "Keep your password to yourself: your training record and your certificates are issued "
        "in your name.\n"
        + _firma(ajustes)
    )


def cuerpo_asignacion(usuario, titulos, fecha_limite, ajustes):
    """Aviso de curso asignado, con el enlace directo a la plataforma."""
    portal = enlace(ajustes, "/my-training")
    lista = "\n".join(f"  · {t}" for t in titulos)
    vence = (f"\nDue date: {fecha_limite}\n"
             "Please complete it on or before that date.\n" if fecha_limite else "")
    return (
        f"Hi {usuario['nombre'].split()[0]},\n\n"
        f"The following training has been assigned to you:\n\n{lista}\n"
        f"{vence}\n"
        f"Start here: {portal}\n\n"
        "Sign in with your email and your password. If you have forgotten it, ask the training "
        "administrator to reset it for you.\n"
        + _firma(ajustes)
    )


def asunto_bienvenida(ajustes) -> str:
    nombre = (ajustes.get("org_short") or ajustes.get("org_name") or "").strip()
    return f"Welcome to {nombre} Academy" if nombre else "Welcome to the training platform"


def cuerpo_vencidos(usuario, filas, ajustes):
    """Recordatorio diario de la capacitación vencida de una persona."""
    portal = enlace(ajustes, "/my-training")
    lineas = []
    for fila in filas:
        dias = fila["dias"]
        atraso = "due today" if dias == 0 else (
            f"{dias} day{'s' if dias != 1 else ''} overdue")
        lineas.append(f"  · {fila['curso']} — due {fila['fecha_limite']} ({atraso}, "
                      f"{fila['avance']:.0f}% complete)")
    cuantos = len(filas)
    return (
        f"Hi {usuario['nombre'].split()[0]},\n\n"
        f"You have {cuantos} training course{'s' if cuantos != 1 else ''} past the due date:\n\n"
        + "\n".join(lineas) + "\n\n"
        f"Please complete it as soon as you can: {portal}\n\n"
        "If you have already done this training outside the platform, or you think this is a "
        f"mistake, reply to {ajustes.get('admin_email', 'your training administrator')}.\n"
        + _firma(ajustes)
    )


def cuerpo_certificado(usuario, curso, puntaje, codigo, ajustes, para_admin=False):
    organizacion = ajustes.get("org_name", "")
    if para_admin:
        encabezado = (f"{usuario['nombre']} ({usuario['email']}) has completed a training course.\n"
                      "A copy of the certificate is attached for the department records.")
    else:
        encabezado = (f"Hi {usuario['nombre'].split()[0]},\n\n"
                      "You have completed your training. Your certificate is attached as a PDF.")
    return (
        f"{encabezado}\n\n"
        f"Course: {curso['titulo']}\n"
        f"Score: {puntaje:.0f}/100\n"
        f"Certificate code: {codigo}\n\n"
        f"{organizacion} · {ajustes.get('org_area', '')}\n"
        "This message was generated automatically by the training platform."
    )
