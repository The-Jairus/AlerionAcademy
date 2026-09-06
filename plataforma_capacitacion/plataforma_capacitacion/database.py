"""Acceso a la base de datos SQLite."""
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
    tipo_material     TEXT NOT NULL CHECK (tipo_material IN ('video', 'documento')),
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
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    curso_id       INTEGER NOT NULL UNIQUE REFERENCES cursos(id) ON DELETE CASCADE,
    titulo         TEXT NOT NULL,
    puntaje_minimo INTEGER NOT NULL DEFAULT 70,
    intentos_max   INTEGER NOT NULL DEFAULT 3,
    creado_en      TEXT NOT NULL
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


def inicializar():
    con = conectar()
    con.executescript(ESQUEMA)
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
