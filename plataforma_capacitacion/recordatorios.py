"""Recordatorio diario de la capacitación vencida.

Busca las asignaciones cuya fecha límite ya pasó y que la persona todavía no
aprueba, y le manda UN correo a cada una con todo lo que tiene pendiente (no un
correo por curso: eso sería ruido y la gente deja de leerlos).

Se ejecuta una vez al día desde cron. Si por lo que sea se corre dos veces el
mismo día, la segunda no envía nada: antes de escribir revisa en la tabla
`correos` si esa persona ya recibió el recordatorio de hoy.

    python recordatorios.py              # envía
    python recordatorios.py --probar     # muestra a quién le escribiría, sin enviar
"""
import sys

import config
import database
import mailer

ASUNTO = "Overdue training reminder"


def _ya_le_escribimos_hoy(con, email: str, hoy: str) -> bool:
    fila = con.execute(
        "SELECT 1 FROM correos WHERE destinatario = ? AND asunto = ?"
        " AND substr(creado_en, 1, 10) = ? AND estado <> 'error' LIMIT 1",
        (email, ASUNTO, hoy)).fetchone()
    return fila is not None


def vencidos(con, hoy: str):
    """Asignaciones pasadas de fecha, agrupadas por persona."""
    filas = con.execute(
        "SELECT a.id, a.avance, a.fecha_limite, u.id AS usuario_id, u.nombre, u.email,"
        " c.titulo AS curso,"
        " CAST(julianday(?) - julianday(a.fecha_limite) AS INTEGER) AS dias"
        " FROM asignaciones a"
        " JOIN usuarios u ON u.id = a.usuario_id"
        " JOIN cursos c ON c.id = a.curso_id"
        " WHERE a.estado <> 'aprobado'"
        "   AND a.fecha_limite IS NOT NULL AND a.fecha_limite <> ''"
        "   AND a.fecha_limite < ?"
        "   AND u.activo = 1 AND c.activo = 1"
        " ORDER BY u.nombre, a.fecha_limite", (hoy, hoy)).fetchall()

    por_persona = {}
    for fila in filas:
        por_persona.setdefault(fila["usuario_id"], []).append(fila)
    return por_persona


def main(probar=False) -> int:
    con = database.conectar()
    ajustes = database.obtener_config(con)
    hoy = database.hoy_local(con)   # la fecha de la organización, no la UTC

    if not mailer.activo(ajustes, "email_vencidos"):
        print("El recordatorio de vencidos está apagado en Settings. No se envía nada.")
        con.close()
        return 0

    por_persona = vencidos(con, hoy)
    if not por_persona:
        print(f"{hoy}: no hay capacitación vencida. Nada que enviar.")
        con.close()
        return 0

    enviados = omitidos = 0
    for filas in por_persona.values():
        persona = filas[0]
        if _ya_le_escribimos_hoy(con, persona["email"], hoy):
            omitidos += 1
            print(f"  omitido {persona['email']}: ya recibió el recordatorio de hoy")
            continue
        cursos = ", ".join(f["curso"] for f in filas)
        if probar:
            print(f"  [prueba] {persona['email']}: {len(filas)} vencido(s) — {cursos}")
            enviados += 1
            continue
        # En primer plano: el proceso termina enseguida y queremos el registro
        # escrito antes de salir.
        mailer.enviar(ajustes, persona["email"], ASUNTO,
                      mailer.cuerpo_vencidos(persona, filas, ajustes),
                      en_segundo_plano=False)
        enviados += 1
        print(f"  enviado a {persona['email']}: {len(filas)} vencido(s) — {cursos}")

    con.close()
    etiqueta = "se habrían enviado" if probar else "enviados"
    print(f"{hoy}: {enviados} recordatorio(s) {etiqueta}"
          + (f", {omitidos} omitido(s) por duplicado." if omitidos else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main(probar="--probar" in sys.argv or "--dry-run" in sys.argv))
