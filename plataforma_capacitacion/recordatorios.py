"""Recordatorio diario de la capacitación vencida.

Busca las asignaciones cuya fecha límite ya pasó y que la persona todavía no
aprueba, y le manda UN correo a cada una con todo lo que tiene pendiente (no un
correo por curso: eso sería ruido y la gente deja de leerlos).

El trabajo se ejecuta todos los días hábiles, porque tiene que detectar a quien
venció ayer. Pero a cada persona se le escribe como mucho una vez por semana:
un correo diario repitiendo lo mismo enseña a la gente a ignorarlo, y entonces
el recordatorio deja de servir justo cuando hace falta.

    python recordatorios.py              # envía
    python recordatorios.py --probar     # muestra a quién le escribiría, sin enviar
"""
import sys
from datetime import datetime, timedelta

import config
import database
import mailer

ASUNTO = "Overdue training reminder"

# Días que tienen que pasar entre dos recordatorios a la misma persona. Con 7,
# cada quien recibe uno por semana, el mismo día de la semana en que se le
# escribió la primera vez; así los correos quedan repartidos y no se amontonan
# todos el lunes. Bajarlo a 1 devuelve el comportamiento de un correo diario.
DIAS_ENTRE_RECORDATORIOS = 7


def _ya_le_escribimos_hace_poco(con, email: str, hoy: str,
                                dias: int = DIAS_ENTRE_RECORDATORIOS) -> bool:
    """¿Esta persona recibió el recordatorio dentro de los últimos `dias`?

    La ventana se calcula en UTC a partir de días locales. Recortar los diez
    primeros caracteres de `creado_en` —que está en UTC— no vale: durante las
    últimas cinco horas de cada día en Bogotá eso ya da la fecha de mañana, y
    el guardia dejaba pasar duplicados justo en esa franja.
    """
    primer_dia = (datetime.strptime(hoy, "%Y-%m-%d")
                  - timedelta(days=max(dias, 1) - 1)).strftime("%Y-%m-%d")
    desde, _ = database.limites_utc_del_dia(primer_dia, con)
    _, hasta = database.limites_utc_del_dia(hoy, con)
    fila = con.execute(
        "SELECT 1 FROM correos WHERE destinatario = ? AND asunto = ?"
        " AND creado_en >= ? AND creado_en < ? AND estado <> 'error' LIMIT 1",
        (email, ASUNTO, desde, hasta)).fetchone()
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


def _anotar_corrida(con, resultado: str):
    """Deja constancia de que el trabajo diario corrió, y de qué hizo.

    Sin esto la única señal de que los recordatorios funcionan es que lleguen,
    y la única señal de que no funcionan es que no lleguen: una tarea que nunca
    se instaló en el servidor falla en silencio absoluto durante semanas. Con
    la marca, Settings puede decir «esto no se ha ejecutado» en vez de callar.

    Se anota siempre que el trabajo corra de verdad, incluso cuando no había
    nada que enviar: «corrió y no había vencidos» es un resultado sano, y es
    justo lo que hay que poder distinguir de «no corrió».
    """
    con.execute(
        "INSERT INTO configuracion (clave, valor) VALUES ('recordatorios_corrida', ?)"
        " ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
        (database.ahora(),))
    con.execute(
        "INSERT INTO configuracion (clave, valor) VALUES ('recordatorios_resultado', ?)"
        " ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
        (resultado,))
    con.commit()


def main(probar=False) -> int:
    con = database.conectar()
    ajustes = database.obtener_config(con)
    hoy = database.hoy_local(con)   # la fecha de la organización, no la UTC

    if not mailer.activo(ajustes, "email_vencidos"):
        print("El recordatorio de vencidos está apagado en Settings. No se envía nada.")
        if not probar:
            _anotar_corrida(con, "turned off in Settings")
        con.close()
        return 0

    por_persona = vencidos(con, hoy)
    if not por_persona:
        print(f"{hoy}: no hay capacitación vencida. Nada que enviar.")
        if not probar:
            _anotar_corrida(con, "nothing overdue")
        con.close()
        return 0

    enviados = omitidos = 0
    for filas in por_persona.values():
        persona = filas[0]
        if _ya_le_escribimos_hace_poco(con, persona["email"], hoy):
            omitidos += 1
            print(f"  omitido {persona['email']}: ya recibió uno en los últimos "
                  f"{DIAS_ENTRE_RECORDATORIOS} días")
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

    if not probar:
        _anotar_corrida(con, f"{enviados} sent"
                        + (f", {omitidos} already reminded this week" if omitidos else ""))
    con.close()
    etiqueta = "se habrían enviado" if probar else "enviados"
    print(f"{hoy}: {enviados} recordatorio(s) {etiqueta}"
          + (f", {omitidos} omitido(s) por haber recibido uno hace menos de "
             f"{DIAS_ENTRE_RECORDATORIOS} días." if omitidos else "."))
    if probar:
        print("Esto fue una prueba: no cuenta como ejecución del trabajo diario.")
    return 0


if __name__ == "__main__":
    sys.exit(main(probar="--probar" in sys.argv or "--dry-run" in sys.argv))
