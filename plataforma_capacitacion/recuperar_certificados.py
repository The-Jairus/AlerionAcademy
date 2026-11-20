"""Reconstruye los certificados cuya fila se perdió al borrar un curso.

Hasta esta versión, borrar un curso borraba en cascada los certificados que ya
se habían emitido por él. El PDF nunca se tocó —vive en `data/certificados/` y
no en la carpeta del curso—, así que el documento sigue ahí: lo que se perdió
es la fila que lo relaciona con la persona.

Este script recompone esas filas leyendo la bitácora, que sí sobrevivió. Cada
vez que alguien termina un curso queda un registro `curso.terminar` con quién
fue, qué curso, qué nota y con qué código de certificado, y eso es todo lo que
hace falta.

    python recuperar_certificados.py            # solo informa, no escribe nada
    python recuperar_certificados.py --aplicar  # crea las filas que faltan

En Docker:

    docker compose exec -T app python recuperar_certificados.py
"""
import re
import sys

import config
import database

PATRON_NOTA = re.compile(r"(\d+(?:[.,]\d+)?)\s*/\s*100")


def codigos_en_disco():
    """Los códigos de los PDF que hay guardados."""
    if not config.CERT_DIR.exists():
        return []
    return sorted(ruta.stem for ruta in config.CERT_DIR.glob("*.pdf"))


def rastro(con, codigo):
    """El registro de la bitácora que menciona ese código, si lo hay."""
    return con.execute(
        "SELECT * FROM bitacora WHERE detalle LIKE ? AND accion = 'curso.terminar'"
        " ORDER BY id DESC LIMIT 1", (f"%{codigo}%",)).fetchone()


def main():
    aplicar = "--aplicar" in sys.argv
    con = database.conectar()

    existentes = {f["codigo"] for f in con.execute("SELECT codigo FROM certificados")}
    usuarios = {f["id"] for f in con.execute("SELECT id FROM usuarios")}
    cursos = {f["id"] for f in con.execute("SELECT id FROM cursos")}

    en_disco = codigos_en_disco()
    huerfanos = [c for c in en_disco if c not in existentes]

    print(f"PDF guardados:            {len(en_disco)}")
    print(f"Con su fila en la base:   {len(en_disco) - len(huerfanos)}")
    print(f"Sin fila (recuperables):  {len(huerfanos)}\n")
    if not huerfanos:
        print("No hay nada que recuperar.")
        return 0

    recuperados, sin_rastro = 0, []
    for codigo in huerfanos:
        registro = rastro(con, codigo)
        if registro is None:
            sin_rastro.append(codigo)
            print(f"  ?  {codigo} — no hay rastro en la bitácora, hay que revisarlo a mano")
            continue
        if registro["usuario_id"] not in usuarios:
            sin_rastro.append(codigo)
            print(f"  ?  {codigo} — la persona ({registro['usuario_nombre']}) ya no existe")
            continue

        nota = PATRON_NOTA.search(registro["detalle"] or "")
        puntaje = float(nota.group(1).replace(",", ".")) if nota else 0.0
        # Si el curso todavía existe se vuelve a enlazar; si no, el certificado
        # queda con el título que la bitácora guardó y sin curso, que es
        # exactamente lo que el esquema nuevo permite.
        curso_id = registro["objeto_id"] if registro["objeto_id"] in cursos else None

        print(f"  ✓  {codigo} — {registro['usuario_nombre']} · {registro['objeto']} · "
              f"{puntaje:.0f}/100 · {registro['creado_en'][:10]}"
              + ("" if curso_id else "  (curso borrado)"))

        if aplicar:
            con.execute(
                "INSERT INTO certificados (codigo, usuario_id, curso_id, curso_titulo,"
                " vigencia_meses_emision, intento_id, puntaje, archivo, origen, emitido_en)"
                " VALUES (?, ?, ?, ?, ?, NULL, ?, ?, 'plataforma', ?)",
                (codigo, registro["usuario_id"], curso_id, registro["objeto"],
                 None, puntaje, f"{codigo}.pdf", registro["creado_en"]))
        recuperados += 1

    if aplicar:
        con.commit()
        print(f"\n{recuperados} certificado(s) devueltos al historial.")
    else:
        print(f"\n{recuperados} certificado(s) se pueden recuperar. "
              "No se escribió nada: vuelve a ejecutarlo con --aplicar.")
    if sin_rastro:
        print(f"{len(sin_rastro)} sin rastro suficiente: {', '.join(sin_rastro[:10])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
