"""Crea datos de demostración para probar la plataforma de inmediato.

    python seed.py            → crea los datos si la base está vacía
    python seed.py --reiniciar → borra todo y vuelve a crearlos
"""
import shutil
import subprocess
import sys
import uuid
from datetime import date, timedelta

from reportlab.lib.pagesizes import landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

import config
import database
import media
from auth import hash_password
from database import ahora

DIAPOSITIVAS = [
    ("Ramp Safety Fundamentals", "Required training · Alerion Aviation Standards",
     ["The ramp is the highest-risk area of any operation: jet blast, propellers, ground equipment.",
      "Every person on the ramp wears a high-visibility vest and hearing protection."]),
    ("1. Before you step out", "Pre-ramp check",
     ["Confirm the aircraft is chocked and the beacon is off before approaching.",
      "Inspect your PPE: vest, hearing protection, safety footwear.",
      "Secure loose items. Anything that can lift in jet blast becomes a projectile."]),
    ("2. Danger zones", "Know where not to be",
     ["Stay clear of the intake hazard zone whenever an engine is running.",
      "Never walk behind an aircraft that is starting or taxiing.",
      "Approach the aircraft only after the anti-collision beacon is off."]),
    ("3. Ground equipment", "Moving vehicles near aircraft",
     ["Any vehicle approaching an aircraft stops at the safety line first.",
      "Use a wing walker whenever clearance is under 10 feet.",
      "Three points of contact when entering or leaving any ground vehicle."]),
    ("4. Foreign object debris", "FOD prevention",
     ["A single loose bolt ingested by an engine can cost millions and ground the fleet.",
      "Complete a FOD walk before every departure.",
      "If you drop something on the ramp, recover it or report it immediately."]),
    ("5. Key takeaways", "Course close-out",
     ["Beacon on means stay clear. No exceptions.",
      "PPE is worn every time, for every duration, on every ramp.",
      "Report every hazard and near miss the same day it happens."]),
]

PREGUNTAS_RAMPA = [
    ("When is it safe to approach a parked aircraft?",
     ["Once the anti-collision beacon is off", "As soon as it stops moving",
      "When the crew waves you in", "After the passengers disembark"], 0),
    ("What is required whenever ground clearance is under 10 feet?",
     ["A wing walker", "A second driver", "A written permit", "Nothing, drive slowly"], 0),
    ("What does FOD stand for on the ramp?",
     ["Foreign object debris", "Fuel on demand", "Flight operations data",
      "Final operational dispatch"], 0),
    ("Which PPE is required on the ramp at all times?",
     ["High-visibility vest and hearing protection", "Gloves only",
      "Sunglasses and a cap", "None if the stay is brief"], 0),
    ("What do you do after dropping a small part on the ramp?",
     ["Recover it or report it immediately", "Leave it, ground crews sweep later",
      "Kick it off the movement area", "Note it in the next shift report"], 0),
]

PREGUNTAS_INDUCCION = [
    ("What should you do on your first day if you spot an unsafe condition?",
     ["Report it to your supervisor right away", "Wait for the monthly meeting",
      "Fix it yourself without telling anyone", "Write it down and keep working"], 0),
    ("Which is the official channel for reporting a safety event?",
     ["The internal safety reporting line", "A group chat", "A conversation in the break room",
      "Minor events are not reported"], 0),
    ("How often is mandatory training renewed?",
     ["According to the validity period of each course", "Never",
      "Only after an incident", "Every five years"], 0),
]

