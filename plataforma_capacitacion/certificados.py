"""Training certificate (PDF) generation, in Alerion Aviation styling."""
import re
import secrets
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import config

# Brand colors — keep in sync with the --brand-* variables in static/css/app.css
NAVY = HexColor("#0e1a28")
GOLD = HexColor("#c6a15b")
GREY = HexColor("#6a7681")
LINE = HexColor("#d8dde2")

# Brand artwork used by the built-in design.
ASSETS = Path(__file__).resolve().parent / "static" / "img"
LOGO = ASSETS / "certificate_logo.png"
WATERMARK = ASSETS / "certificate_mark.png"

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def _long_date(moment: datetime) -> str:
    return f"{MONTHS[moment.month - 1]} {moment.day}, {moment.year}"


def _short_date(moment: datetime) -> str:
    """Formato mm/dd/yyyy, el que usa la compañía."""
    return moment.strftime("%m/%d/%Y")


def _a_fecha(valor, por_defecto=None):
    """Acepta datetime, texto ISO o None."""
    if isinstance(valor, datetime):
        return valor
    if valor:
        try:
            return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    return por_defecto or datetime.now()


def nuevo_codigo() -> str:
    return f"ALR-{datetime.now().year}-{secrets.token_hex(3).upper()}"


def _centered(c, y, text, font, size, color, width, tracking=0):
    c.saveState()
    c.setFillColor(color)
    if tracking:
        obj = c.beginText()
        obj.setFont(font, size)
        obj.setFillColor(color)
        obj.setCharSpace(tracking)
        span = c.stringWidth(text, font, size) + tracking * (len(text) - 1)
        obj.setTextOrigin((width - span) / 2, y)
        obj.textOut(text)
        c.drawText(obj)
    else:
        c.setFont(font, size)
        c.drawCentredString(width / 2, y, text)
    c.restoreState()


def _seal(c, x, y, radius, code):
    c.saveState()
    c.setStrokeColor(GOLD)
    c.setFillColor(Color(0.776, 0.631, 0.357, alpha=0.08))
    c.setLineWidth(1.6)
    c.circle(x, y, radius, stroke=1, fill=1)
    c.setLineWidth(0.7)
    c.circle(x, y, radius - 4, stroke=1, fill=0)

    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 6.2)
    c.drawCentredString(x, y + 15, "TRAINING")
    c.drawCentredString(x, y + 8, "COMPLETE")
    c.setFont("Helvetica", 5.5)
    c.drawCentredString(x, y - 21, code)

    c.setStrokeColor(GOLD)
    c.setLineWidth(2)
    path = c.beginPath()
    path.moveTo(x - 10, y - 6)
    path.lineTo(x - 3, y - 13)
    path.lineTo(x + 11, y + 1)
    c.drawPath(path, stroke=1, fill=0)
    c.restoreState()


def _letterhead(c, width, height, settings, code):
    drawn = False
    if LOGO.exists():
        try:
            image = ImageReader(str(LOGO))
            iw, ih = image.getSize()
            h = 13 * mm
            c.drawImage(image, 26 * mm, height - 38 * mm, width=h * iw / ih, height=h,
                        mask="auto")
            drawn = True
        except Exception:
            drawn = False

    if not drawn:
        c.setFillColor(GOLD)
        c.rect(26 * mm, height - 37 * mm, 2.4 * mm, 11 * mm, stroke=0, fill=1)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(32 * mm, height - 32 * mm, settings.get("org_name", "").upper())
        c.setFillColor(GREY)
        c.setFont("Helvetica", 8)
        c.drawString(32 * mm, height - 36.5 * mm, settings.get("org_area", ""))

    c.setFillColor(GREY)
    c.setFont("Helvetica", 9)
    c.drawRightString(width - 26 * mm, height - 32 * mm, f"Certificate {code}")


