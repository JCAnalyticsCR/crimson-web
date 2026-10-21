#!/bin/sh
# Ciclo completo del respaldo diario DENTRO de la imagen del cron: las herramientas reales (age y rclone de Debian, los
# flags que usa el codigo, el usuario 10001, alembic.ini) y nada real: una SQLite con la semilla, una llave desechable
# y un "bucket" local de rclone (:local:). Lo corre el CI (trabajo "Respaldo"); en local, desde la raiz del repo:
#
#   docker build -f infra/respaldo/Dockerfile -t crimson-respaldo:local .
#   docker run --rm -v "$PWD/infra/respaldo/ciclo_ci.sh:/ciclo_ci.sh:ro" crimson-respaldo:local sh /ciclo_ci.sh
#
# No va dentro de la imagen (el .dockerignore de la raiz solo deja entrar api/ y worker/). Falla en el primer paso que falle.
set -eu

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
export ENV=local DATABASE_URL="sqlite:///$T/origen.db"
export RESPALDO_DESTINO=":local:$T/bucket" RESPALDO_TMP="$T"
unset RESPALDO_PING_URL RESEND_API_KEY RESPALDO_AVISO_A 2>/dev/null || true

echo "== herramientas"
age --version
rclone version | head -n 1

echo "== base de origen: alembic upgrade head + semilla"
alembic upgrade head
python - <<'PY'
from sqlalchemy.orm import sessionmaker

from app.core.db import make_engine
from app.seeds import seed

eng = make_engine()
with sessionmaker(bind=eng)() as s:
    seed(s, "admin@ejemplo.com", "solo-ci-desechable-2026")
eng.dispose()
PY

echo "== llave desechable (la publica va a RESPALDO_AGE_RECIPIENTS)"
age-keygen -o "$T/llave-prueba.txt" 2>/dev/null
RESPALDO_AGE_RECIPIENTS="$(sed -n 's/^# public key: //p' "$T/llave-prueba.txt")"
export RESPALDO_AGE_RECIPIENTS

echo "== respaldo diario (revisar y correr)"
python -m app.tools.respaldo_diario --revisar
python -m app.tools.respaldo_diario
ls "$T/bucket/diario/" | grep -q '\.zip\.age$'
ls "$T/bucket/diario/" | grep -q '\.json$'
if ls "$T" | grep -q '^crimson-respaldo-'; then
  echo "FALLO: quedaron temporales del respaldo en $T" >&2
  exit 1
fi

echo "== prueba mensual: bajar el ultimo, revisar edad y hashes, descifrar"
RESPALDO_AGE_IDENTITY_FILE="$T/llave-prueba.txt" GITHUB_OUTPUT="$T/salida" \
  python -m app.tools.prueba_restauracion --dir "$T/restauracion"
ZIP="$(sed -n 's/^zip=//p' "$T/salida")"
test -s "$ZIP"

echo "== restore en una base vacia (misma migracion que el codigo)"
python -m app.tools.restore "$ZIP" --target "sqlite:///$T/restaurada.db"

echo "OK ciclo completo del respaldo: backup -> verificacion -> age -> rclone -> descarga -> descifrado -> restore"
