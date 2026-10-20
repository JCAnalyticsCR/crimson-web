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
# glibc con 2 arenas en vez de 8 x nucleo: menos RAM retenida (Railway cobra la RAM tomada)
ENV MALLOC_ARENA_MAX=2
# IP del cliente (plan 1.10): uvicorn solo cree en X-Forwarded-For cuando la conexion viene de una red interna (el edge
# de Railway llega desde 100.64.0.0/10) y toma la ultima direccion que no es interna: lo que el cliente escriba a la
# izquierda no cuenta. Antes era '*' (la primera, la que escribe cualquiera). El panel y la tienda pasan por el nginx
# del portal, que manda la IP firmada con PROXY_SHARED_KEY (core/clientip.py). Railway puede sobrescribir la variable.
ENV FORWARDED_ALLOW_IPS="127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,fc00::/7"
# Migra, emite la invitacion inicial si el entorno esta vacio y arranca. En Railway el healthcheck apunta a /health.
# --no-access-log: el log de acceso de uvicorn guarda la URL con su query (tokens de enlaces); request_log (app/main.py)
# ya registra metodo, ruta sin query, estado y duracion.
CMD ["sh", "-c", "alembic upgrade head && python -m app.bootstrap && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --no-access-log"]
