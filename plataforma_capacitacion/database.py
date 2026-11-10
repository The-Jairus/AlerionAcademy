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
    rol           TEXT NOT NULL DEFAULT 'learner',   -- learner | instructor | admin
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
    one_time          INTEGER NOT NULL DEFAULT 0,   -- 1 = no vence nunca
    creado_por        INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    cert_tipo         TEXT NOT NULL DEFAULT 'unsigned',   -- signed | unsigned
    actualizado_en    TEXT,
    actualizado_por   INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    firma_nombre      TEXT,
    firma_cargo       TEXT,
    firma_archivo     TEXT,
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
    -- Columnas históricas: el material de verdad está en `contenidos`. Se
    -- conservan para que la migración pueda leerlas y por si hiciera falta
    -- revisar qué tenía un módulo antes del cambio.
    tipo_material     TEXT CHECK (tipo_material IS NULL OR tipo_material IN ('video', 'documento')),
    carpeta           TEXT,
    archivo           TEXT,
    archivo_original  TEXT,
    duracion_segundos REAL DEFAULT 0,
    paginas           INTEGER DEFAULT 0,
    quiz_id           INTEGER REFERENCES quices(id) ON DELETE SET NULL,  -- NULL = sin examen
    creado_en         TEXT NOT NULL
);

-- Un módulo puede llevar varios materiales (una presentación y un video, por
-- ejemplo). Se recorren en orden y hay que terminarlos todos para desbloquear
-- el examen. Las columnas de material que quedan en `modulos` son históricas:
-- la verdad está aquí.
CREATE TABLE IF NOT EXISTS contenidos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    modulo_id         INTEGER NOT NULL REFERENCES modulos(id) ON DELETE CASCADE,
    titulo            TEXT,
    orden             INTEGER NOT NULL DEFAULT 1,
    tipo_material     TEXT NOT NULL CHECK (tipo_material IN ('video', 'documento')),
    carpeta           TEXT,
    archivo           TEXT,
    archivo_original  TEXT,
    duracion_segundos REAL DEFAULT 0,
    paginas           INTEGER DEFAULT 0,
    creado_en         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contenidos_modulo ON contenidos (modulo_id, orden);

CREATE TABLE IF NOT EXISTS avance_contenidos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    asignacion_id     INTEGER NOT NULL REFERENCES asignaciones(id) ON DELETE CASCADE,
    contenido_id      INTEGER NOT NULL REFERENCES contenidos(id) ON DELETE CASCADE,
    max_posicion      REAL NOT NULL DEFAULT 0,
    avance            REAL NOT NULL DEFAULT 0,
    completo          INTEGER NOT NULL DEFAULT 0,
    completado_en     TEXT,
    UNIQUE (asignacion_id, contenido_id)
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

-- Un examen es independiente: vive por su cuenta con su banco de preguntas y
-- puede usarse en varios módulos, incluso de cursos distintos. El módulo es el
-- que apunta al examen (modulos.quiz_id); sin examen, ese campo queda en NULL.
CREATE TABLE IF NOT EXISTS quices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo              TEXT NOT NULL,
    descripcion         TEXT,
    creado_por          INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
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

-- Instructores que, además del creador, pueden editar el curso.
CREATE TABLE IF NOT EXISTS curso_instructores (
    curso_id   INTEGER NOT NULL REFERENCES cursos(id) ON DELETE CASCADE,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    agregado_en TEXT NOT NULL,
    PRIMARY KEY (curso_id, usuario_id)
);

