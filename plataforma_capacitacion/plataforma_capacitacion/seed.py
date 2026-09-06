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
    ("Trabajo seguro en alturas", "Curso obligatorio · Resolución 4272 de 2021",
     ["Todo trabajo a más de 2 metros sobre el nivel inferior es trabajo en alturas.",
      "Requiere permiso, certificación vigente y un coordinador designado."]),
    ("1. Antes de subir", "Verificación previa",
     ["Revisa el permiso de trabajo firmado por el coordinador.",
      "Inspecciona arnés, eslinga y puntos de anclaje: si hay un corte o una costura suelta, el equipo se retira.",
      "Confirma que el punto de anclaje resiste 2.270 kg por persona."]),
    ("2. El arnés", "Cómo se usa",
     ["Ajusta las bandas de las piernas: debe caber una mano, no dos.",
      "La argolla dorsal queda entre los omóplatos.",
      "La eslinga con absorbedor se conecta siempre por encima de la cabeza."]),
    ("3. Distancia de caída", "El cálculo que salva vidas",
     ["Longitud de la eslinga + desgarre del absorbedor + estatura + margen de 1 m.",
      "Si el resultado es mayor que la altura disponible, el sistema no sirve: cambia de método."]),
    ("4. Rescate", "Los primeros 15 minutos",
     ["El trauma por suspensión aparece rápido: nadie queda suspendido esperando ayuda.",
      "El plan de rescate se define antes de iniciar la tarea, no durante la emergencia."]),
    ("5. Qué recordar", "Cierre del curso",
     ["Sin permiso y sin inspección del equipo, no se sube.",
      "La argolla dorsal y el anclaje alto no son opcionales.",
      "Cada tarea en alturas tiene su plan de rescate escrito."]),
]

PREGUNTAS_ALTURAS = [
    ("¿A partir de qué altura una tarea se considera trabajo en alturas?",
     ["2 metros sobre el nivel inferior", "5 metros sobre el nivel inferior",
      "Cuando se usa escalera", "A partir de 10 metros"], 0),
    ("¿Dónde debe quedar la argolla dorsal del arnés?",
     ["Entre los omóplatos", "En la cintura", "Sobre el pecho", "Da igual"], 0),
    ("¿Cuánta resistencia debe tener el punto de anclaje por persona?",
     ["2.270 kg", "500 kg", "100 kg", "Lo que soporte la estructura"], 0),
    ("¿Cuándo se define el plan de rescate?",
     ["Antes de iniciar la tarea", "Cuando ocurre la caída",
      "Al terminar la jornada", "Lo define la ARL después"], 0),
    ("Si el cálculo de distancia de caída supera la altura disponible, ¿qué se hace?",
     ["Se cambia el método de trabajo", "Se acorta la eslinga a la mitad",
      "Se trabaja con cuidado", "Se pide permiso verbal"], 0),
]

PREGUNTAS_INDUCCION = [
    ("¿Qué debes hacer en tu primer día si detectas una condición insegura?",
     ["Reportarla de inmediato a tu líder", "Esperar a la reunión mensual",
      "Resolverla por tu cuenta sin avisar", "Anotarla y seguir trabajando"], 0),
    ("¿Cuál es el canal oficial para reportar un incidente?",
     ["La línea interna de HSE", "Un grupo de WhatsApp", "Comentarlo en la cafetería",
      "No se reportan los incidentes menores"], 0),
    ("¿Cada cuánto se renueva la capacitación obligatoria?",
     ["Según la vigencia de cada curso", "Nunca", "Solo si hay un accidente",
      "Cada cinco años"], 0),
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


def _crear_curso_documento(con):
    carpeta_id = uuid.uuid4().hex[:12]
    carpeta = config.MATERIAL_DIR / carpeta_id
    carpeta.mkdir(parents=True, exist_ok=True)
    pdf = carpeta / "original.pdf"
    _pdf_demo(pdf)
    paginas = media._rasterizar(pdf, carpeta)
    return con.execute(
        "INSERT INTO cursos (titulo, descripcion, categoria, tipo_material, carpeta, archivo,"
        " archivo_original, duracion_segundos, paginas, horas, vigencia_meses, activo, creado_en)"
        " VALUES (?, ?, ?, 'documento', ?, ?, ?, 0, ?, 2, 12, 1, ?)",
        ("Trabajo seguro en alturas",
         "Requisitos, uso del arnés, cálculo de distancia de caída y plan de rescate.",
         "Seguridad y salud en el trabajo", carpeta_id, pdf.name,
         "alturas.pdf", paginas, ahora())).lastrowid


def _crear_curso_video(con):
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
        "INSERT INTO cursos (titulo, descripcion, categoria, tipo_material, carpeta, archivo,"
        " archivo_original, duracion_segundos, paginas, horas, vigencia_meses, activo, creado_en)"
        " VALUES (?, ?, ?, 'video', ?, ?, ?, ?, 0, 1, 24, 1, ?)",
        ("Inducción de ingreso",
         "Video de bienvenida: canales de reporte, reglas de oro y qué hacer el primer día.",
         "Inducción", carpeta_id, destino.name, "induccion.mp4", duracion, ahora())).lastrowid


