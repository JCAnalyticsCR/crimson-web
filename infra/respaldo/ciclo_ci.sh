#!/bin/sh
# Ciclo completo del respaldo diario DENTRO de la imagen del cron: las herramientas reales (age y rclone de Debian, los
# flags que usa el codigo, el usuario 10001, alembic.ini) y nada real: una base desechable con la semilla y un valor de
# cada tipo que cambia entre motores, llaves desechables (custodio y prueba) y un "bucket" local de rclone (:local:).
#
# Bases (variables opcionales; sin ellas, todo en SQLite):
#   CICLO_ORIGEN_URL      base de origen: se migra y se siembra con ella (dueno de las tablas)
#   CICLO_LECTURA_URL     la que usa el respaldo diario (en produccion, el rol de solo lectura de RESPALDO.md 4.3)
#   CICLO_RESTAURADA_URL  base VACIA (y ya creada) para el restore final
# El CI (trabajo "Respaldo") lo corre dos veces: en SQLite y como produccion, desde Postgres con el rol de solo lectura
# (la verificacion del cron restaura en SQLite: Postgres -> SQLite) y con el restore final en otra Postgres vacia, como
# la prueba mensual (Postgres -> Postgres). En local, desde la raiz del repo:
#
#   docker build -f infra/respaldo/Dockerfile -t crimson-respaldo:local .
#   docker run --rm -v "$PWD/infra/respaldo/ciclo_ci.sh:/ciclo_ci.sh:ro" crimson-respaldo:local sh /ciclo_ci.sh
#
# No va dentro de la imagen (el .dockerignore de la raiz solo deja entrar api/ y worker/). Falla en el primer paso que falle.
set -eu

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
ORIGEN="${CICLO_ORIGEN_URL:-sqlite:///$T/origen.db}"
LECTURA="${CICLO_LECTURA_URL:-$ORIGEN}"
RESTAURADA="${CICLO_RESTAURADA_URL:-sqlite:///$T/restaurada.db}"
export ENV=local DATABASE_URL="$ORIGEN"
export RESPALDO_DESTINO=":local:$T/bucket" RESPALDO_TMP="$T"
unset RESPALDO_PING_URL RESEND_API_KEY RESPALDO_AVISO_A 2>/dev/null || true

echo "== herramientas"
age --version
rclone version | head -n 1

echo "== base de origen (${ORIGEN%%:*}): alembic upgrade head + semilla + un valor de cada tipo"
alembic upgrade head
python - <<'PY'
import hashlib
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.db import make_engine
from app.models import Category, ExchangeRate, Media, Tenant
from app.seeds import seed

eng = make_engine()
with sessionmaker(bind=eng)() as s:
    seed(s, "admin@ejemplo.com", "solo-ci-desechable-2026")
    t = s.scalar(select(Tenant).order_by(Tenant.id))
    # lo que cambia de un motor a otro: binario, decimales al limite de la escala, JSON anidado con tildes y enteros
    # grandes, una auto-referencia y sellos de tiempo con zona y microsegundos (los pone el modelo)
    datos = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
    s.add(Media(tenant_id=t.id, key="ciclo-ci-" + "0" * 31, filename="prueba-ñandú.png", content_type="image/png",
                size=len(datos), sha256=hashlib.sha256(datos).hexdigest(), data=datos))
    padre = Category(tenant_id=t.id, name="Categoría padre (ciclo CI)")
    s.add(padre)
    s.flush()
    s.add(Category(tenant_id=t.id, name="Categoría hija (ciclo CI)", parent_id=padre.id))
    s.add(ExchangeRate(date=date(2001, 2, 3), currency="EUR", sell=Decimal("1234567.12345"), buy=Decimal("0.00001"),
                       source="manual", note="ciclo CI"))
    t.settings = {**(t.settings or {}), "ciclo_ci": {"z": [1, 2.5, None, True], "a": {"ñ": "Pérez", "n": 10**15}}}
    s.commit()
eng.dispose()
PY

echo "== llaves desechables: una de custodio (RESPALDO_AGE_RECIPIENTS) y una de prueba (RESPALDO_AGE_RECIPIENTS_PRUEBA)"
age-keygen -o "$T/llave-custodio.txt" 2>/dev/null
age-keygen -o "$T/llave-prueba.txt" 2>/dev/null
RESPALDO_AGE_RECIPIENTS="$(sed -n 's/^# public key: //p' "$T/llave-custodio.txt")"
RESPALDO_AGE_RECIPIENTS_PRUEBA="$(sed -n 's/^# public key: //p' "$T/llave-prueba.txt")"
export RESPALDO_AGE_RECIPIENTS RESPALDO_AGE_RECIPIENTS_PRUEBA

echo "== respaldo diario (revisar y correr), verificado en una SQLite desechable"
DATABASE_URL="$LECTURA" python -m app.tools.respaldo_diario --revisar
DATABASE_URL="$LECTURA" python -m app.tools.respaldo_diario
ls "$T/bucket/diario/" | grep -q '\.zip\.age$'
ls "$T/bucket/diario/" | grep -q '\.json$'
grep -q '"verificacion": "restaurado"' "$T"/bucket/diario/*.json
if ls "$T" | grep -q '^crimson-respaldo-'; then
  echo "FALLO: quedaron temporales del respaldo en $T" >&2
  exit 1
fi
# el custodio abre todas las copias; la llave de prueba solo la de diario/ (semanal/, mensual/ y anual/ existen los
# domingos y los dias 1: ese dia tambien se revisan)
for f in "$T"/bucket/*/crimson-*.zip.age; do
  age -d -i "$T/llave-custodio.txt" "$f" >/dev/null
  case "$f" in
    */diario/*) age -d -i "$T/llave-prueba.txt" "$f" >/dev/null ;;
    *) if age -d -i "$T/llave-prueba.txt" "$f" >/dev/null 2>&1; then
         echo "FALLO: la llave de prueba abre $(basename "$(dirname "$f")")/" >&2
         exit 1
       fi ;;
  esac
done

echo "== prueba mensual: bajar el ultimo, revisar edad y hashes, descifrar"
RESPALDO_AGE_IDENTITY_FILE="$T/llave-prueba.txt" GITHUB_OUTPUT="$T/salida" \
  python -m app.tools.prueba_restauracion --dir "$T/restauracion"
ZIP="$(sed -n 's/^zip=//p' "$T/salida")"
test -s "$ZIP"

echo "== restore en una base vacia (${RESTAURADA%%:*}), misma migracion que el codigo"
python -m app.tools.restore "$ZIP" --target "$RESTAURADA"

echo "OK ciclo completo del respaldo (${LECTURA%%:*} -> ${RESTAURADA%%:*}): backup -> verificacion -> age -> rclone -> descarga -> descifrado -> restore"