def generar_certificado(usuario, curso, puntaje, codigo, ajustes, emitido_en=None,
                        completado_en=None):
    """Builds the PDF and returns its path."""
    emitido_en = _a_fecha(emitido_en)
    completado_en = _a_fecha(completado_en, emitido_en)
    width, height = landscape(A4)
    path = config.CERT_DIR / f"{codigo}.pdf"

    c = canvas.Canvas(str(path), pagesize=landscape(A4))
    c.setTitle(f"Certificate of Training - {usuario['nombre']} - {curso['titulo']}")
    c.setAuthor(ajustes.get("org_name", ""))

    # Frame
    c.setStrokeColor(NAVY)
    c.setLineWidth(1.2)
    c.rect(14 * mm, 14 * mm, width - 28 * mm, height - 28 * mm, stroke=1, fill=0)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.rect(17 * mm, 17 * mm, width - 34 * mm, height - 34 * mm, stroke=1, fill=0)

    if WATERMARK.exists():
        try:
            wm = ImageReader(str(WATERMARK))
            iw, ih = wm.getSize()
            w = 150 * mm
            c.drawImage(wm, (width - w) / 2, height / 2 - (w * ih / iw) / 2 - 6 * mm,
                        width=w, height=w * ih / iw, mask="auto")
        except Exception:
            pass

    _letterhead(c, width, height, ajustes, codigo)

    _centered(c, height - 62 * mm, "CERTIFICATE OF TRAINING",
              "Helvetica-Bold", 22, NAVY, width, tracking=2.4)
    c.setStrokeColor(GOLD)
    c.setLineWidth(2)
    c.line(width / 2 - 22 * mm, height - 68 * mm, width / 2 + 22 * mm, height - 68 * mm)

    _centered(c, height - 82 * mm, "This is to certify that", "Helvetica", 11, GREY, width)
    _centered(c, height - 97 * mm, usuario["nombre"], "Helvetica-Bold", 24, NAVY, width)

    document = (usuario["documento"] or "").strip()
    id_line = f"ID {document}" if document else usuario["email"]
    _centered(c, height - 105 * mm, id_line, "Helvetica", 10.5, GREY, width)

    _centered(c, height - 119 * mm, "has successfully completed and passed the course",
              "Helvetica", 11, GREY, width)
    _centered(c, height - 132 * mm, curso["titulo"], "Helvetica-Bold", 16, NAVY, width)

    hours = curso["horas"] or 1
    hours_text = f"{hours:g} hour" + ("s" if float(hours) != 1 else "")
    detail = (f"Duration: {hours_text}   ·   Score: {puntaje:.0f} / 100   ·   "
              f"Completed: {_short_date(completado_en)}")
    _centered(c, height - 143 * mm, detail, "Helvetica", 10, GREY, width)

    # Signature block and seal
    y_sign = 42 * mm
    c.setStrokeColor(NAVY)
    c.setLineWidth(0.8)
    c.line(48 * mm, y_sign, 118 * mm, y_sign)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(48 * mm, y_sign - 6 * mm, ajustes.get("org_signer", ""))
    c.setFont("Helvetica", 9)
    c.setFillColor(GREY)
    c.drawString(48 * mm, y_sign - 11 * mm, ajustes.get("org_signer_title", ""))

    _seal(c, width - 68 * mm, y_sign + 2 * mm, 15 * mm, codigo)

    c.setFont("Helvetica", 7.5)
    c.setFillColor(GREY)
    validity = curso["vigencia_meses"] or 0
    footer = f"Issued electronically on {_short_date(emitido_en)}."
    if validity:
        footer += f" Valid for {validity} months from the date of issue."
    c.drawCentredString(width / 2, 24 * mm, footer)
    c.drawCentredString(width / 2, 20 * mm,
                        f"Verify this certificate with code {codigo} in the training platform.")

    c.showPage()
    c.save()
    return Path(path)


# ---------------------------------------------------------------------------
# Certificados a partir de una plantilla subida por el administrador
# ---------------------------------------------------------------------------
MESES_EN = MONTHS