def _crear_quiz(con, curso_id, titulo, preguntas, minimo=70, intentos=3):
    quiz_id = con.execute(
        "INSERT INTO quices (curso_id, titulo, puntaje_minimo, intentos_max, creado_en)"
        " VALUES (?, ?, ?, ?, ?)", (curso_id, titulo, minimo, intentos, ahora())).lastrowid
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
        print("La base ya tiene usuarios. Usa --reiniciar si quieres empezar de cero.")
        return

    personas = [
        ("Laura Gómez Vidal", "admin@empresa.com", "Admin123*", "admin", "1020304050", "Jefe de Formación"),
        ("Carlos Ruiz Mejía", "carlos@empresa.com", "Curso123*", "estudiante", "79541203", "Técnico de mantenimiento"),
        ("Diana Patiño Cano", "diana@empresa.com", "Curso123*", "estudiante", "1093456781", "Analista de calidad"),
        ("Jorge Salcedo Rivas", "jorge@empresa.com", "Curso123*", "estudiante", "80234512", "Operario de planta"),
    ]
    ids = {}
    for nombre, email, clave, rol, documento, cargo in personas:
        ids[email] = con.execute(
            "INSERT INTO usuarios (nombre, email, documento, cargo, password_hash, rol, activo,"
            " creado_en) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            (nombre, email, documento, cargo, hash_password(clave), rol, ahora())).lastrowid

    print("Generando material del curso en PDF…")
    curso_doc = _crear_curso_documento(con)
    print("Generando video de demostración…")
    curso_video = _crear_curso_video(con)

    _crear_quiz(con, curso_doc, "Evaluación de trabajo en alturas", PREGUNTAS_ALTURAS, minimo=80)
    if curso_video:
        _crear_quiz(con, curso_video, "Evaluación de inducción", PREGUNTAS_INDUCCION, minimo=70)

    limite = (date.today() + timedelta(days=15)).isoformat()
    for email in ("carlos@empresa.com", "diana@empresa.com", "jorge@empresa.com", "admin@empresa.com"):
        con.execute("INSERT INTO asignaciones (usuario_id, curso_id, asignado_en, fecha_limite)"
                    " VALUES (?, ?, ?, ?)", (ids[email], curso_doc, ahora(), limite))
    if curso_video:
        for email in ("carlos@empresa.com", "diana@empresa.com"):
            con.execute("INSERT INTO asignaciones (usuario_id, curso_id, asignado_en, fecha_limite)"
                        " VALUES (?, ?, ?, ?)", (ids[email], curso_video, ahora(), limite))

    con.commit()
    con.close()
    print("\nListo. Entra con:")
    print("  Administrador  admin@empresa.com   Admin123*")
    print("  Estudiante     carlos@empresa.com  Curso123*")


if __name__ == "__main__":
    sembrar(reiniciar="--reiniciar" in sys.argv)