def _pdf_demo(destino):
    ancho, alto = landscape((210 * mm, 148 * mm))
    lienzo = canvas.Canvas(str(destino), pagesize=(ancho, alto))
    for titulo, subtitulo, puntos in DIAPOSITIVAS:
        lienzo.setFillColorRGB(0.086, 0.129, 0.169)
        lienzo.rect(0, 0, ancho, alto, stroke=0, fill=1)
        lienzo.setFillColorRGB(0.31, 0.75, 0.61)
        lienzo.rect(24 * mm, alto - 30 * mm, 26 * mm, 2, stroke=0, fill=1)
        lienzo.setFillColorRGB(1, 1, 1)
        lienzo.setFont("Helvetica-Bold", 22)
        lienzo.drawString(24 * mm, alto - 44 * mm, titulo)
        lienzo.setFont("Helvetica", 11)
        lienzo.setFillColorRGB(0.62, 0.69, 0.74)
        lienzo.drawString(24 * mm, alto - 52 * mm, subtitulo)
        lienzo.setFillColorRGB(0.92, 0.94, 0.95)
        lienzo.setFont("Helvetica", 13)
        y = alto - 70 * mm
        for punto in puntos:
            for linea in _envolver(punto, 62):
                lienzo.drawString(24 * mm, y, linea)
                y -= 7 * mm
            y -= 4 * mm
        lienzo.showPage()
    lienzo.save()


def _envolver(texto, ancho):
    palabras, lineas, actual = texto.split(), [], ""
    for palabra in palabras:
        if len(actual) + len(palabra) + 1 > ancho:
            lineas.append(actual)
            actual = palabra
        else:
            actual = f"{actual} {palabra}".strip()
    if actual:
        lineas.append(actual)
    return lineas


def _crear_curso(con, titulo, descripcion, categoria, horas=2, vigencia=12):
    return con.execute(
        "INSERT INTO cursos (titulo, descripcion, categoria, horas, vigencia_meses, activo,"
        " creado_en) VALUES (?, ?, ?, ?, ?, 1, ?)",
        (titulo, descripcion, categoria, horas, vigencia, ahora())).lastrowid


def _modulo_documento(con, curso_id, titulo, orden):
    carpeta_id = uuid.uuid4().hex[:12]
    carpeta = config.MATERIAL_DIR / carpeta_id
    carpeta.mkdir(parents=True, exist_ok=True)
    pdf = carpeta / "original.pdf"
    _pdf_demo(pdf)
    paginas = media._rasterizar(pdf, carpeta)
    return con.execute(
        "INSERT INTO modulos (curso_id, titulo, descripcion, orden, tipo_material, carpeta,"
        " archivo, archivo_original, duracion_segundos, paginas, creado_en)"
        " VALUES (?, ?, '', ?, 'documento', ?, ?, ?, 0, ?, ?)",
        (curso_id, titulo, orden, carpeta_id, pdf.name, "ramp-safety.pdf", paginas,
         ahora())).lastrowid


def _modulo_video(con, curso_id, titulo, orden):
    carpeta_id = uuid.uuid4().hex[:12]
    carpeta = config.MATERIAL_DIR / carpeta_id
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / "original.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=60",
         "-f", "lavfi", "-i", "sine=frequency=340:duration=60",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(destino)],
        capture_output=True, timeout=600)
    if not destino.exists():
        shutil.rmtree(carpeta, ignore_errors=True)
        return None
    duracion = media._duracion_video(destino)
    return con.execute(
        "INSERT INTO modulos (curso_id, titulo, descripcion, orden, tipo_material, carpeta,"
        " archivo, archivo_original, duracion_segundos, paginas, creado_en)"
        " VALUES (?, ?, '', ?, 'video', ?, ?, ?, ?, 0, ?)",
        (curso_id, titulo, orden, carpeta_id, destino.name, "briefing.mp4", duracion,
         ahora())).lastrowid


def _crear_quiz(con, modulo_id, titulo, preguntas, minimo=70, intentos=3, por_intento=0):
    quiz_id = con.execute(
        "INSERT INTO quices (modulo_id, titulo, puntaje_minimo, intentos_max,"
        " preguntas_por_intento, mezclar, creado_en) VALUES (?, ?, ?, ?, ?, 1, ?)",
        (modulo_id, titulo, minimo, intentos, por_intento, ahora())).lastrowid
    for orden, (enunciado, opciones, correcta) in enumerate(preguntas, start=1):
        pregunta_id = con.execute(
            "INSERT INTO preguntas (quiz_id, enunciado, orden) VALUES (?, ?, ?)",
            (quiz_id, enunciado, orden)).lastrowid
        for indice, texto in enumerate(opciones):
            con.execute(
                "INSERT INTO opciones (pregunta_id, texto, correcta, orden) VALUES (?, ?, ?, ?)",
                (pregunta_id, texto, int(indice == correcta), indice + 1))
    return quiz_id


