"""Envío de correo con el certificado adjunto.

Si hay servidor SMTP configurado, el correo sale por ahí. Si no lo hay, el
mensaje se guarda como archivo .eml en data/correos_salida para que puedas
abrirlo o reenviarlo. En ambos casos queda registro en la tabla `correos`.
"""
import base64
import html
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


class Mensaje:
    """Un correo en sus dos versiones: texto plano y HTML.

    Van las dos dentro del mismo envío. El cliente de correo muestra la que
    sabe dibujar, y quien lee en texto plano —o tiene el HTML bloqueado— no se
    queda sin el contenido. Los filtros de spam también miran la versión de
    texto: un correo que solo trae HTML puntúa peor.

    Las funciones `cuerpo_*` devuelven esto, pero `enviar` sigue aceptando una
    cadena suelta para que cualquier aviso corto se pueda mandar sin ceremonia.
    """
    __slots__ = ("texto", "html")

    def __init__(self, texto: str, html: str | None = None):
        self.texto = texto
        self.html = html

    def __str__(self):
        return self.texto


def _como_mensaje(cuerpo) -> Mensaje:
    return cuerpo if isinstance(cuerpo, Mensaje) else Mensaje(str(cuerpo))


class Adjunto:
    """Un archivo adjunto y el nombre con el que debe llegar.

    No siempre coinciden. El certificado se guarda en disco por su código,
    porque es el identificador y no puede cambiar, pero en la bandeja de
    entrada de alguien «ALR-2026-A1B2C3.pdf» no dice nada: tiene que llegar con
    el nombre de la persona delante.

    Imita a un Path en lo poco que las tres formas de envío necesitan —existir,
    leerse y tener nombre—, así que ninguna de ellas se entera de la
    diferencia, y quien adjunte un Path a secas sigue funcionando igual.
    """
    __slots__ = ("ruta", "name")

    def __init__(self, ruta, nombre: str | None = None):
        self.ruta = Path(ruta)
        self.name = nombre or self.ruta.name

    def exists(self) -> bool:
        return self.ruta.exists()

    def read_bytes(self) -> bytes:
        return self.ruta.read_bytes()


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


def _construir(remitente, destinatario, asunto, cuerpo: "Mensaje", adjunto: Path | None):
    mensaje = EmailMessage()
    mensaje["From"] = remitente
    mensaje["To"] = destinatario
    mensaje["Subject"] = asunto
    # Primero el texto y luego el HTML como alternativa: el orden importa, el
    # cliente toma la última versión que sepa mostrar.
    mensaje.set_content(cuerpo.texto)
    if cuerpo.html:
        mensaje.add_alternative(cuerpo.html, subtype="html")
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
        raise ErrorGraph(f"Could not reach the mail service: {error.reason}") from None


