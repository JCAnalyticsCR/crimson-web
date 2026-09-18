# API Crimson · python slim + uv. Incluye Pango para WeasyPrint (PDF real de facturas y cotizaciones).
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra pdf --no-install-project
COPY . .
RUN uv sync --frozen --no-dev --extra pdf
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd --create-home --uid 10001 crimson && chown -R crimson /app
USER crimson
EXPOSE 8000
# Migra, emite la invitacion inicial si el entorno esta vacio y arranca. En Railway el healthcheck apunta a /health
CMD ["sh", "-c", "alembic upgrade head && python -m app.bootstrap && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