def sembrar(reiniciar=False):
    if reiniciar:
        for carpeta in (config.MATERIAL_DIR, config.CERT_DIR, config.OUTBOX_DIR):
            shutil.rmtree(carpeta, ignore_errors=True)
            carpeta.mkdir(parents=True, exist_ok=True)
        config.DB_PATH.unlink(missing_ok=True)
        for sufijo in ("-wal", "-shm"):
            config.DB_PATH.with_name(config.DB_PATH.name + sufijo).unlink(missing_ok=True)

    database.inicializar()
    con = database.conectar()

    if con.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]:
        con.close()
        print("The database already has users. Use --reiniciar to start from scratch.")
        return

    personas = [
        ("Training Administrator", "admin@flyalerion.com", "Admin123*", "admin", "", "Director of Training"),
        ("James Carter", "james@flyalerion.com", "Course123*", "estudiante", "A-10482", "Line Maintenance Technician"),
        ("Diana Reyes", "diana@flyalerion.com", "Course123*", "estudiante", "A-10517", "Flight Coordinator"),
        ("Marcus Hill", "marcus@flyalerion.com", "Course123*", "estudiante", "A-10633", "Ramp Agent"),
    ]
    ids = {}
    for nombre, email, clave, rol, documento, cargo in personas:
        ids[email] = con.execute(
            "INSERT INTO usuarios (nombre, email, documento, cargo, password_hash, rol, activo,"
            " creado_en) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            (nombre, email, documento, cargo, hash_password(clave), rol, ahora())).lastrowid

    print("Building the PDF course material…")
    curso_doc = _crear_curso(
        con, "Ramp Safety Fundamentals",
        "Danger zones, PPE, ground vehicle movement and FOD prevention on the ramp.",
        "Safety", horas=2)
    modulo_doc = _modulo_documento(con, curso_doc, "Module 1 — Ramp fundamentals", 1)
    _crear_quiz(con, modulo_doc, "Ramp Safety Assessment", PREGUNTAS_RAMPA,
                minimo=80, por_intento=3)   # 3 preguntas al azar de un banco de 5

    print("Building the demo video…")
    curso_video = _crear_curso(
        con, "New Hire Onboarding",
        "Welcome briefing: reporting channels, safety golden rules and your first week.",
        "Onboarding", horas=1, vigencia=24)
    modulo_video = _modulo_video(con, curso_video, "Module 1 — Welcome briefing", 1)
    if modulo_video:
        _crear_quiz(con, modulo_video, "Onboarding Assessment", PREGUNTAS_INDUCCION, minimo=70)
        modulo_extra = _modulo_documento(con, curso_video, "Module 2 — Reporting and standards", 2)
        _crear_quiz(con, modulo_extra, "Reporting Assessment", PREGUNTAS_RAMPA,
                    minimo=70, por_intento=3)

    limite = (date.today() + timedelta(days=15)).isoformat()
    for email in ("james@flyalerion.com", "diana@flyalerion.com", "marcus@flyalerion.com", "admin@flyalerion.com"):
        con.execute("INSERT INTO asignaciones (usuario_id, curso_id, asignado_en, fecha_limite)"
                    " VALUES (?, ?, ?, ?)", (ids[email], curso_doc, ahora(), limite))
    if curso_video:
        for email in ("james@flyalerion.com", "diana@flyalerion.com"):
            con.execute("INSERT INTO asignaciones (usuario_id, curso_id, asignado_en, fecha_limite)"
                        " VALUES (?, ?, ?, ?)", (ids[email], curso_video, ahora(), limite))

    con.commit()
    con.close()
    print("\nDone. Sign in with:")
    print("  Administrator  admin@flyalerion.com   Admin123*")
    print("  Employee       james@flyalerion.com   Course123*")


if __name__ == "__main__":
    sembrar(reiniciar="--reiniciar" in sys.argv)
