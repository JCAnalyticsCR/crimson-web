# Despliegue seguro (CI + Railway)

Cómo llega un cambio a producción sin que una prueba rota, una migración con deriva o un `nginx.conf` con error
tumbe la plataforma. Complementa `infra/RESPALDO.md` (respaldos) y el runbook de Hacienda en `api/README.md`.

> Lo de Railway (secciones 3 y 4) se configura **a mano, una vez**, en el panel. El repo no trae archivos de
> configuración de Railway: *Config as Code* está obsoleto y no aplica a servicios nuevos.

---

## 1. El camino de un cambio

```
push a main ──► GitHub Actions "CI" (6 trabajos en paralelo, < 10 min)
                   │  verde  ──► Railway despliega SOLO los servicios cuyas rutas vigiladas cambiaron
                   │  rojo   ──► Railway marca el despliegue SKIPPED: producción sigue con la versión anterior
```

- Cada ítem va en su rama y su PR; el CI corre en cada push de cualquier rama y en cada PR, así que el rojo se ve
  **antes** de fusionar.
- **Un cambio de dinero por despliegue.** Si algo sale mal, se sabe cuál fue.

## 2. Qué revisa el CI (`.github/workflows/ci.yml`)

| Trabajo | Qué hace | Falla si… |
|---|---|---|
| API · ruff + pytest | `uv sync --locked`, `ruff check app ../worker`, `pytest -n auto` (en paralelo, Argon2 barato solo en pruebas, reloj en UTC como producción) | el lock no coincide con `pyproject.toml`, hay errores de lint o falla una prueba |
| Migraciones | Postgres 16 vacía: una sola cabeza de Alembic, `alembic upgrade head`, `alembic check`, la última migración se revierte y se vuelve a aplicar, y al final las carreras de inventario contra Postgres real (`SELECT … FOR UPDATE`) | hay dos cabezas, una migración no corre en Postgres, el modelo y las migraciones no coinciden, el `downgrade` no sirve, o dos pedidos pueden llevarse la última unidad |
| Portal · tsc + vite build | `npm ci`, `tsc -b`, `vite build` | errores de tipos o de build |
| Portal · imagen + nginx -t | `docker build portal` (el Dockerfile arma `nginx.conf` con `envsubst` y corre `nginx -t`) | `nginx.conf` o el Dockerfile del portal tienen un error |
| Respaldo · imagen + ciclo completo | construye `infra/respaldo/Dockerfile` y, dentro de la imagen, con `age` y `rclone` reales y nada real (`infra/respaldo/ciclo_ci.sh`): SQLite con la semilla → respaldo diario → bucket local → prueba mensual (bajar, hashes, descifrar) → `restore` en una base vacía | la imagen no construye, o cualquier paso del respaldo o de su restauración falla con las herramientas de verdad |
| Dependencias · pip-audit + npm audit | vulnerabilidades conocidas en `uv.lock` y altas en `portal/package-lock.json` (sin las de desarrollo) | **en un PR**, si hay alguna que no esté en la lista de excepciones. En un push solo informa (el paso queda en rojo y el trabajo en verde) |

Lo mismo en local, antes de subir:

```bash
cd api && uv sync --locked && uv run ruff check app ../worker && TZ=UTC uv run pytest -n auto -q
cd api && DATABASE_URL=postgresql+psycopg://crimson:crimson_local_only@localhost:5432/crimson \
          uv run alembic upgrade head && uv run alembic check      # con infra/docker-compose.yml levantado
cd portal && npm ci && npx tsc -b && npx vite build
docker build -t crimson-portal:local portal                         # nginx -t
docker build -f infra/respaldo/Dockerfile -t crimson-respaldo:local .  # imagen del cron de respaldo
docker run --rm -v "$PWD/infra/respaldo/ciclo_ci.sh:/ciclo_ci.sh:ro" crimson-respaldo:local sh /ciclo_ci.sh
```

