"""Procesamiento del material de formación.

Todo lo que entra se optimiza para ocupar el mínimo en disco, que es lo que
determina el tamaño (y el costo) del servidor:

- Los videos se recomprimen a 720p H.264 (suele reducirlos a la mitad o menos).
- Las páginas de documentos se guardan en WebP, unas 4 veces más livianas que PNG.
- El archivo original se borra después de procesarlo: ya no se vuelve a usar.
- Las presentaciones (.pptx/.ppt/.odp) se convierten a PDF con LibreOffice.
- Cada PDF se rasteriza a imágenes PNG, una por página. El estudiante nunca
  recibe el archivo original: solo ve imágenes, así que no puede editarlo ni
  descargarlo desde el visor.
"""
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from werkzeug.utils import secure_filename

import config


class ErrorMaterial(Exception):
    """Problema al procesar el archivo del curso."""


# Calidad del material. Bajar RESOLUCION_VIDEO o subir CRF reduce más el peso.
RESOLUCION_VIDEO = 1280      # ancho máximo; la altura se ajusta sola
CRF_VIDEO = 28               # 23 = mejor calidad y más peso; 30 = más liviano
ESCALA_PAGINA = 1.6          # resolución de las páginas renderizadas
CALIDAD_WEBP = 80


def _duracion_video(ruta: Path) -> float:
    try:
        salida = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(ruta)],
            capture_output=True, text=True, timeout=120,
        )
        return round(float(salida.stdout.strip()), 2)
    except Exception:
        return 0.0


def _optimizar_video(origen: Path) -> Path:
    """Recomprime el video a 720p. Si no mejora, deja el original intacto.

    Corre con un solo hilo y prioridad baja: en un servidor de un núcleo, esto
    evita que ffmpeg acapare la CPU y deje a la plataforma sin responder.
    """
    destino = origen.with_name("video.mp4")
    try:
        subprocess.run(
            ["nice", "-n", "15", "ffmpeg", "-y",
             "-threads", "1", "-filter_threads", "1",
             "-i", str(origen),
             "-vf", f"scale='min({RESOLUCION_VIDEO},iw)':-2",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", str(CRF_VIDEO),
             "-profile:v", "high", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
             str(destino)],
            capture_output=True, timeout=7200)
    except Exception:
        destino.unlink(missing_ok=True)
        return origen
    if not destino.exists() or destino.stat().st_size == 0:
        return origen
    if destino.stat().st_size >= origen.stat().st_size:
        destino.unlink(missing_ok=True)      # el original ya estaba bien comprimido
        return origen
    origen.unlink(missing_ok=True)
    return destino


def _convertir_a_pdf(origen: Path, destino_dir: Path) -> Path:
    """Convierte una presentación a PDF usando LibreOffice."""
    perfil = destino_dir / ".lo_profile"
    proceso = subprocess.run(
        ["soffice", "--headless", "--norestore",
         f"-env:UserInstallation=file://{perfil}",
         "--convert-to", "pdf", "--outdir", str(destino_dir), str(origen)],
        capture_output=True, text=True, timeout=600,
    )
    shutil.rmtree(perfil, ignore_errors=True)
    pdf = destino_dir / (origen.stem + ".pdf")
    if not pdf.exists():
        raise ErrorMaterial(
            "No se pudo convertir la presentación a PDF. "
            f"Detalle de LibreOffice: {proceso.stderr[:300] or 'sin detalle'}"
        )
    return pdf


def _rasterizar(pdf: Path, destino_dir: Path, escala: float = ESCALA_PAGINA) -> int:
    """Genera una imagen WebP por página. Devuelve el número de páginas."""
    import pypdfium2 as pdfium

    documento = pdfium.PdfDocument(str(pdf))
    total = len(documento)
    if total == 0:
        raise ErrorMaterial("El documento no tiene páginas legibles.")
    for indice in range(total):
        imagen = documento[indice].render(scale=escala).to_pil()
        imagen.convert("RGB").save(destino_dir / f"p{indice + 1:04d}.webp",
                                   format="WEBP", quality=CALIDAD_WEBP, method=4)
    documento.close()
    return total