-- Bitácora: qué pasó, quién lo hizo y cuándo. Solo la ve el administrador.
CREATE TABLE IF NOT EXISTS bitacora (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id  INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    usuario_nombre TEXT,          -- copia, para que el registro sobreviva al borrado
    accion      TEXT NOT NULL,    -- curso.crear, usuario.eliminar, ...
    objeto      TEXT,             -- a qué se le hizo, en palabras
    objeto_id   INTEGER,
    detalle     TEXT,
    ip          TEXT,          -- en desuso: ya no se registra (ver _olvidar_ips)
    creado_en   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bitacora_fecha ON bitacora (creado_en DESC);

-- Encuesta de satisfacción al terminar un curso.
CREATE TABLE IF NOT EXISTS encuestas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asignacion_id INTEGER NOT NULL REFERENCES asignaciones(id) ON DELETE CASCADE,
    usuario_id    INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    curso_id      INTEGER NOT NULL REFERENCES cursos(id) ON DELETE CASCADE,
    estrellas     INTEGER NOT NULL CHECK (estrellas BETWEEN 1 AND 5),
    comentario    TEXT,
    creado_en     TEXT NOT NULL,
    UNIQUE (asignacion_id)
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


def sumar_meses(fecha: str, meses: int) -> str:
    """Suma meses a una fecha ISO sin dependencias externas."""
    base = datetime.fromisoformat(str(fecha).replace("Z", "+00:00")).replace(tzinfo=None)
    total = base.month - 1 + int(meses)
    anio, mes = base.year + total // 12, total % 12 + 1
    dia = min(base.day, [31, 29 if anio % 4 == 0 and (anio % 100 or anio % 400 == 0) else 28,
                         31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mes - 1])
    return base.replace(year=anio, month=mes, day=dia).isoformat(timespec="seconds")


def ahora() -> str:
    """Marca de tiempo ISO en UTC, sin microsegundos.

    Todo se guarda en UTC; la conversión a la hora de la organización se hace
    solo al mostrarlo (ver zona_horaria y los filtros de app.py).
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def zona_horaria(con=None):
    """Zona horaria de la organización, de la configuración."""
    from zoneinfo import ZoneInfo
    try:
        nombre = (obtener_config(con).get("org_timezone") or "").strip()
        return ZoneInfo(nombre) if nombre else timezone.utc
    except Exception:
        return timezone.utc          # nombre inválido: no romper la pantalla


def en_zona(valor, con=None):
    """Pasa una marca de tiempo guardada en UTC a la hora de la organización."""
    if not valor:
        return None
    try:
        momento = datetime.fromisoformat(str(valor))
    except ValueError:
        return None
    if momento.tzinfo is None:
        # Una fecha sola ("2026-11-07") es un día del calendario, no un
        # instante: la fecha límite de un curso es ese día donde está la
        # organización. Si se leyera como medianoche UTC, al convertirla a
        # Bogotá se correría al día anterior y el vencimiento aparecería un
        # día antes. Las marcas con hora sí vienen en UTC.
        zona = zona_horaria(con)
        if len(str(valor).strip()) <= 10:
            return momento.replace(tzinfo=zona)
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(zona_horaria(con))


def hoy_local(con=None) -> str:
    """La fecha de hoy donde está la organización, no en UTC.

    A las 7 de la noche en Colombia en UTC ya es el día siguiente: comparar
    vencimientos contra la fecha UTC marcaba cursos como vencidos cinco horas
    antes de tiempo.
    """
    return datetime.now(zona_horaria(con)).strftime("%Y-%m-%d")


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


def _quices_reutilizables(con):
    """El examen deja de pertenecer a un módulo y pasa a ser reutilizable.

    Antes: quices.modulo_id, uno por módulo, borrado en cascada con él.
    Ahora: modulos.quiz_id, varios módulos pueden apuntar al mismo examen y
    borrar un módulo ya no se lleva por delante el banco de preguntas.
    """
    if "quiz_id" not in _columnas(con, "modulos"):
        con.execute("ALTER TABLE modulos ADD COLUMN quiz_id INTEGER")

    if "modulo_id" not in _columnas(con, "quices"):
        # Ya migrada; solo falta el dueño si viene de una versión intermedia.
        if "creado_por" not in _columnas(con, "quices"):
            con.execute("ALTER TABLE quices ADD COLUMN creado_por INTEGER")
        if "descripcion" not in _columnas(con, "quices"):
            con.execute("ALTER TABLE quices ADD COLUMN descripcion TEXT")
        return

    # 1. Cada módulo se queda apuntando al examen que ya tenía. Esto va PRIMERO:
    #    después de reconstruir la tabla, modulo_id ya no existe.
    con.execute("UPDATE modulos SET quiz_id = ("
                "  SELECT q.id FROM quices q WHERE q.modulo_id = modulos.id)"
                " WHERE quiz_id IS NULL")

    # 2. El examen queda a nombre de quien creó el curso donde se usaba.
    with _sin_fk(con):
        con.execute("DROP TABLE IF EXISTS quices__viejo")
        con.execute("ALTER TABLE quices RENAME TO quices__viejo")
        con.executescript(ESQUEMA)
        con.execute("PRAGMA foreign_keys = OFF")
        con.execute(
            "INSERT INTO quices (id, titulo, descripcion, creado_por, puntaje_minimo,"
            " intentos_max, preguntas_por_intento, mezclar, creado_en)"
            " SELECT q.id, q.titulo, NULL, c.creado_por, q.puntaje_minimo,"
            " q.intentos_max, q.preguntas_por_intento, q.mezclar, q.creado_en"
            " FROM quices__viejo q"
            " LEFT JOIN modulos m ON m.id = q.modulo_id"
            " LEFT JOIN cursos c ON c.id = m.curso_id")
        con.execute("DROP TABLE quices__viejo")
    print("[migración] los exámenes ahora son reutilizables entre módulos")


def _relajar_modulos(con):
    """La tabla vieja exigía tipo_material; ahora el material vive aparte."""
    info = {f["name"]: f["notnull"] for f in con.execute("PRAGMA table_info(modulos)")}
    if not info.get("tipo_material"):
        return
    with _sin_fk(con):
        _reconstruir(con, "modulos")
    print("[migración] los módulos ya no exigen material propio")


def _material_a_contenidos(con):
    """El material pasa de vivir en el módulo a vivir en `contenidos`.

    Antes: un módulo = un archivo. Ahora un módulo lleva los que haga falta
    (una presentación y un video, por ejemplo). Cada módulo que ya existía se
    convierte en su primer contenido, y el avance que la gente llevaba se copia
    tal cual para que nadie tenga que repetir nada.
    """
    if con.execute("SELECT COUNT(*) FROM contenidos").fetchone()[0]:
        return                                  # ya se migró
    if "tipo_material" not in _columnas(con, "modulos"):
        return                                  # instalación nueva: no hay nada que mover

    modulos = con.execute(
        "SELECT id, titulo, tipo_material, carpeta, archivo, archivo_original,"
        " duracion_segundos, paginas, creado_en FROM modulos"
        " WHERE tipo_material IS NOT NULL").fetchall()
    if not modulos:
        return

    for modulo in modulos:
        contenido_id = con.execute(
            "INSERT INTO contenidos (modulo_id, titulo, orden, tipo_material, carpeta,"
            " archivo, archivo_original, duracion_segundos, paginas, creado_en)"
            " VALUES (?, NULL, 1, ?, ?, ?, ?, ?, ?, ?)",
            (modulo["id"], modulo["tipo_material"], modulo["carpeta"], modulo["archivo"],
             modulo["archivo_original"], modulo["duracion_segundos"], modulo["paginas"],
             modulo["creado_en"])).lastrowid
        # El avance del módulo era el de su único material: se copia al contenido.
        con.execute(
            "INSERT OR IGNORE INTO avance_contenidos (asignacion_id, contenido_id,"
            " max_posicion, avance, completo, completado_en)"
            " SELECT asignacion_id, ?, max_posicion, avance, material_completo, completado_en"
            " FROM avance_modulos WHERE modulo_id = ?", (contenido_id, modulo["id"]))
    print(f"[migración] {len(modulos)} módulo(s) pasaron a la tabla de contenidos")


def _olvidar_ips(con):
    """Borra las direcciones de red que la bitácora alcanzó a guardar.

    Se registraron por una decisión que no se consultó, y rastrear desde dónde
    se conecta cada empleado no aporta nada a una plataforma de capacitación.
    La columna se deja en la tabla (vaciarla es seguro; reconstruir la tabla,
    no) pero no se vuelve a escribir ni se muestra.
    """
    if "ip" not in _columnas(con, "bitacora"):
        return
    cuantas = con.execute("SELECT COUNT(*) FROM bitacora"
                          " WHERE ip IS NOT NULL AND ip <> ''").fetchone()[0]
    if cuantas:
        con.execute("UPDATE bitacora SET ip = NULL")
        print(f"[migración] se borraron {cuantas} direcciones de red de la bitácora")


def _redondear_avance(con):
    """Lo que ya está terminado pasa a marcar 100%.

    Un video se da por visto al 95%, así que quien lo terminaba dejaba el avance
    en 99 y pico, y ese decimal subía hasta el curso: aparecía "completado" con
    99%. Lo que está hecho está hecho.
    """
    arregladas = 0
    for tabla, condicion in (("avance_contenidos", "completo = 1"),
                             ("avance_modulos", "material_completo = 1"),
                             ("asignaciones", "estado = 'aprobado'")):
        if tabla not in {f["name"] for f in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}:
            continue
        cuantas = con.execute(
            f"SELECT COUNT(*) FROM {tabla} WHERE {condicion} AND avance < 100").fetchone()[0]
        if cuantas:
            con.execute(f"UPDATE {tabla} SET avance = 100 WHERE {condicion} AND avance < 100")
            arregladas += cuantas
    if arregladas:
        print(f"[migración] {arregladas} registro(s) de avance terminados pasaron a 100%")


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
    for columna, definicion in (
            ("one_time", "INTEGER NOT NULL DEFAULT 0"),
            ("creado_por", "INTEGER"),
            ("cert_tipo", "TEXT NOT NULL DEFAULT 'unsigned'"),
            ("firma_nombre", "TEXT"),
            ("firma_cargo", "TEXT"),
            ("firma_archivo", "TEXT")):
        if columna not in _columnas(con, "cursos"):
            con.execute(f"ALTER TABLE cursos ADD COLUMN {columna} {definicion}")
            if columna == "cert_tipo":
                # Los cursos que ya existían venían firmando con los datos de
                # Settings: se quedan firmados para que sus certificados no
                # cambien de un día para otro. Los nuevos se preguntan al crearlos.
                con.execute("UPDATE cursos SET cert_tipo = 'signed'")
    # Los cursos que ya existían quedan a nombre del primer administrador.
    con.execute("UPDATE cursos SET creado_por = ("
                "  SELECT id FROM usuarios WHERE rol = 'admin' ORDER BY id LIMIT 1)"
                " WHERE creado_por IS NULL")
    # 'estudiante' pasa a llamarse 'learner'
    con.execute("UPDATE usuarios SET rol = 'learner' WHERE rol = 'estudiante'")

    # El examen se separa del módulo y pasa a ser reutilizable. Va después de
    # intentos.modulo_id (que lee quices.modulo_id) y de cursos.creado_por (de
    # donde sale el dueño del examen).
    _quices_reutilizables(con)

    for columna, definicion in (("actualizado_en", "TEXT"), ("actualizado_por", "INTEGER")):
        if columna not in _columnas(con, "cursos"):
            con.execute(f"ALTER TABLE cursos ADD COLUMN {columna} {definicion}")
    con.execute("UPDATE cursos SET actualizado_en = creado_en, actualizado_por = creado_por"
                " WHERE actualizado_en IS NULL")

    _olvidar_ips(con)
    _redondear_avance(con)

    # El material del módulo pasa a la tabla de contenidos, y la columna que lo
    # exigía deja de ser obligatoria. El orden importa: primero se copia, luego
    # se relaja, porque la copia lee esas columnas.
    _material_a_contenidos(con)
    _relajar_modulos(con)

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


def obtener_config(con=None) -> dict:
    """Ajustes guardados sobre los valores por defecto.

    Sin argumentos usa la conexión del request. Los scripts que corren fuera de
    Flask (recordatorios.py, por ejemplo) pasan su propia conexión.
    """
    datos = dict(config.CONFIG_DEFAULT)
    filas = (con.execute("SELECT clave, valor FROM configuracion").fetchall()
             if con is not None else consultar("SELECT clave, valor FROM configuracion"))
    for fila in filas:
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