Las pruebas del CI corren con `TZ=UTC`, el mismo reloj de producción (Railway). Una prueba que falla solo de 18:00 a
24:00 CR no es "un problema del CI": muestra que la app (o la prueba) mezcla `date.today()`, que en producción es la
fecha UTC, con la fecha de Costa Rica (`cr_now()`), justo el borde que pide vigilar la sección 2, regla 8 del plan.
Se arregla usando la fecha de Costa Rica, nunca fijando otro reloj en el CI. Para verlo a cualquier hora en local:
`TZ=XXX-14 uv run pytest -q tests/test_x.py` (desde las 04:00 CR la fecha local ya es la de mañana, como en
producción después de las 18:00 CR; funciona igual en Windows y en Linux).

Hoy hay **un** error conocido de este tipo, de la app y no de la prueba: `commissions.py` fecha con `date.today()` los
movimientos creados "ahora" (`earned_on`, líneas ~874, ~1348 y ~1413), y *Mis números* corta en la fecha CR. Lo
corrige el ítem 2.1 (`cr_today()` en las fechas de dinero). Mientras tanto,
`test_ganancias_aliados.py::test_ajuste_del_pago_acordado_no_resta_dos_veces` está marcada `xfail` **solo** en ese
horario y con `strict`: cuando 2.1 lo corrija, la marca hace fallar el CI hasta que se quite.

Reglas que el CI hace cumplir:

- **Migraciones:** como máximo una revisión por ítem, re-encadenada al head vigente antes de fusionar (nada de
  revisiones `merge`), reversible, y el modelo tiene que quedar igual que la migración (`alembic check`).
- **Dependencias:** `uv.lock` siempre al día (`uv sync --locked`). Las nuevas, pineadas con `==`. En un PR,
  `pip-audit` y `npm audit --omit=dev --audit-level=high` **bloquean**: la corrección va en ese PR o en uno aparte
  (`uv lock --upgrade-package <paquete>` / `npm install <paquete>@<versión> --save-exact`, y la suite en verde). Si
  una vulnerabilidad de Python no tiene arreglo o no aplica, se acepta en `IGNORAR` del paso de `pip-audit`
  (`.github/workflows/ci.yml`) con el motivo, quién la aceptó y hasta cuándo. En un push a `main` solo informan: una
  vulnerabilidad recién publicada no puede frenar el despliegue de un arreglo urgente.
  Al activar la compuerta (2026-10-20) se corrigieron las dos que había: `certifi` 2026.10.19 (raíces de emSign con
  llaves comprometidas: afecta el TLS hacia Hacienda, ONVO y Resend) y `react-router-dom` 7.18.4 (varias altas).

## 3. Activar "Wait for CI" y las rutas vigiladas en Railway (una vez)

En cada servicio: **Settings → Source**.

| Servicio | Rutas vigiladas (Watch Paths) | Wait for CI |
|---|---|---|
| `api` | `/api/**` | Sí |
| `worker` | `/api/**` y `/worker/**` (una por línea) | Sí |
| `portal` | `/portal/**` | Sí |
| `respaldo` (cron, ver `infra/RESPALDO.md` §4) | `/api/**` y `/infra/respaldo/**` | Sí |

- Las rutas empiezan con `/` aunque el servicio tenga *Root Directory* (`api`, `portal`): Railway las evalúa desde la
  raíz del repo.
- `docs/**` (landing y demo en GitHub Pages) y `.github/**` no despliegan ningún servicio.
- Si el interruptor "Wait for CI" no aparece: en GitHub → Settings → Applications → Railway, aceptar los permisos
  nuevos que pide la app. El workflow ya cumple el requisito (`on: push` con `main` en `branches`; no quitarlo
  aunque `"**"` ya lo incluya).

Cómo se comporta (documentación de Railway):

- El despliegue queda en **WAITING** hasta que terminan todos los workflows de GitHub Actions del commit. Railway
  mira la conclusión de cada workflow, no de cada trabajo (por eso, en un push, los pasos informativos de auditoría
  de dependencias no bloquean).
