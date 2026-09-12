"""Acceso a la base de datos SQLite."""
import re
import sqlite3
from datetime import datetime, timezone

from flask import g

import config

ESQUEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS usuarios (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre        TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    documento     TEXT,
    cargo         TEXT,
    password_hash TEXT NOT NULL,
    rol           TEXT NOT NULL DEFAULT 'estudiante',
    activo        INTEGER NOT NULL DEFAULT 1,
    creado_en     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cursos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo            TEXT NOT NULL,
    descripcion       TEXT,
    categoria         TEXT,
    tipo_material     TEXT,
    carpeta           TEXT,
    archivo           TEXT,
    archivo_original  TEXT,
    duracion_segundos REAL DEFAULT 0,
    paginas           INTEGER DEFAULT 0,
    horas             REAL DEFAULT 1,
    vigencia_meses    INTEGER DEFAULT 12,
    activo            INTEGER NOT NULL DEFAULT 1,
    creado_en         TEXT NOT NULL
);

-- Un curso se compone de uno o varios módulos. Cada módulo tiene su material
-- y su propio examen; el curso se aprueba cuando se aprueban todos.
CREATE TABLE IF NOT EXISTS modulos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    curso_id          INTEGER NOT NULL REFERENCES cursos(id) ON DELETE CASCADE,
    titulo            TEXT NOT NULL,
    descripcion       TEXT,
    orden             INTEGER NOT NULL DEFAULT 1,
    tipo_material     TEXT NOT NULL CHECK (tipo_material IN ('video', 'documento')),
    carpeta           TEXT,
    archivo           TEXT,
    archivo_original  TEXT,
    duracion_segundos REAL DEFAULT 0,
    paginas           INTEGER DEFAULT 0,
    creado_en         TEXT NOT NULL
);

-- Avance de una persona dentro de cada módulo del curso asignado.
CREATE TABLE IF NOT EXISTS avance_modulos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    asignacion_id     INTEGER NOT NULL REFERENCES asignaciones(id) ON DELETE CASCADE,
    modulo_id         INTEGER NOT NULL REFERENCES modulos(id) ON DELETE CASCADE,
    max_posicion      REAL NOT NULL DEFAULT 0,
    avance            REAL NOT NULL DEFAULT 0,
    material_completo INTEGER NOT NULL DEFAULT 0,
    estado            TEXT NOT NULL DEFAULT 'pendiente',
    completado_en     TEXT,
    UNIQUE (asignacion_id, modulo_id)
);

CREATE TABLE IF NOT EXISTS asignaciones (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id        INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    curso_id          INTEGER NOT NULL REFERENCES cursos(id) ON DELETE CASCADE,
    asignado_en       TEXT NOT NULL,
    fecha_limite      TEXT,
    avance            REAL NOT NULL DEFAULT 0,
    max_posicion      REAL NOT NULL DEFAULT 0,
    material_completo INTEGER NOT NULL DEFAULT 0,
    estado            TEXT NOT NULL DEFAULT 'pendiente',
    completado_en     TEXT,
    UNIQUE (usuario_id, curso_id)
);