def guardar_material(archivo) -> dict:
    """Recibe un FileStorage y devuelve los metadatos del material listo."""
    nombre_original = secure_filename(archivo.filename or "")
    if not nombre_original:
        raise ErrorMaterial("Selecciona un archivo de material.")

    extension = Path(nombre_original).suffix.lower()
    if extension not in config.EXT_PERMITIDAS:
        permitidas = ", ".join(sorted(config.EXT_PERMITIDAS))
        raise ErrorMaterial(f"Formato no admitido. Usa uno de estos: {permitidas}.")

    carpeta_id = uuid.uuid4().hex[:12]
    carpeta = config.MATERIAL_DIR / carpeta_id
    carpeta.mkdir(parents=True, exist_ok=True)

    try:
        ruta = carpeta / f"original{extension}"
        archivo.save(ruta)

        if extension in config.EXT_VIDEO:
            duracion = _duracion_video(ruta)
            if duracion <= 0:
                raise ErrorMaterial(
                    "No se pudo leer la duración del video. Súbelo en MP4 (H.264) o WebM."
                )
            # La recompresión NO se hace aquí: tardaría minutos y la subida
            # expiraría. optimizar_en_segundo_plano() la hace después.
            return {
                "tipo_material": "video",
                "carpeta": carpeta_id,
                "archivo": ruta.name,
                "archivo_original": nombre_original,
                "duracion_segundos": duracion,
                "paginas": 0,
            }

        pdf = ruta if extension == ".pdf" else _convertir_a_pdf(ruta, carpeta)
        paginas = _rasterizar(pdf, carpeta)
        # Las páginas ya están renderizadas: el documento fuente no se vuelve a leer.
        pdf.unlink(missing_ok=True)
        ruta.unlink(missing_ok=True)
        return {
            "tipo_material": "documento",
            "carpeta": carpeta_id,
            "archivo": pdf.name,
            "archivo_original": nombre_original,
            "duracion_segundos": 0,
            "paginas": paginas,
        }
    except Exception:
        shutil.rmtree(carpeta, ignore_errors=True)
        raise


def optimizar_en_segundo_plano(modulo_id: int, carpeta_id: str, archivo: str) -> None:
    """Recomprime el video después de responder, sin bloquear la subida.

    Cuando termina, actualiza el nombre del archivo en la base de datos. Si algo
    falla, el módulo se queda con el video original, que funciona igual.
    """
    def tarea():
        import database
        origen = config.MATERIAL_DIR / carpeta_id / archivo
        if not origen.exists():
            return
        try:
            resultado = _optimizar_video(origen)
        except Exception as error:
            print(f"[media] no se pudo optimizar {carpeta_id}: {error}", flush=True)
            return
        if resultado.name == archivo:
            return                      # no hubo mejora: se deja como estaba
        con = database.conectar()
        try:
            con.execute("UPDATE modulos SET archivo = ? WHERE id = ?",
                        (resultado.name, modulo_id))
            con.commit()
            print(f"[media] video del módulo {modulo_id} optimizado "
                  f"({resultado.stat().st_size / 1024 / 1024:.1f} MB)", flush=True)
        finally:
            con.close()

    threading.Thread(target=tarea, daemon=True).start()


def guardar_firma(archivo) -> str:
    """Guarda la imagen de la firma del certificado y devuelve su nombre.

    Se recorta el fondo si la imagen trae transparencia y se limita el ancho:
    una firma escaneada de 4 MB no aporta nada frente a una de 40 KB.
    """
    nombre = secure_filename(archivo.filename or "")
    extension = Path(nombre).suffix.lower()
    if extension not in config.EXT_FIRMA:
        permitidas = ", ".join(sorted(config.EXT_FIRMA))
        raise ErrorMaterial(f"The signature must be an image ({permitidas}).")

    destino = config.FIRMA_DIR / f"{uuid.uuid4().hex[:12]}.png"
    config.FIRMA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image

        archivo.stream.seek(0)
        imagen = Image.open(archivo.stream)
        imagen = imagen.convert("RGBA")
        recorte = imagen.getbbox()          # quita el margen vacío del escaneo
        if recorte:
            imagen = imagen.crop(recorte)
        if imagen.width > 1200:
            alto = round(imagen.height * 1200 / imagen.width)
            imagen = imagen.resize((1200, alto), Image.LANCZOS)
        imagen.save(destino, format="PNG", optimize=True)
    except ErrorMaterial:
        raise
    except Exception as error:
        destino.unlink(missing_ok=True)
        raise ErrorMaterial(f"The signature image could not be read: {error}")
    return destino.name


def eliminar_firma(nombre: str | None):
    if nombre:
        (config.FIRMA_DIR / nombre).unlink(missing_ok=True)


def eliminar_material(carpeta_id: str | None):
    if carpeta_id:
        shutil.rmtree(config.MATERIAL_DIR / carpeta_id, ignore_errors=True)


def ruta_video(curso) -> Path:
    return config.MATERIAL_DIR / curso["carpeta"] / curso["archivo"]


def ruta_pagina(curso, numero: int) -> Path:
    """WebP para el material nuevo; PNG para el que se subió antes."""
    carpeta = config.MATERIAL_DIR / curso["carpeta"]
    webp = carpeta / f"p{int(numero):04d}.webp"
    return webp if webp.exists() else carpeta / f"p{int(numero):04d}.png"