def _resumen_error(cuerpo):
    """Saca el mensaje legible de la respuesta de error.

    Cada servicio devuelve el error en un sitio distinto. Se prueban los que
    usan los proveedores soportados y, si ninguno encaja, se deja el cuerpo
    crudo recortado: vale más un texto feo en el registro que un "falló" a secas.
    """
    try:
        datos = json.loads(cuerpo)
    except Exception:
        return cuerpo[:300]
    if isinstance(datos.get("error"), dict):                 # Microsoft Graph
        return datos["error"].get("message", "")[:300]
    for clave in ("error_description", "message", "error"):  # Brevo, Resend
        valor = datos.get(clave)
        if isinstance(valor, str) and valor:
            return valor[:300]
    return str(datos)[:300]


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

    # Graph admite un solo cuerpo, no dos alternativas: si hay HTML se manda
    # ese, que es el que la persona va a ver.
    mensaje = {
        "message": {
            "subject": asunto,
            "body": ({"contentType": "HTML", "content": cuerpo.html} if cuerpo.html
                     else {"contentType": "Text", "content": cuerpo.texto}),
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
    cuerpo = _como_mensaje(cuerpo)
    if adjunto is not None and not isinstance(adjunto, Adjunto):
        adjunto = Adjunto(adjunto)
    modo = (ajustes.get("mail_mode") or "smtp").strip()
    if modo == "graph":
        _entregar_graph(ajustes, destinatario, asunto, cuerpo, adjunto)
    elif modo == "api":
        _entregar_api(ajustes, destinatario, asunto, cuerpo, adjunto)
    else:
        _entregar_smtp(ajustes, destinatario, asunto, cuerpo, adjunto)


# ---------------------------------------------------------------------------
# Envío por un servicio de correo transaccional (HTTPS, puerto 443)
#
# El mismo razonamiento que con Graph: el puerto 443 nunca está bloqueado. La
# diferencia es que esto no depende de la cuenta corporativa de Microsoft, así
# que se puede enviar desde el propio dominio de la plataforma sin pedirle
# nada al administrador de Office 365.
#
# Se soportan varios proveedores a propósito. Cada uno son diez líneas, y
# quedarse amarrado a uno solo es un riesgo que no hace falta correr: si mañana
# cambian el plan gratuito o la cuenta se suspende, se cambia el desplegable y
# la clave, sin tocar código.
# ---------------------------------------------------------------------------
PROVEEDORES_API = {
    "brevo": {
        "nombre": "Brevo",
        "url": "https://api.brevo.com/v3/smtp/email",
        "clave_en": "api-key",          # cabecera propia, no Authorization
    },
    "resend": {
        "nombre": "Resend",
        "url": "https://api.resend.com/emails",
        "clave_en": "Authorization",
        "prefijo": "Bearer ",
    },
}


def _cuerpo_api(proveedor, remitente, nombre_remitente, destinatario, asunto, cuerpo, adjunto):
    """El JSON que espera cada servicio. Es lo único que cambia entre ellos."""
    archivo = None
    if adjunto and adjunto.exists():
        archivo = (adjunto.name, base64.b64encode(adjunto.read_bytes()).decode())

    if proveedor == "brevo":
        mensaje = {
            "sender": {"email": remitente, "name": nombre_remitente or remitente},
            "to": [{"email": destinatario}],
            "subject": asunto,
            "textContent": cuerpo.texto,
        }
        if cuerpo.html:
            mensaje["htmlContent"] = cuerpo.html
        if archivo:
            mensaje["attachment"] = [{"name": archivo[0], "content": archivo[1]}]
        return mensaje

    # Resend
    mensaje = {
        "from": (f"{nombre_remitente} <{remitente}>" if nombre_remitente else remitente),
        "to": [destinatario],
        "subject": asunto,
        "text": cuerpo.texto,
    }
    if cuerpo.html:
        mensaje["html"] = cuerpo.html
    if archivo:
        mensaje["attachments"] = [{"filename": archivo[0], "content": archivo[1]}]
    return mensaje


def _entregar_api(ajustes, destinatario, asunto, cuerpo, adjunto):
    proveedor = (ajustes.get("api_proveedor") or "brevo").strip().lower()
    ficha = PROVEEDORES_API.get(proveedor)
    clave = (ajustes.get("api_clave") or "").strip()
    remitente = (ajustes.get("api_sender") or ajustes.get("smtp_sender")
                 or ajustes.get("admin_email") or "").strip()
    nombre_remitente = (ajustes.get("api_sender_nombre") or "").strip()

    if ficha is None:
        _registrar(destinatario, asunto, "error",
                   f"Unknown email provider: {proveedor}.", None)
        return
    if not clave or not remitente:
        _registrar(destinatario, asunto, "error",
                   f"{ficha['nombre']} is missing the API key or the from address.", None)
        return

    cabeceras = {"Content-Type": "application/json", "Accept": "application/json",
                 ficha["clave_en"]: ficha.get("prefijo", "") + clave}
    mensaje = _cuerpo_api(proveedor, remitente, nombre_remitente, destinatario,
                          asunto, cuerpo, adjunto)
    try:
        _pedir(ficha["url"], json.dumps(mensaje).encode("utf-8"), cabeceras, timeout=60)
        _registrar(destinatario, asunto, "enviado",
                   f"{ficha['nombre']}, as {remitente}", adjunto.name if adjunto else None)
    except ErrorGraph as error:
        _registrar(destinatario, asunto, "error", f"{ficha['nombre']}: {error}",
                   adjunto.name if adjunto else None)
    except Exception as error:
        _registrar(destinatario, asunto, "error",
                   f"{ficha['nombre']}: {type(error).__name__}: {error}",
                   adjunto.name if adjunto else None)


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


def enviar(ajustes: dict, destinatarios, asunto: str, cuerpo, adjunto: Path | None = None,
           en_segundo_plano: bool = True):
    """Envía el mismo mensaje a uno o varios destinatarios.

    `cuerpo` puede ser un Mensaje (texto + HTML) o una cadena suelta.
    """
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


# ===========================================================================
# Presentación HTML
#
# El correo sale en dos versiones dentro del mismo mensaje: texto plano y
# HTML. No es un lujo. El cliente de correo elige la que sabe mostrar, y la de
# texto es además la que leen los filtros de spam: un correo que solo trae
# HTML puntúa peor que uno que trae las dos.
#
# Las reglas del HTML para correo no son las de una página web. Outlook de
# escritorio dibuja con el motor de Word, que no entiende flexbox, ni grid, ni
# hojas de estilo externas, ni casi nada posterior a 2007. Por eso aquí todo
# es tabla y todo el estilo va escrito en el atributo style de cada etiqueta:
# es feo de leer, pero es lo único que se ve igual en Outlook, Gmail y el
# correo del teléfono.
#
# La otra regla: la mayoría de los clientes NO cargan las imágenes hasta que
# la persona lo autoriza. El diseño tiene que funcionar con el logo apagado,
# así que la cabecera es azul con texto alternativo blanco y el logo es un
# adorno encima, nunca el único portador del mensaje.
# ===========================================================================
TINTA = "#0e1a28"        # azul corporativo, el de la barra lateral
TINTA_HONDA = "#08111b"
ORO = "#c6a15b"          # acento de la marca
PAPEL = "#eef1f4"
TEXTO = "#1d2b3a"
TEXTO_SUAVE = "#5d6b7a"
LINEA = "#dde2e7"
ALERTA = "#97392c"
ALERTA_FONDO = "#fbeae6"

# Arial y Helvetica de primeras: la tipografía de la plataforma no existe en
# el correo, y una fuente que el cliente no tiene se sustituye por Times.
FUENTE = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,"
          "Arial,sans-serif")


def _esc(valor) -> str:
    """Todo lo que venga de la base de datos pasa por aquí.

    Los nombres de las personas y los títulos de los cursos los escribe gente,
    y un apóstrofo o un «&» sueltos rompen el HTML. Un `<` rompería más cosas.
    """
    return html.escape("" if valor is None else str(valor))


def _parrafo(texto, color=TEXTO, tamano="15px", margen="0 0 16px") -> str:
    return (f'<p style="margin:{margen};font-family:{FUENTE};font-size:{tamano};'
            f'line-height:1.6;color:{color};">{texto}</p>')


def _boton(url, texto) -> str:
    """Botón de llamada a la acción.

    El relleno va en la celda y no en el enlace: Outlook ignora el padding de
    los elementos en línea, y sin esto el botón se encoge hasta el tamaño de
    la letra. El condicional de arriba le da las esquinas redondeadas a
    Outlook, que tampoco entiende border-radius.
    """
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:26px 0 10px;"><tr>'
        f'<td align="center" bgcolor="{ORO}" style="background:{ORO};border-radius:8px;'
        'padding:15px 32px;">'
        f'<a href="{url}" style="font-family:{FUENTE};font-size:16px;font-weight:700;'
        f'color:{TINTA};text-decoration:none;letter-spacing:.01em;display:inline-block;">'
        f'{texto} &nbsp;&rarr;</a>'
        '</td></tr></table>'
    )


