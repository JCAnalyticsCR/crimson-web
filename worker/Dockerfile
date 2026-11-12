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
# RAM = lo que se paga en Railway (CPU casi en cero). Pool «solo» (11/11): las tareas corren en el proceso principal,
# una a la vez como ya pasaba con --concurrency=1, sin el proceso hijo del pool prefork (~130 MB menos). Quedan dos
# procesos: el worker y el beat embebido. Sin gossip/mingle (un solo worker) y glibc con 2 arenas en vez de 8 x nucleo.
# Nota: el pool solo no aplica time_limit (no hay tareas que lo usen) ni max-memory-per-child.
ENV MALLOC_ARENA_MAX=2
# Beat embebido con celery-redbeat: horario y candado en Redis, asi dos contenedores durante un despliegue no despachan
# doble y el horario no se pierde al reiniciar (antes: --schedule=/tmp/celerybeat-schedule). En "celery worker" la
# opcion es --scheduler: su -S es --statedb.
CMD ["celery", "-A", "worker_celery_app:celery", "worker", "--beat", "--scheduler=redbeat.RedBeatScheduler", "--loglevel=INFO", "--pool=solo", "--without-gossip", "--without-mingle"]