CREATE TABLE IF NOT EXISTS quices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    modulo_id           INTEGER NOT NULL UNIQUE REFERENCES modulos(id) ON DELETE CASCADE,
    titulo              TEXT NOT NULL,
    puntaje_minimo      INTEGER NOT NULL DEFAULT 70,
    intentos_max        INTEGER NOT NULL DEFAULT 3,
    preguntas_por_intento INTEGER NOT NULL DEFAULT 0,   -- 0 = todo el banco
    mezclar             INTEGER NOT NULL DEFAULT 1,
    creado_en           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preguntas (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id   INTEGER NOT NULL REFERENCES quices(id) ON DELETE CASCADE,
    enunciado TEXT NOT NULL,
    orden     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS opciones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pregunta_id INTEGER NOT NULL REFERENCES preguntas(id) ON DELETE CASCADE,
    texto       TEXT NOT NULL,
    correcta    INTEGER NOT NULL DEFAULT 0,
    orden       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS intentos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    asignacion_id  INTEGER NOT NULL REFERENCES asignaciones(id) ON DELETE CASCADE,
    modulo_id      INTEGER REFERENCES modulos(id) ON DELETE CASCADE,
    quiz_id        INTEGER NOT NULL REFERENCES quices(id) ON DELETE CASCADE,
    usuario_id     INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    puntaje        REAL NOT NULL DEFAULT 0,
    aprobado       INTEGER NOT NULL DEFAULT 0,
    respuestas     TEXT,
    finalizado_en  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS certificados (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo     TEXT NOT NULL UNIQUE,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    curso_id   INTEGER NOT NULL REFERENCES cursos(id) ON DELETE CASCADE,
    intento_id INTEGER REFERENCES intentos(id) ON DELETE SET NULL,
    puntaje    REAL NOT NULL DEFAULT 0,
    archivo    TEXT NOT NULL,
    origen     TEXT NOT NULL DEFAULT 'plataforma',   -- plataforma | importado
    emitido_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS configuracion (
    clave TEXT PRIMARY KEY,
    valor TEXT
);

CREATE TABLE IF NOT EXISTS correos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    destinatario TEXT NOT NULL,
    asunto       TEXT NOT NULL,
    estado       TEXT NOT NULL,
    detalle      TEXT,
    adjunto      TEXT,
    creado_en    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_modulos_curso ON modulos(curso_id);
CREATE INDEX IF NOT EXISTS idx_avance_asig   ON avance_modulos(asignacion_id);
CREATE INDEX IF NOT EXISTS idx_asig_usuario ON asignaciones(usuario_id);
CREATE INDEX IF NOT EXISTS idx_asig_curso   ON asignaciones(curso_id);
CREATE INDEX IF NOT EXISTS idx_cert_usuario ON certificados(usuario_id);
"""


def ahora() -> str:
    """Marca de tiempo ISO en UTC, sin microsegundos."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def conectar() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def db() -> sqlite3.Connection:
    """Conexión ligada al ciclo de vida del request."""
    if "_db" not in g:
        g._db = conectar()
    return g._db


def cerrar_db(_exc=None):
    con = g.pop("_db", None)
    if con is not None:
        con.close()


def consultar(sql, params=(), uno=False):
    cur = db().execute(sql, params)
    filas = cur.fetchall()
    cur.close()
    return (filas[0] if filas else None) if uno else filas


def ejecutar(sql, params=()):
    con = db()
    cur = con.execute(sql, params)
    con.commit()
    ident = cur.lastrowid
    cur.close()
    return ident


def _columnas(con, tabla):
    return {f["name"] for f in con.execute(f"PRAGMA table_info({tabla})")}


def _sin_fk(con):
    """Contexto para cambios de estructura sin disparar borrados en cascada."""
    class Contexto:
        def __enter__(self):
            con.commit()
            self.anterior = con.isolation_level
            con.isolation_level = None
            con.execute("PRAGMA foreign_keys = OFF")
            # Sin esto, renombrar una tabla reescribe las claves foráneas de las
            # demás y las deja apuntando a la tabla temporal.
            con.execute("PRAGMA legacy_alter_table = ON")
            return con

        def __exit__(self, *_):
            con.execute("PRAGMA legacy_alter_table = OFF")
            con.execute("PRAGMA foreign_keys = ON")
            con.isolation_level = self.anterior
            return False
    return Contexto()


def _reconstruir(con, tabla):
    """Recrea una tabla desde el esquema actual conservando sus filas."""
    columnas = [f["name"] for f in con.execute(f"PRAGMA table_info({tabla})")]
    if not columnas:
        return
    temporal = f"{tabla}__tmp"
    con.execute(f"DROP TABLE IF EXISTS {temporal}")
    con.execute(f"ALTER TABLE {tabla} RENAME TO {temporal}")
    con.executescript(ESQUEMA)                  # vuelve a crearla como debe ser
    con.execute("PRAGMA foreign_keys = OFF")      # el esquema las reactiva; aquí estorban
    con.execute("PRAGMA legacy_alter_table = ON")
    actuales = {f["name"] for f in con.execute(f"PRAGMA table_info({tabla})")}
    comunes = ", ".join(c for c in columnas if c in actuales)
    if comunes:
        con.execute(f"INSERT INTO {tabla} ({comunes}) SELECT {comunes} FROM {temporal}")
    con.execute(f"DROP TABLE {temporal}")


def _reparar_referencias(con):
    """Repara tablas cuyas claves foráneas apuntan a una tabla inexistente.

    Al renombrar una tabla, SQLite reescribe las claves foráneas de las demás.
    Si luego se borra la renombrada, esas referencias quedan colgando y
    cualquier borrado en cascada falla con "no such table".
    """
    existentes = {f["name"] for f in
                  con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    rotas = []
    for fila in con.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'"):
        if not fila["sql"] or fila["name"].endswith(("__tmp", "__viejo", "_old", "_legacy")):
            continue
        for referencia in re.findall(r'REFERENCES\s+"?(\w+)"?', fila["sql"], re.IGNORECASE):
            if referencia not in existentes:
                rotas.append(fila["name"])
                break
    if not rotas:
        return
    with _sin_fk(con):
        for tabla in rotas:
            _reconstruir(con, tabla)
            print(f"[migración] referencias de {tabla} reparadas")
    _reparar_referencias(con)      # una reparación puede destapar otra


def _relajar_cursos(con):
    """La tabla vieja exigía tipo_material; ahora ese dato vive en los módulos."""
    info = {f["name"]: f["notnull"] for f in con.execute("PRAGMA table_info(cursos)")}
    if not info.get("tipo_material") or not info["tipo_material"]:
        return
    with _sin_fk(con):
        _reconstruir(con, "cursos")


def _quices_por_modulo(con):
    """Los quices dejan de colgar del curso y pasan a colgar del módulo."""
    if "curso_id" not in _columnas(con, "quices"):
        return
    with _sin_fk(con):
        con.execute("DROP TABLE IF EXISTS quices__viejo")
        con.execute("ALTER TABLE quices RENAME TO quices__viejo")
        con.executescript(ESQUEMA)
        con.execute("PRAGMA foreign_keys = OFF")
        con.execute(
            "INSERT INTO quices (id, modulo_id, titulo, puntaje_minimo, intentos_max,"
            " preguntas_por_intento, mezclar, creado_en)"
            " SELECT q.id, m.id, q.titulo, q.puntaje_minimo, q.intentos_max, 0, 1, q.creado_en"
            " FROM quices__viejo q JOIN modulos m ON m.curso_id = q.curso_id AND m.orden = 1")
        con.execute("DROP TABLE quices__viejo")


def migrar(con):
    """Lleva una base creada antes de los módulos al esquema nuevo."""
    tablas = {f["name"] for f in
              con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "cursos" not in tablas:
        return

    # 1. Cada curso con material propio se convierte en su módulo 1.
    if "carpeta" in _columnas(con, "cursos"):
        pendientes = con.execute(
            "SELECT c.* FROM cursos c WHERE c.carpeta IS NOT NULL AND c.carpeta <> ''"
            " AND NOT EXISTS (SELECT 1 FROM modulos m WHERE m.curso_id = c.id)").fetchall()
        for curso in pendientes:
            con.execute(
                "INSERT INTO modulos (curso_id, titulo, descripcion, orden, tipo_material,"
                " carpeta, archivo, archivo_original, duracion_segundos, paginas, creado_en)"
                " VALUES (?, ?, '', 1, ?, ?, ?, ?, ?, ?, ?)",
                (curso["id"], curso["titulo"], curso["tipo_material"], curso["carpeta"],
                 curso["archivo"], curso["archivo_original"], curso["duracion_segundos"],
                 curso["paginas"], ahora()))
        con.commit()

    # 2. La tabla de cursos deja de exigir el material.
    _relajar_cursos(con)

    # 3. Los quices pasan a colgar del módulo.
    _quices_por_modulo(con)

    # 4. Columnas nuevas en tablas que ya existían.
    if "modulo_id" not in _columnas(con, "intentos"):
        con.execute("ALTER TABLE intentos ADD COLUMN modulo_id INTEGER")
        con.execute("UPDATE intentos SET modulo_id = ("
                    "  SELECT q.modulo_id FROM quices q WHERE q.id = intentos.quiz_id)")
    if "origen" not in _columnas(con, "certificados"):
        con.execute("ALTER TABLE certificados ADD COLUMN origen TEXT NOT NULL"
                    " DEFAULT 'plataforma'")

    # 5. Repara referencias que hayan quedado colgando por migraciones anteriores.
    _reparar_referencias(con)

    # 6. El avance por curso se replica al módulo 1, para no perder progreso.
    filas = con.execute(
        "SELECT a.*, m.id AS modulo_id FROM asignaciones a"
        " JOIN modulos m ON m.curso_id = a.curso_id AND m.orden = 1"
        " WHERE NOT EXISTS (SELECT 1 FROM avance_modulos v WHERE v.asignacion_id = a.id)"
    ).fetchall()
    for fila in filas:
        con.execute(
            "INSERT OR IGNORE INTO avance_modulos (asignacion_id, modulo_id, max_posicion,"
            " avance, material_completo, estado, completado_en) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fila["id"], fila["modulo_id"], fila["max_posicion"], fila["avance"],
             fila["material_completo"], fila["estado"], fila["completado_en"]))
    con.commit()


def inicializar():
    con = conectar()
    con.executescript(ESQUEMA)
    migrar(con)
    existentes = {f["clave"]: f["valor"] for f in
                  con.execute("SELECT clave, valor FROM configuracion").fetchall()}
    # Migración: conserva los valores guardados con las claves antiguas en español.
    for antigua, nueva in config.CLAVES_ANTIGUAS.items():
        if existentes.get(antigua) and not existentes.get(nueva):
            con.execute("INSERT OR REPLACE INTO configuracion (clave, valor) VALUES (?, ?)",
                        (nueva, existentes[antigua]))
    for clave, valor in config.CONFIG_DEFAULT.items():
        con.execute(
            "INSERT OR IGNORE INTO configuracion (clave, valor) VALUES (?, ?)",
            (clave, valor),
        )
    con.commit()
    con.close()


def obtener_config() -> dict:
    datos = dict(config.CONFIG_DEFAULT)
    for fila in consultar("SELECT clave, valor FROM configuracion"):
        datos[fila["clave"]] = fila["valor"]
    return datos


def guardar_config(datos: dict):
    con = db()
    for clave, valor in datos.items():
        con.execute(
            "INSERT INTO configuracion (clave, valor) VALUES (?, ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            (clave, str(valor)),
        )
    con.commit()
