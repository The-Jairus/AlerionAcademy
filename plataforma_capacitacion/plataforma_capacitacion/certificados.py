"""Training certificate (PDF) generation, in Alerion Aviation styling."""
import secrets
from datetime import datetime
from pathlib import Path

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

# Optional artwork: drop a PNG here and it is printed on every certificate.
LOGO = Path(__file__).resolve().parent / "static" / "img" / "certificate_logo.png"

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def _long_date(moment: datetime) -> str:
    return f"{MONTHS[moment.month - 1]} {moment.day}, {moment.year}"


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


def generar_certificado(usuario, curso, puntaje, codigo, ajustes, emitido_en=None):
    """Builds the PDF and returns its path."""
    emitido_en = emitido_en or datetime.now()
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
              f"Date: {_long_date(emitido_en)}")
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
    footer = f"Issued electronically on {_long_date(emitido_en)}."
    if validity:
        footer += f" Valid for {validity} months from the date of issue."
    c.drawCentredString(width / 2, 24 * mm, footer)
    c.drawCentredString(width / 2, 20 * mm,
                        f"Verify this certificate with code {codigo} in the training platform.")

    c.showPage()
    c.save()
    return Path(path)
