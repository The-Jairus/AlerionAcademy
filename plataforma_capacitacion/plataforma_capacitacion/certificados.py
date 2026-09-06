"""Emisión del certificado de capacitación en PDF."""
import secrets
from datetime import datetime
from pathlib import Path

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

import config

TINTA = HexColor("#16212B")
VERDE = HexColor("#14664F")
GRIS = HexColor("#6A7681")
LINEA = HexColor("#C9D2CC")

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_larga(momento: datetime) -> str:
    return f"{momento.day} de {MESES[momento.month - 1]} de {momento.year}"


def nuevo_codigo() -> str:
    año = datetime.now().year
    return f"CERT-{año}-{secrets.token_hex(3).upper()}"


def _centrado(lienzo, y, texto, fuente, tamaño, color, ancho, tracking=0):
    lienzo.saveState()
    lienzo.setFont(fuente, tamaño, leading=tamaño * 1.2)
    lienzo.setFillColor(color)
    if tracking:
        objeto = lienzo.beginText()
        objeto.setFont(fuente, tamaño)
        objeto.setFillColor(color)
        objeto.setCharSpace(tracking)
        desplazamiento = lienzo.stringWidth(texto, fuente, tamaño) + tracking * (len(texto) - 1)
        objeto.setTextOrigin((ancho - desplazamiento) / 2, y)
        objeto.textOut(texto)
        lienzo.drawText(objeto)
    else:
        lienzo.drawCentredString(ancho / 2, y, texto)
    lienzo.restoreState()


def _sello(lienzo, x, y, radio, codigo):
    lienzo.saveState()
    lienzo.setStrokeColor(VERDE)
    lienzo.setFillColor(Color(0.078, 0.4, 0.31, alpha=0.06))
    lienzo.setLineWidth(1.6)
    lienzo.circle(x, y, radio, stroke=1, fill=1)
    lienzo.setLineWidth(0.7)
    lienzo.circle(x, y, radio - 4, stroke=1, fill=0)

    lienzo.setFillColor(VERDE)
    lienzo.setFont("Helvetica-Bold", 6.2)
    lienzo.drawCentredString(x, y + 15, "FORMACIÓN")
    lienzo.drawCentredString(x, y + 8, "APROBADA")
    lienzo.setFont("Helvetica", 5.5)
    lienzo.drawCentredString(x, y - 21, codigo)

    lienzo.setStrokeColor(VERDE)
    lienzo.setLineWidth(2)
    ruta = lienzo.beginPath()
    ruta.moveTo(x - 10, y - 6)
    ruta.lineTo(x - 3, y - 13)
    ruta.lineTo(x + 11, y + 1)
    lienzo.drawPath(ruta, stroke=1, fill=0)
    lienzo.restoreState()


def generar_certificado(usuario, curso, puntaje, codigo, ajustes, emitido_en=None):
    """Crea el PDF y devuelve la ruta del archivo."""
    emitido_en = emitido_en or datetime.now()
    ancho, alto = landscape(A4)
    ruta = config.CERT_DIR / f"{codigo}.pdf"

    lienzo = canvas.Canvas(str(ruta), pagesize=landscape(A4))
    lienzo.setTitle(f"Certificado {curso['titulo']} - {usuario['nombre']}")
    lienzo.setAuthor(ajustes.get("org_nombre", ""))

    # Marco
    lienzo.setStrokeColor(TINTA)
    lienzo.setLineWidth(1.2)
    lienzo.rect(14 * mm, 14 * mm, ancho - 28 * mm, alto - 28 * mm, stroke=1, fill=0)
    lienzo.setStrokeColor(LINEA)
    lienzo.setLineWidth(0.6)
    lienzo.rect(17 * mm, 17 * mm, ancho - 34 * mm, alto - 34 * mm, stroke=1, fill=0)

    # Encabezado
    lienzo.setFillColor(VERDE)
    lienzo.rect(26 * mm, alto - 38 * mm, 9 * mm, 9 * mm, stroke=0, fill=1)
    lienzo.setFillColor(TINTA)
    lienzo.setFont("Helvetica-Bold", 12)
    lienzo.drawString(39 * mm, alto - 33 * mm, ajustes.get("org_nombre", ""))
    lienzo.setFont("Helvetica", 9)
    lienzo.setFillColor(GRIS)
    lienzo.drawString(39 * mm, alto - 37.5 * mm, ajustes.get("org_area", ""))
    lienzo.drawRightString(ancho - 26 * mm, alto - 33 * mm, f"Certificado {codigo}")

    _centrado(lienzo, alto - 62 * mm, "Certificado de capacitación",
              "Helvetica-Bold", 26, TINTA, ancho, tracking=0.6)
    lienzo.setStrokeColor(VERDE)
    lienzo.setLineWidth(2)
    lienzo.line(ancho / 2 - 22 * mm, alto - 68 * mm, ancho / 2 + 22 * mm, alto - 68 * mm)

    _centrado(lienzo, alto - 82 * mm, "Se certifica que", "Helvetica", 11, GRIS, ancho)
    _centrado(lienzo, alto - 97 * mm, usuario["nombre"], "Helvetica-Bold", 24, TINTA, ancho)

    documento = (usuario["documento"] or "").strip()
    linea_id = f"documento de identidad {documento}" if documento else usuario["email"]
    _centrado(lienzo, alto - 105 * mm, f"identificado(a) con {linea_id}",
              "Helvetica", 10.5, GRIS, ancho)

    _centrado(lienzo, alto - 119 * mm, "culminó y aprobó satisfactoriamente el curso",
              "Helvetica", 11, GRIS, ancho)
    _centrado(lienzo, alto - 132 * mm, curso["titulo"], "Helvetica-Bold", 16, VERDE, ancho)

    horas = curso["horas"] or 1
    horas_txt = f"{horas:g} hora" + ("s" if float(horas) != 1 else "")
    detalle = (f"Intensidad: {horas_txt}  ·  Calificación obtenida: {puntaje:.0f} / 100  ·  "
               f"Fecha: {_fecha_larga(emitido_en)}")
    _centrado(lienzo, alto - 143 * mm, detalle, "Helvetica", 10, GRIS, ancho)

    # Firma y sello
    y_firma = 42 * mm
    lienzo.setStrokeColor(TINTA)
    lienzo.setLineWidth(0.8)
    lienzo.line(48 * mm, y_firma, 118 * mm, y_firma)
    lienzo.setFillColor(TINTA)
    lienzo.setFont("Helvetica-Bold", 10.5)
    lienzo.drawString(48 * mm, y_firma - 6 * mm, ajustes.get("org_firmante", ""))
    lienzo.setFont("Helvetica", 9)
    lienzo.setFillColor(GRIS)
    lienzo.drawString(48 * mm, y_firma - 11 * mm, ajustes.get("org_cargo_firmante", ""))

    _sello(lienzo, ancho - 68 * mm, y_firma + 2 * mm, 15 * mm, codigo)

    lienzo.setFont("Helvetica", 7.5)
    lienzo.setFillColor(GRIS)
    vigencia = curso["vigencia_meses"] or 0
    pie = f"Documento generado electrónicamente el {_fecha_larga(emitido_en)}."
    if vigencia:
        pie += f" Vigencia de la certificación: {vigencia} meses."
    lienzo.drawCentredString(ancho / 2, 24 * mm, pie)
    lienzo.drawCentredString(ancho / 2, 20 * mm,
                             f"Verifica su autenticidad con el código {codigo} "
                             "en la plataforma de capacitación.")

    lienzo.showPage()
    lienzo.save()
    return Path(ruta)