def _panel(filas, titulo=None) -> str:
    """Recuadro de datos: etiqueta a la izquierda, valor a la derecha."""
    dentro = ""
    if titulo:
        dentro += (f'<tr><td colspan="2" style="padding:0 0 12px;font-family:{FUENTE};'
                   f'font-size:11px;font-weight:700;letter-spacing:.09em;'
                   f'text-transform:uppercase;color:{TEXTO_SUAVE};">{titulo}</td></tr>')
    for etiqueta, valor in filas:
        dentro += (
            '<tr>'
            f'<td style="padding:6px 14px 6px 0;font-family:{FUENTE};font-size:13px;'
            f'color:{TEXTO_SUAVE};white-space:nowrap;vertical-align:top;">{etiqueta}</td>'
            f'<td style="padding:6px 0;font-family:{FUENTE};font-size:15px;'
            f'color:{TEXTO};font-weight:600;word-break:break-word;">{valor}</td>'
            '</tr>')
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:#f7f9fa;border:1px solid {LINEA};border-left:4px solid {ORO};'
        'border-radius:8px;margin:0 0 20px;"><tr><td style="padding:18px 20px;">'
        '<table role="presentation" class="apilar" cellpadding="0" cellspacing="0" '
        f'border="0">{dentro}</table>'
        '</td></tr></table>')