- Uno **fallido** lo pasa a **SKIPPED** al instante; producción sigue con la versión anterior.
- Uno **cancelado** solo bloquea si ningún otro workflow del commit pasó. Como el de GitHub Pages también corre en
  `main`, el CI **nunca se cancela en `main`** (cada commit tiene su propio grupo de concurrencia).
- Si en 2 horas no terminó, el despliegue se salta.
- El build de GitHub Pages ("pages build and deployment") también es un workflow del commit: si falla, ese commit
  tampoco despliega la plataforma. Los cambios de `docs/` van en commits aparte de los de `api/` o `portal/`.

### Prueba de la compuerta (una vez, en ventana)

1. Un commit en `main` que solo agrega `api/tests/test_compuerta_ci.py` con `def test_compuerta(): assert False`.
2. En Railway, el despliegue de `api` y `worker` de ese commit queda **SKIPPED** y el anterior sigue activo.
3. `git revert` de ese commit: el CI vuelve a verde y se despliega normal (sin cambios de código).
4. Un commit que solo toca `docs/`: ni `api` ni `worker` ni `portal` crean despliegue.

Anotar fecha y resultado en la bitácora de operaciones. Es parte del criterio de listo de la mensualidad de operación.

### Recomendado: proteger `main` en GitHub

Settings → Branches → regla para `main`: "Require status checks to pass" con los seis trabajos del CI (en un PR,
*Dependencias* también bloquea). Así un PR rojo no se puede fusionar. Si alguien empuja directo a `main`, la compuerta
de Railway sigue protegiendo producción.

## 4. Ventanas, orden y migraciones

**Ventanas de despliegue:** fuera de **07:00–09:00** y de **20:45–21:15** hora de Costa Rica (tareas del beat:
recordatorios, cierre de caja, sincronizaciones). Tampoco entre **03:00 y 03:45** (respaldo diario).

**Antes de un despliegue con migración** (el CMD del api corre `alembic upgrade head` al arrancar y volver a la
imagen anterior **no** baja el esquema):

1. Respaldo a demanda: en el servicio `respaldo`, `railway cron run` (o "Run now" en el panel) y ver
   `OK respaldo diario ...` en el log. Si no está configurado todavía: `railway ssh --service api` →
   `python -m app.tools.backup` (ver `infra/RESPALDO.md` §3).
2. Fusionar a `main`. El `api` migra al arrancar; el `worker` usa la misma base.
3. **Orden api → worker:** Railway despliega los dos a la vez. Las migraciones son *expand/contract* (el código
   viejo funciona con el esquema nuevo), pero el worker nuevo puede arrancar antes de que el api termine de migrar y
   fallar unas tareas. Si la migración agrega algo que el worker usa desde el primer minuto: esperar a que el `api`
   quede *Active*, y en el `worker` hacer **Redeploy** del último despliegue.
4. Revisar `GET /health` y los logs de ambos.

**Volver atrás:** Railway → servicio → Deployments → el despliegue anterior → **Redeploy**. Si hubo migración, el
esquema queda adelante: se prefiere corregir hacia adelante. Un `alembic downgrade -1` solo con respaldo recién
hecho y si la migración no borra datos.

## 5. Saltarse el CI ante una urgencia

Solo cuando producción está caída o perdiendo dinero y el CI está rojo por algo ajeno al arreglo (o no da tiempo).

- **Quién:** solo el responsable técnico de la plataforma (administrador del proyecto en Railway), con aviso previo
  al administrador de Crimson.
- **Cómo:** en el servicio afectado, apagar "Wait for CI", desplegar (Deployments → **Deploy Latest Commit**) y
  **volver a encenderlo** apenas el despliegue quede activo.
- **Constancia:** un issue en GitHub con la etiqueta `despliegue-urgente`: commit, motivo, quién, hora, y el enlace al
  PR que deja el CI en verde (a más tardar en 24 h). Nunca para un cambio de dinero sin pruebas.
