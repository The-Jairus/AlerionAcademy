"""Envío de correo con el certificado adjunto.

Si hay servidor SMTP configurado, el correo sale por ahí. Si no lo hay, el
mensaje se guarda como archivo .eml en data/correos_salida para que puedas
abrirlo o reenviarlo. En ambos casos queda registro en la tabla `correos`.
"""
import smtplib
import threading
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


def _entregar(ajustes, destinatario, asunto, cuerpo, adjunto):
    remitente = ajustes.get("smtp_remitente") or ajustes.get("correo_administrador") or "no-reply@localhost"
    mensaje = _construir(remitente, destinatario, asunto, cuerpo, adjunto)
    host = (ajustes.get("smtp_host") or "").strip()

    if not host:
        nombre = f"{datetime.now():%Y%m%d-%H%M%S}-{destinatario.replace('@', '_at_')}.eml"
        (config.OUTBOX_DIR / nombre).write_bytes(bytes(mensaje))
        _registrar(destinatario, asunto, "bandeja_local",
                   "Sin servidor SMTP configurado: el mensaje quedó en data/correos_salida.",
                   nombre)
        return

    try:
        puerto = int(ajustes.get("smtp_puerto") or 587)
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
            if ajustes.get("smtp_usuario"):
                servidor.login(ajustes["smtp_usuario"], ajustes.get("smtp_password", ""))
            servidor.send_message(mensaje)
        _registrar(destinatario, asunto, "enviado", f"Entregado vía {host}",
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


def cuerpo_certificado(usuario, curso, puntaje, codigo, ajustes, para_admin=False):
    organizacion = ajustes.get("org_nombre", "")
    if para_admin:
        encabezado = (f"{usuario['nombre']} ({usuario['email']}) aprobó una capacitación.\n"
                      "Se adjunta la copia del certificado para el archivo del área.")
    else:
        encabezado = (f"Hola {usuario['nombre'].split()[0]},\n\n"
                      "Completaste tu capacitación. Adjuntamos tu certificado en PDF.")
    return (
        f"{encabezado}\n\n"
        f"Curso: {curso['titulo']}\n"
        f"Calificación: {puntaje:.0f}/100\n"
        f"Código del certificado: {codigo}\n\n"
        f"{organizacion} · {ajustes.get('org_area', '')}\n"
        "Este mensaje se generó automáticamente desde la plataforma de capacitación."
    )