def _lista(elementos, color_borde=ORO) -> str:
    """Lista de cursos. Sin <ul>: Outlook le pone sangrías que no se controlan."""
    filas = ""
    for elemento in elementos:
        filas += (
            f'<tr><td style="padding:0 0 10px;"><table role="presentation" width="100%" '
            'cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td style="background:#f7f9fa;border-left:3px solid {color_borde};'
            f'border-radius:0 6px 6px 0;padding:12px 16px;font-family:{FUENTE};'
            f'font-size:15px;color:{TEXTO};">{elemento}</td>'
            '</tr></table></td></tr>')
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'border="0" style="margin:4px 0 18px;">{filas}</table>')


def _aviso(texto, color=TINTA, fondo="#f2f5f7") -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{fondo};border-radius:8px;margin:0 0 20px;">'
        f'<tr><td style="padding:14px 18px;font-family:{FUENTE};font-size:14px;'
        f'line-height:1.55;color:{color};">{texto}</td></tr></table>')


def _html(ajustes, titulo, saludo, contenido, preencabezado="") -> str:
    """El marco: cabecera con el logo, cuerpo blanco y pie."""
    organizacion = _esc(ajustes.get("org_name", ""))
    area = _esc(ajustes.get("org_area", ""))
    logo = enlace(ajustes, "/static/img/email-logo.png")
    sitio = enlace(ajustes, "/")

    # Texto de vista previa: lo que se lee en la lista del buzón, junto al
    # asunto, antes de abrir el correo. Si no se pone, el cliente toma la
    # primera frase que encuentre, que suele ser basura.
    vista = ""
    if preencabezado:
        # El relleno invisible del final evita que el cliente complete la vista
        # previa con las primeras palabras del cuerpo.
        relleno = "&#847;&zwnj;&nbsp;" * 30
        vista = (f'<div style="display:none;font-size:1px;color:{PAPEL};line-height:1px;'
                 'max-height:0;max-width:0;opacity:0;overflow:hidden;">'
                 f'{preencabezado}{relleno}</div>')

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
<title>{_esc(titulo)}</title>
<style>
/* Lo único que no va en línea. En el teléfono quedan unos 180 px para el valor
   de cada dato, y un correo electrónico no cabe: ahí la etiqueta pasa a estar
   encima del valor en lugar de al lado. Outlook de escritorio ignora esto,
   que es justo lo que se quiere: en el escritorio sobra el ancho. */
@media only screen and (max-width:480px) {{
  table.apilar td {{
    display:block !important; width:100% !important;
    padding:0 !important; white-space:normal !important;
  }}
  table.apilar td[colspan] {{ padding-bottom:10px !important; }}
  table.apilar tr + tr td:first-child {{ padding-top:12px !important; }}
}}
</style>
</head>
<body style="margin:0;padding:0;background:{PAPEL};-webkit-text-size-adjust:100%;">
{vista}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" \
style="background:{PAPEL};">
<tr><td align="center" style="padding:28px 12px 34px;">

<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" \
style="width:100%;max-width:600px;">

  <tr><td align="center" bgcolor="{TINTA}" style="background:{TINTA};padding:32px 24px 28px;\
border-radius:14px 14px 0 0;">
    <img src="{logo}" width="230" alt="{organizacion} Academy" \
style="display:block;width:230px;max-width:72%;height:auto;border:0;outline:none;\
font-family:{FUENTE};font-size:19px;font-weight:700;color:#ffffff;">
  </td></tr>
  <tr><td bgcolor="{ORO}" style="background:{ORO};height:4px;font-size:0;line-height:0;">\
&nbsp;</td></tr>

  <tr><td bgcolor="#ffffff" style="background:#ffffff;padding:32px 28px 30px;\
border-radius:0 0 14px 14px;">
    <h1 style="margin:0 0 6px;font-family:{FUENTE};font-size:22px;line-height:1.25;\
font-weight:700;color:{TINTA};letter-spacing:-.01em;">{_esc(titulo)}</h1>
    {f'<p style="margin:0 0 20px;font-family:{FUENTE};font-size:15px;color:{TEXTO_SUAVE};">{saludo}</p>' if saludo else ''}
    {contenido}
  </td></tr>

  <tr><td style="padding:22px 28px 0;">
    <p style="margin:0 0 6px;font-family:{FUENTE};font-size:13px;line-height:1.5;\
color:{TEXTO_SUAVE};">
      <strong style="color:{TEXTO};">{organizacion}</strong>{f' &middot; {area}' if area else ''}
    </p>
    <p style="margin:0;font-family:{FUENTE};font-size:12px;line-height:1.5;color:#8c98a4;">
      This message was sent automatically by the training platform. Please do not reply to it.<br>
      <a href="{sitio}" style="color:#8c98a4;text-decoration:underline;">{sitio}</a>
    </p>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""


