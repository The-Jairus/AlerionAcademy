"""Reinicia la base de datos dejando la estructura nueva, limpia.

    python reiniciar.py --cursos   Borra cursos, módulos, exámenes, asignaciones,
                                   intentos y certificados, junto con el material
                                   subido. CONSERVA usuarios y configuración.

    python reiniciar.py --todo     Borra además usuarios y configuración: la
                                   plataforma queda como recién instalada.

Las tablas afectadas no se vacían: se eliminan y se vuelven a crear desde el
esquema actual, así que cualquier resto de una migración anterior desaparece.
"""
import shutil
import sys

import config
import database

CURSOS = ["avance_modulos", "intentos", "certificados", "asignaciones",
          "opciones", "preguntas", "quices", "modulos", "cursos"]
TODO = CURSOS + ["correos", "configuracion", "usuarios"]


def _tablas(con):
    return {f["name"] for f in
            con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def reiniciar(completo=False):
    database.inicializar()
    con = database.conectar()
    objetivo = TODO if completo else CURSOS
    existentes = _tablas(con)

    if not completo:
        usuarios = con.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
        print(f"Se conservan {usuarios} usuario(s) y la configuración.")

    con.commit()
    anterior = con.isolation_level
    con.isolation_level = None
    try:
        con.execute("PRAGMA foreign_keys = OFF")
        # También caen las tablas temporales que hayan quedado de migraciones previas.
        sobrantes = [t for t in existentes
                     if t.endswith(("__tmp", "__viejo", "_old", "_legacy"))]
        for tabla in objetivo + sobrantes:
            if tabla in existentes or tabla in sobrantes:
                con.execute(f"DROP TABLE IF EXISTS {tabla}")
                print(f"  tabla {tabla} eliminada")
        con.executescript(database.ESQUEMA)          # se recrean limpias
        con.execute("PRAGMA foreign_keys = ON")
    finally:
        con.isolation_level = anterior

    for clave, valor in config.CONFIG_DEFAULT.items():
        con.execute("INSERT OR IGNORE INTO configuracion (clave, valor) VALUES (?, ?)",
                    (clave, valor))
    con.commit()

    problemas = con.execute("PRAGMA foreign_key_check").fetchall()
    print("\nRevisión de integridad:",
          "sin problemas" if not problemas else f"{len(problemas)} inconsistencia(s)")
    con.close()

    # Material y certificados en disco
    for carpeta in (config.MATERIAL_DIR, config.CERT_DIR):
        shutil.rmtree(carpeta, ignore_errors=True)
        carpeta.mkdir(parents=True, exist_ok=True)
    print("Material y certificados borrados del disco.")

    if completo:
        print("\nBase vacía. Crea el primer administrador con:")
        print("  python crear_admin.py")
    else:
        print("\nListo: entra con tu usuario de siempre y vuelve a crear los cursos.")


if __name__ == "__main__":
    completo = "--todo" in sys.argv
    if not completo and "--cursos" not in sys.argv:
        print(__doc__)
        sys.exit(0)
    alcance = "TODA la base (incluidos usuarios)" if completo else "cursos y sus registros"
    print(f"Se va a borrar: {alcance}.")
    if input("Escribe BORRAR para confirmar: ").strip() != "BORRAR":
        sys.exit("Cancelado.")
    reiniciar(completo)
