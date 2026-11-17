"""Optimiza el material ya subido: libera disco sin volver a cargar nada.

    python optimizar.py --revisar      Muestra cuánto se ahorraría, sin tocar nada.
    python optimizar.py --aplicar      Aplica la optimización.

Hace tres cosas:
  1. Convierte a WebP las páginas que estén en PNG (unas 4 veces más livianas).
  2. Recomprime los videos a 720p H.264 (suele reducirlos a la mitad).
  3. Borra los archivos fuente (el PDF o PPTX original) que ya no se usan,
     porque las páginas están renderizadas.

En el servidor:  docker compose exec app python optimizar.py --aplicar
"""
import sys
from pathlib import Path

import config
import database
import media

FUENTES = {".pdf", ".pptx", ".ppt", ".odp"}


def _mb(bytes_):
    return bytes_ / (1024 * 1024)


def _carpetas_en_uso():
    con = database.conectar()
    filas = con.execute("SELECT carpeta, tipo_material, archivo FROM modulos").fetchall()
    con.close()
    return {f["carpeta"]: (f["tipo_material"], f["archivo"]) for f in filas if f["carpeta"]}


def revisar():
    total = ahorro = 0
    huerfanas = []
    en_uso = _carpetas_en_uso()
    for carpeta in sorted(config.MATERIAL_DIR.iterdir()):
        if not carpeta.is_dir():
            continue
        peso = sum(f.stat().st_size for f in carpeta.rglob("*") if f.is_file())
        total += peso
        if carpeta.name not in en_uso:
            huerfanas.append((carpeta.name, peso))
            ahorro += peso
            continue
        pngs = list(carpeta.glob("p*.png"))
        if pngs:
            ahorro += int(sum(f.stat().st_size for f in pngs) * 0.75)   # WebP ≈ 1/4
        for fuente in carpeta.iterdir():
            if fuente.is_file() and fuente.suffix.lower() in FUENTES and pngs or \
               (fuente.is_file() and fuente.suffix.lower() in FUENTES
                    and list(carpeta.glob("p*.webp"))):
                ahorro += fuente.stat().st_size
        for video in carpeta.glob("original.*"):
            if video.suffix.lower() in config.EXT_VIDEO:
                ahorro += int(video.stat().st_size * 0.45)              # estimación

    print(f"Material actual:        {_mb(total):8.1f} MB")
    print(f"Ahorro estimado:        {_mb(ahorro):8.1f} MB  ({ahorro / total * 100:.0f}%)"
          if total else "Sin material todavía.")
    if huerfanas:
        print(f"\nCarpetas sin curso asociado ({len(huerfanas)}), se pueden borrar:")
        for nombre, peso in huerfanas:
            print(f"  {nombre}  {_mb(peso):.1f} MB")
    return total


def aplicar():
    from PIL import Image
    import shutil

    antes = sum(f.stat().st_size for f in config.MATERIAL_DIR.rglob("*") if f.is_file())
    en_uso = _carpetas_en_uso()
    con = database.conectar()

    for carpeta in sorted(config.MATERIAL_DIR.iterdir()):
        if not carpeta.is_dir():
            continue

        if carpeta.name not in en_uso:
            shutil.rmtree(carpeta, ignore_errors=True)
            print(f"  {carpeta.name}: carpeta huérfana eliminada")
            continue

        tipo, archivo = en_uso[carpeta.name]

        # 1. páginas PNG -> WebP
        pngs = sorted(carpeta.glob("p*.png"))
        for png in pngs:
            destino = png.with_suffix(".webp")
            try:
                Image.open(png).convert("RGB").save(
                    destino, format="WEBP", quality=media.CALIDAD_WEBP, method=4)
                png.unlink()
            except Exception as error:
                print(f"  {carpeta.name}/{png.name}: no se pudo convertir ({error})")
        if pngs:
            print(f"  {carpeta.name}: {len(pngs)} páginas pasadas a WebP")

        # 2. archivos fuente que ya no se usan
        if list(carpeta.glob("p*.webp")) or pngs:
            for fuente in list(carpeta.iterdir()):
                if fuente.is_file() and fuente.suffix.lower() in FUENTES:
                    fuente.unlink()
                    print(f"  {carpeta.name}: fuente {fuente.name} eliminada")

        # 3. videos
        if tipo == "video":
            actual = carpeta / archivo
            if actual.exists() and actual.name != "video.mp4":
                peso_antes = actual.stat().st_size
                nuevo = media._optimizar_video(actual)
                if nuevo != actual:
                    con.execute("UPDATE modulos SET archivo = ? WHERE carpeta = ?",
                                (nuevo.name, carpeta.name))
                    con.commit()
                    print(f"  {carpeta.name}: video {_mb(peso_antes):.1f} → "
                          f"{_mb(nuevo.stat().st_size):.1f} MB")

    con.close()
    despues = sum(f.stat().st_size for f in config.MATERIAL_DIR.rglob("*") if f.is_file())
    print(f"\nAntes:   {_mb(antes):8.1f} MB")
    print(f"Después: {_mb(despues):8.1f} MB")
    if antes:
        print(f"Liberado:{_mb(antes - despues):8.1f} MB  ({(antes - despues) / antes * 100:.0f}%)")


if __name__ == "__main__":
    database.inicializar()
    if "--aplicar" in sys.argv:
        print("Optimizando el material…\n")
        aplicar()
    else:
        revisar()
        print("\nPara aplicarlo:  python optimizar.py --aplicar")