# --------------------------------------------------------------------- textos
def cuerpo_bienvenida(usuario, clave_temporal, ajustes, reinicio=False):
    """Bienvenida con el usuario, la clave temporal y cómo cambiarla.

    `reinicio` cambia solo la primera frase: el mismo mensaje se usa cuando un
    administrador reinicia la contraseña, y decirle «se creó tu cuenta» a
    alguien que lleva meses usando la plataforma lo confunde.
    """
    portal = enlace(ajustes, "/login")
    cuenta = enlace(ajustes, "/account")
    organizacion = ajustes.get("org_name", "")
    nombre = usuario["nombre"].split()[0]

    if reinicio:
        apertura = ("An administrator has reset the password of your account in the "
                    f"{organizacion} training platform. Your new password is temporary, "
                    "so please change it the next time you sign in.")
        titulo = "Your password was reset"
    else:
        apertura = (f"An account has been created for you in the {organizacion} training "
                    "platform. This is where you will find the training assigned to you, "
                    "take the exams and download your certificates.")
        titulo = "Your account is ready"

    texto = (
        f"Hi {nombre},\n\n{apertura}\n\n"
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

    pasos = "".join(
        f'<tr><td style="padding:0 0 9px;font-family:{FUENTE};font-size:14px;'
        f'line-height:1.55;color:{TEXTO};">'
        f'<span style="display:inline-block;width:22px;color:{ORO};font-weight:700;">'
        f'{n}.</span>{t}</td></tr>'
        for n, t in enumerate([
            "Sign in with the details above.",
            'Click your name at the bottom left to open '
            f'<a href="{cuenta}" style="color:{TINTA};font-weight:700;">My account</a>.',
            'Under <strong>Change password</strong>, type the temporary password in '
            '<strong>Current password</strong>.',
            "Type your new password twice and save. Minimum 8 characters.",
        ], start=1))

    cuerpo_html = (
        _parrafo(_esc(apertura))
        + _panel([("Username", _esc(usuario["email"])),
                  ("Temporary password",
                   f'<span style="font-family:Consolas,Menlo,monospace;font-size:15px;'
                   f'background:#fff;border:1px solid {LINEA};border-radius:5px;'
                   f'padding:3px 8px;">{_esc(clave_temporal)}</span>')],
                 titulo="Your sign-in details")
        + _boton(portal, "Sign in to the platform")
        + f'<h2 style="margin:26px 0 12px;font-family:{FUENTE};font-size:16px;'
          f'font-weight:700;color:{TINTA};">Please change your password now</h2>'
        + _parrafo("This password is temporary and was set by an administrator, so change "
                   "it the first time you sign in.", margen="0 0 14px")
        + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
          f'border="0">{pasos}</table>'
        + _aviso("Keep your password to yourself: your training record and your "
                 "certificates are issued in your name.")
    )
    return Mensaje(texto, _html(ajustes, titulo, f"Hi {_esc(nombre)},", cuerpo_html,
                                preencabezado=f"Your sign-in details for the "
                                              f"{organizacion} training platform."))


def cuerpo_asignacion(usuario, titulos, fecha_limite, ajustes):
    """Aviso de curso asignado, con el enlace directo a la plataforma."""
    portal = enlace(ajustes, "/my-training")
    nombre = usuario["nombre"].split()[0]
    lista = "\n".join(f"  · {t}" for t in titulos)
    vence = (f"\nDue date: {fecha_limite}\n"
             "Please complete it on or before that date.\n" if fecha_limite else "")
    texto = (
        f"Hi {nombre},\n\n"
        f"The following training has been assigned to you:\n\n{lista}\n"
        f"{vence}\n"
        f"Start here: {portal}\n\n"
        "Sign in with your email and your password. If you have forgotten it, ask the training "
        "administrator to reset it for you.\n"
        + _firma(ajustes)
    )

    cuantos = len(titulos)
    cuerpo_html = (
        _parrafo("The following course has been assigned to you. It is waiting in your "
                 "account." if cuantos == 1 else
                 "The following courses have been assigned to you. They are waiting in "
                 "your account.")
        + _lista([f'<strong>{_esc(t)}</strong>' for t in titulos])
        + (_aviso(f'<strong>Due date: {_esc(fecha_limite)}</strong><br>'
                  'Please complete it on or before that date.',
                  color=TINTA, fondo="#fbf4e6") if fecha_limite else "")
        + _boton(portal, "Start your training")
        + _parrafo("Sign in with your email and your password. If you have forgotten it, "
                   "ask the training administrator to reset it for you.",
                   color=TEXTO_SUAVE, tamano="14px", margen="14px 0 0")
    )
    resumen = (titulos[0] if cuantos == 1
               else f"{cuantos} courses have been assigned to you.")
    return Mensaje(texto, _html(ajustes, "New training assigned", f"Hi {_esc(nombre)},",
                                cuerpo_html, preencabezado=_esc(resumen)))


def asunto_bienvenida(ajustes) -> str:
    nombre = (ajustes.get("org_short") or ajustes.get("org_name") or "").strip()
    return f"Welcome to {nombre} Academy" if nombre else "Welcome to the training platform"


def cuerpo_vencidos(usuario, filas, ajustes):
    """Recordatorio diario de la capacitación vencida de una persona."""
    portal = enlace(ajustes, "/my-training")
    nombre = usuario["nombre"].split()[0]
    lineas, tarjetas = [], []
    for fila in filas:
        dias = fila["dias"]
        atraso = "due today" if dias == 0 else (
            f"{dias} day{'s' if dias != 1 else ''} overdue")
        lineas.append(f"  · {fila['curso']} — due {fila['fecha_limite']} ({atraso}, "
                      f"{fila['avance']:.0f}% complete)")
        tarjetas.append(
            f'<strong>{_esc(fila["curso"])}</strong><br>'
            f'<span style="font-size:13px;color:{TEXTO_SUAVE};">'
            f'Due {_esc(fila["fecha_limite"])} &middot; '
            f'<span style="color:{ALERTA};font-weight:600;">{atraso}</span> &middot; '
            f'{fila["avance"]:.0f}% complete</span>')

    cuantos = len(filas)
    texto = (
        f"Hi {nombre},\n\n"
        f"You have {cuantos} training course{'s' if cuantos != 1 else ''} past the due date:\n\n"
        + "\n".join(lineas) + "\n\n"
        f"Please complete it as soon as you can: {portal}\n\n"
        "If you have already done this training outside the platform, or you think this is a "
        f"mistake, reply to {ajustes.get('admin_email', 'your training administrator')}.\n"
        + _firma(ajustes)
    )

    admin = ajustes.get("admin_email", "")
    contacto = (f'<a href="mailto:{_esc(admin)}" style="color:{TINTA};">{_esc(admin)}</a>'
                if admin else "your training administrator")
    cuerpo_html = (
        _parrafo(f'You have <strong>{cuantos} training course'
                 f'{"s" if cuantos != 1 else ""}</strong> past the due date.')
        + _lista(tarjetas, color_borde="#c0564a")
        + _boton(portal, "Complete it now")
        + _parrafo("If you have already done this training outside the platform, or you "
                   f"think this is a mistake, reply to {contacto}.",
                   color=TEXTO_SUAVE, tamano="14px", margen="14px 0 0")
    )
    return Mensaje(texto, _html(ajustes, "Training past its due date", f"Hi {_esc(nombre)},",
                                cuerpo_html,
                                preencabezado=f"{cuantos} course"
                                              f"{'s' if cuantos != 1 else ''} overdue."))


def cuerpo_certificado(usuario, curso, puntaje, codigo, ajustes, para_admin=False):
    organizacion = ajustes.get("org_name", "")
    if para_admin:
        encabezado = (f"{usuario['nombre']} ({usuario['email']}) has completed a training course.\n"
                      "A copy of the certificate is attached for the department records.")
        titulo = "Training completed"
        saludo = ""
        apertura = (f'<strong>{_esc(usuario["nombre"])}</strong> '
                    f'({_esc(usuario["email"])}) has completed a training course. '
                    'A copy of the certificate is attached for the department records.')
    else:
        encabezado = (f"Hi {usuario['nombre'].split()[0]},\n\n"
                      "You have completed your training. Your certificate is attached as a PDF.")
        titulo = "Your certificate is ready"
        saludo = f"Hi {_esc(usuario['nombre'].split()[0])},"
        apertura = ("You have completed your training. Your certificate is attached to this "
                    "message as a PDF.")

    texto = (
        f"{encabezado}\n\n"
        f"Course: {curso['titulo']}\n"
        f"Score: {puntaje:.0f}/100\n"
        f"Certificate code: {codigo}\n\n"
        f"{organizacion} · {ajustes.get('org_area', '')}\n"
        "This message was generated automatically by the training platform."
    )

    cuerpo_html = (
        _parrafo(apertura)
        + _panel([("Course", _esc(curso["titulo"])),
                  ("Score", f"{puntaje:.0f}/100"),
                  ("Certificate code",
                   f'<span style="font-family:Consolas,Menlo,monospace;">{_esc(codigo)}</span>')])
        + (_parrafo("Keep the PDF: it is the record of this training, and the code above "
                    "identifies it.", color=TEXTO_SUAVE, tamano="14px", margen="0")
           if not para_admin else "")
    )
    return Mensaje(texto, _html(ajustes, titulo, saludo, cuerpo_html,
                                preencabezado=_esc(curso["titulo"])))


def cuerpo_comentario(usuario, curso, estrellas, comentario, ajustes):
    """Aviso al instructor cuando alguien califica un curso."""
    enlace_cursos = enlace(ajustes, "/admin/courses")
    texto = (f"{usuario['nombre']} rated the course “{curso['titulo']}” "
             f"{estrellas} out of 5.\n\n"
             + (f"Their comment:\n\n{comentario}\n\n" if comentario else "No comment left.\n\n")
             + f"See all feedback: {enlace_cursos}\n")

    puntos = ("&#9733;" * int(estrellas)) + ("&#9734;" * (5 - int(estrellas)))
    cuerpo_html = (
        _parrafo(f'<strong>{_esc(usuario["nombre"])}</strong> rated '
                 f'&ldquo;{_esc(curso["titulo"])}&rdquo;')
        + f'<p style="margin:0 0 18px;font-family:{FUENTE};font-size:26px;'
          f'letter-spacing:3px;color:{ORO};">{puntos}'
          f'<span style="font-size:15px;color:{TEXTO_SUAVE};letter-spacing:0;"> '
          f'&nbsp;{estrellas} of 5</span></p>'
        + (_aviso(f'&ldquo;{_esc(comentario)}&rdquo;') if comentario
           else _parrafo("No comment left.", color=TEXTO_SUAVE, tamano="14px"))
        + _boton(enlace_cursos, "See all feedback")
    )
    return Mensaje(texto, _html(ajustes, "New course feedback", "", cuerpo_html,
                                preencabezado=f"{estrellas} of 5 &middot; "
                                              f"{_esc(curso['titulo'])}"))


def cuerpo_prueba(ajustes):
    """Mensaje del botón «Send a test» de Settings."""
    portal = enlace(ajustes, "/")
    modo = (ajustes.get("mail_mode") or "smtp").strip()
    como = {"api": PROVEEDORES_API.get(
                (ajustes.get("api_proveedor") or "brevo").lower(), {}).get("nombre", "an API"),
            "graph": "Microsoft Graph",
            "smtp": "SMTP"}.get(modo, modo)
    remitente = (ajustes.get("api_sender") if modo == "api" else
                 ajustes.get("graph_sender") if modo == "graph" else
                 ajustes.get("smtp_sender")) or ""

    texto = ("This is a test message from the training platform.\n\n"
             "If you are reading it, automated email is working: welcome messages, "
             "course assignments, overdue reminders and certificates will be delivered.\n\n"
             f"Sent through: {como}\n"
             f"From: {remitente}\n\n"
             f"Platform: {portal}\n"
             + _firma(ajustes))

    cuerpo_html = (
        _parrafo("If you are reading this, automated email is working. Welcome messages, "
                 "course assignments, overdue reminders and certificates will be delivered.")
        + _panel([("Sent through", _esc(como)), ("From", _esc(remitente))],
                 titulo="Delivery settings in use")
        + _boton(portal, "Open the platform")
    )
    return Mensaje(texto, _html(ajustes, "Email is working", "",
                                cuerpo_html,
                                preencabezado="Test message from the training platform."))
