"""Configuración central de la plataforma de capacitación."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
MATERIAL_DIR = DATA_DIR / "materiales"
CERT_DIR = DATA_DIR / "certificados"
OUTBOX_DIR = DATA_DIR / "correos_salida"
DB_PATH = DATA_DIR / "capacitacion.db"

for carpeta in (DATA_DIR, MATERIAL_DIR, CERT_DIR, OUTBOX_DIR):
    carpeta.mkdir(parents=True, exist_ok=True)


def _secret_key() -> str:
    """Llave de sesión persistente: sobrevive reinicios del servidor."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    archivo = DATA_DIR / "secret.key"
    if not archivo.exists():
        archivo.write_text(secrets.token_hex(32), encoding="utf-8")
        os.chmod(archivo, 0o600)
    return archivo.read_text(encoding="utf-8").strip()


SECRET_KEY = _secret_key()

# Subida de material: 1 GB por archivo
MAX_CONTENT_LENGTH = 1024 * 1024 * 1024

EXT_VIDEO = {".mp4", ".webm", ".m4v", ".mov"}
EXT_DOC = {".pdf", ".pptx", ".ppt", ".odp"}
EXT_PERMITIDAS = EXT_VIDEO | EXT_DOC

# Reglas de avance
PORCENTAJE_MIN_VIDEO = 95.0      # % del video que debe verse para habilitar el examen
SALTO_MAX_SEGUNDOS = 45.0        # tolerancia del servidor ante saltos en el video
SEGUNDOS_MIN_POR_PAGINA = 5      # tiempo mínimo de lectura por página del documento

# Valores por defecto de la configuración editable desde el panel
CONFIG_DEFAULT = {
    "org_name": "Alerion Aviation",
    "org_short": "Alerion",
    "org_area": "Training & Standards Department",
    "org_signer": "Director of Training",
    "org_signer_title": "Alerion Aviation",
    "admin_email": "training@flyalerion.com",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_password": "",
    "smtp_tls": "1",
    "smtp_sender": "training@flyalerion.com",
}

# Antiguas claves en español -> nuevas, para bases creadas antes del cambio de idioma
CLAVES_ANTIGUAS = {
    "org_nombre": "org_name",
    "org_firmante": "org_signer",
    "org_cargo_firmante": "org_signer_title",
    "correo_administrador": "admin_email",
    "smtp_puerto": "smtp_port",
    "smtp_usuario": "smtp_user",
    "smtp_remitente": "smtp_sender",
}
