# Worker Celery (+ beat): misma base que la API, comando distinto. Contexto de build = raiz del repo.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --frozen --no-dev --extra pdf --no-install-project
COPY api/ ./
COPY worker/celery_app.py ./worker_celery_app.py
RUN uv sync --frozen --no-dev --extra pdf
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd --create-home --uid 10001 crimson && chown -R crimson /app
USER crimson
CMD ["celery", "-A", "worker_celery_app:celery", "worker", "--beat", "--loglevel=INFO", "--concurrency=2", "--schedule=/tmp/celerybeat-schedule"]
