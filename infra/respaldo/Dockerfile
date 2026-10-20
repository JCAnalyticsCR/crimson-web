# Respaldo diario (servicio cron "respaldo" de Railway). Contexto de build = raiz del repo (usa el .dockerignore de la
# raiz: solo entra api/). Arranca a la hora del Cron Schedule, respalda, verifica, cifra, sube y TERMINA.
# Configuracion y variables: infra/RESPALDO.md seccion 4.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# age cifra con llaves PUBLICAS (este servicio no puede descifrar) y rclone sube al almacenamiento externo. Nada mas:
# sin Pango/WeasyPrint (no genera PDF) ni pg_dump (el zip es el que se verifica; el volumen de Railway es la 2.a capa).
RUN apt-get update \
 && apt-get install -y --no-install-recommends age rclone ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY api/ ./
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd --create-home --uid 10001 crimson && chown -R crimson /app
USER crimson
ENV MALLOC_ARENA_MAX=2
CMD ["python", "-m", "app.tools.respaldo_diario"]
