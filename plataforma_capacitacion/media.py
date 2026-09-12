"""Procesamiento del material de formación.

- Los videos se guardan tal cual y se les mide la duración con ffprobe.
- Las presentaciones (.pptx/.ppt/.odp) se convierten a PDF con LibreOffice.
- Cada PDF se rasteriza a imágenes PNG, una por página. El estudiante nunca
  recibe el archivo original: solo ve imágenes, así que no puede editarlo ni
  descargarlo desde el visor.
"""
import shutil
import subprocess
import uuid
from pathlib import Path

from werkzeug.utils import secure_filename

import config


class ErrorMaterial(Exception):
    """Problema al procesar el archivo del curso."""


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


def _rasterizar(pdf: Path, destino_dir: Path, escala: float = 2.0) -> int:
    """Genera un PNG por página. Devuelve el número de páginas."""
    import pypdfium2 as pdfium

    documento = pdfium.PdfDocument(str(pdf))
    total = len(documento)
    if total == 0:
        raise ErrorMaterial("El documento no tiene páginas legibles.")
    for indice in range(total):
        imagen = documento[indice].render(scale=escala).to_pil()
        imagen.convert("RGB").save(destino_dir / f"p{indice + 1:04d}.png",
                                   format="PNG", optimize=True)
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


def eliminar_material(carpeta_id: str | None):
    if carpeta_id:
        shutil.rmtree(config.MATERIAL_DIR / carpeta_id, ignore_errors=True)


def ruta_video(curso) -> Path:
    return config.MATERIAL_DIR / curso["carpeta"] / curso["archivo"]


def ruta_pagina(curso, numero: int) -> Path:
    return config.MATERIAL_DIR / curso["carpeta"] / f"p{int(numero):04d}.png"
