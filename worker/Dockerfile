# Worker Celery: misma base que la API, comando distinto. Contexto de build = raiz del repo.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY api/ ./
COPY worker/celery_app.py ./worker_celery_app.py
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
CMD ["celery", "-A", "worker_celery_app:celery", "worker", "--beat", "--loglevel=INFO", "--concurrency=2"]
