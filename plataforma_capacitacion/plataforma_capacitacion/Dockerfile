# Imagen lista para producción: incluye LibreOffice (para convertir .pptx) y ffmpeg.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/datos

RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-impress \
        fonts-dejavu-core \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt gunicorn

COPY . .

# La base de datos, el material y los certificados viven fuera del código,
# en un volumen, para que sobrevivan a cada actualización.
VOLUME ["/datos"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fs http://localhost:8000/ingreso || exit 1

# 3 procesos, 2 hilos cada uno; timeout alto porque subir un video tarda.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "--threads", "2", \
     "--timeout", "600", "--access-logfile", "-", "app:app"]