def valores_marcadores(usuario, curso, puntaje, codigo, ajustes, emitido_en=None,
                       completado_en=None) -> dict:
    """Texto que reemplaza a cada marcador dentro de la plantilla."""
    emitido_en = _a_fecha(emitido_en)
    completado_en = _a_fecha(completado_en, emitido_en)
    documento = (usuario["documento"] or "").strip() or usuario["email"]
    horas = curso["horas"] or 1
    return {
        "{{NAME}}": usuario["nombre"],
        "{{ID}}": documento,
        "{{COURSE}}": curso["titulo"],
        "{{SCORE}}": f"{puntaje:.0f}",
        "{{DATE}}": _short_date(completado_en),          # cuándo se ejecutó el curso
        "{{DATE_LONG}}": _long_date(completado_en),
        "{{ISSUE_DATE}}": _short_date(emitido_en),       # cuándo se emitió el PDF
        "{{CODE}}": codigo,
        "{{HOURS}}": f"{horas:g}",
        "{{VALIDITY}}": str(curso["vigencia_meses"] or ""),
        "{{COMPANY}}": ajustes.get("org_name", ""),
        "{{AREA}}": ajustes.get("org_area", ""),
        "{{SIGNER}}": ajustes.get("org_signer", ""),
        "{{SIGNER_TITLE}}": ajustes.get("org_signer_title", ""),
    }


def _reemplazar_en_xml(xml: str, valores: dict) -> str:
    """Reemplaza los marcadores aunque Word los haya partido en varios trozos."""
    for marcador, valor in valores.items():
        limpio = escape(str(valor))
        if marcador in xml:
            xml = xml.replace(marcador, limpio)
            continue
        # {{NAME}} puede quedar como {{NA</w:t>...<w:t>ME}} dentro del XML
        tolerante = "".join(re.escape(ch) + r"(?:<[^>]*>)*" for ch in marcador)
        xml = re.sub(tolerante, limpio, xml)
    return xml


def rellenar_plantilla(plantilla: Path, valores: dict, destino_pdf: Path) -> Path:
    """Sustituye los marcadores en el .docx/.pptx y lo convierte a PDF."""
    if not plantilla.exists():
        raise FileNotFoundError("The certificate template is missing.")

    trabajo = Path(tempfile.mkdtemp(prefix="cert_"))
    try:
        copia = trabajo / f"relleno{plantilla.suffix}"
        with zipfile.ZipFile(plantilla) as origen, zipfile.ZipFile(copia, "w", zipfile.ZIP_DEFLATED) as salida:
            for elemento in origen.infolist():
                datos = origen.read(elemento.filename)
                if elemento.filename.endswith(".xml") and (
                        "document" in elemento.filename or "slide" in elemento.filename
                        or "content" in elemento.filename or "header" in elemento.filename
                        or "footer" in elemento.filename):
                    texto = _reemplazar_en_xml(datos.decode("utf-8", "ignore"), valores)
                    datos = texto.encode("utf-8")
                salida.writestr(elemento, datos)

        perfil = trabajo / "profile"
        subprocess.run(
            ["soffice", "--headless", "--norestore",
             f"-env:UserInstallation=file://{perfil}",
             "--convert-to", "pdf", "--outdir", str(trabajo), str(copia)],
            capture_output=True, text=True, timeout=300)
        generado = trabajo / "relleno.pdf"
        if not generado.exists():
            raise RuntimeError("LibreOffice could not convert the template to PDF.")
        destino_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(generado, destino_pdf)
        return destino_pdf
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)


def generar(usuario, curso, puntaje, codigo, ajustes, emitido_en=None,
            completado_en=None) -> Path:
    """Punto de entrada único: usa la plantilla si hay una, si no el diseño incluido."""
    if ajustes.get("cert_mode") == "template" and ajustes.get("cert_template"):
        plantilla = config.TEMPLATE_DIR / ajustes["cert_template"]
        if plantilla.exists():
            try:
                return rellenar_plantilla(
                    plantilla,
                    valores_marcadores(usuario, curso, puntaje, codigo, ajustes,
                                       emitido_en, completado_en),
                    config.CERT_DIR / f"{codigo}.pdf")
            except Exception as error:      # nunca dejamos a nadie sin certificado
                print(f"[certificado] plantilla falló, uso el diseño incluido: {error}")
    return generar_certificado(usuario, curso, puntaje, codigo, ajustes, emitido_en, completado_en)
