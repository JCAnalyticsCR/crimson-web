# Despliegue seguro (CI + Railway)

Cómo llega un cambio a producción sin que una prueba rota, una migración con deriva o un `nginx.conf` con error
tumbe la plataforma. Complementa `infra/RESPALDO.md` (respaldos) y el runbook de Hacienda en `api/README.md`.

> Lo de Railway (secciones 3 y 4) se configura **a mano, una vez**, en el panel. El repo no trae archivos de
> configuración de Railway: *Config as Code* está obsoleto y no aplica a servicios nuevos.

---

## 1. El camino de un cambio

```
push a main ──► GitHub Actions "CI" (8 trabajos; la compra real espera a los de la API y el portal; < 10 min)
                   │  verde  ──► Railway despliega SOLO los servicios cuyas rutas vigiladas cambiaron
                   │  rojo   ──► Railway marca el despliegue SKIPPED: producción sigue con la versión anterior
```

- Cada ítem va en su rama y su PR; el CI corre en cada push de cualquier rama y en cada PR, así que el rojo se ve
  **antes** de fusionar.
- **Un cambio de dinero por despliegue.** Si algo sale mal, se sabe cuál fue.

## 2. Qué revisa el CI (`.github/workflows/ci.yml`)

| Trabajo | Qué hace | Falla si… |
|---|---|---|
| API · ruff + pytest | `uv sync --locked`, `ruff check app scripts ../worker`, `pytest -n auto` (en paralelo, Argon2 barato solo en pruebas, reloj de Costa Rica hasta los ítems 2.1 y 3.4) | el lock no coincide con `pyproject.toml`, hay errores de lint o falla una prueba |
| Migraciones | Postgres 16 vacía: una sola cabeza de Alembic, `alembic upgrade head`, `alembic check`, la última migración se revierte y se vuelve a aplicar, y al final las carreras de inventario contra Postgres real (`SELECT … FOR UPDATE`) | hay dos cabezas, una migración no corre en Postgres, el modelo y las migraciones no coinciden, el `downgrade` no sirve, o dos pedidos pueden llevarse la última unidad |
| Portal · tsc + vite build | `npm ci`, `tsc -b`, `vite build` | errores de tipos o de build |
| Tienda · compra real en local | Después de los trabajos de la API y del portal: Playwright levanta el API (migraciones + semilla ficticia + uvicorn, sobre una Postgres de servicio) y el portal, y compra por SINPE a 375 y 768 px, con puntero y solo con teclado (`portal/e2e/README.md`, «Compra real en local»). Sin secretos y sin salir de la máquina | no se puede completar una compra: ficha, carrito, los cuatro pasos del checkout, pedido creado con el total correcto, existencia apartada o «Mi pedido» con las instrucciones de pago |
| Portal · imagen + nginx -t | `docker build portal` (el Dockerfile arma `nginx.conf` con `envsubst` y corre `nginx -t`) | `nginx.conf` o el Dockerfile del portal tienen un error |
| Respaldo · imagen + ciclo completo | construye `infra/respaldo/Dockerfile` y, dentro de la imagen, con `age` y `rclone` reales y nada real (`infra/respaldo/ciclo_ci.sh`): base con la semilla y un valor de cada tipo (binario, decimales, JSON, fechas con zona) → respaldo diario (verificado restaurando en una SQLite desechable) → bucket local → prueba mensual (bajar, hashes, descifrar) → `restore` en una base vacía. Dos veces: todo en SQLite, y **como producción**: desde una Postgres 16 con el rol de solo lectura de `RESPALDO.md` 4.3 (Postgres → SQLite en la verificación del cron) y con el `restore` final en otra Postgres vacía, como la prueba mensual (Postgres → Postgres) | la imagen no construye, el rol de solo lectura no alcanza para respaldar, la verificación del cron no da los mismos hashes al pasar de Postgres a SQLite, o cualquier paso del respaldo o de su restauración falla con las herramientas de verdad |
| Dependencias · pip-audit + npm audit | vulnerabilidades conocidas en `uv.lock` y altas en `portal/package-lock.json` (sin las de desarrollo) | **en un PR**, si hay alguna que no esté en la lista de excepciones. En un push solo informa (el paso queda en rojo y el trabajo en verde) |

Lo mismo en local, antes de subir:

```bash
cd api && uv sync --locked && uv run ruff check app scripts ../worker && TZ=America/Costa_Rica uv run pytest -n auto -q
cd api && DATABASE_URL=postgresql+psycopg://crimson:crimson_local_only@localhost:5432/crimson \
          uv run alembic upgrade head && uv run alembic check      # con infra/docker-compose.yml levantado
cd portal && npm ci && npx tsc -b && npx vite build
docker build -t crimson-portal:local portal                         # nginx -t
docker build -f infra/respaldo/Dockerfile -t crimson-respaldo:local .  # imagen del cron de respaldo
docker run --rm -v "$PWD/infra/respaldo/ciclo_ci.sh:/ciclo_ci.sh:ro" crimson-respaldo:local sh /ciclo_ci.sh
```

Las pruebas del CI corren con `TZ=America/Costa_Rica`, **no** con el reloj de producción (Railway corre en UTC), y
así se quedan hasta los ítems 2.1 y 3.4. La app todavía mezcla `date.today()` (en producción, la fecha UTC) con la
fecha de Costa Rica (`cr_now()`): son 88 usos y el plan los corrige en 2.1 (fechas de dinero y fiscales, con
`cr_today()`) y en 3.4 (el resto, con la regla DTZ de ruff), no antes. Con `TZ=UTC` el CI quedaba en rojo según la
**hora del push**: de 18:00 a 24:00 CR fallaban pruebas que comparan contra la fecha CR, y el último día de cada mes
(y el 31 de diciembre) fallaban además las de comisiones de *Inicio*, reportes y backfill, que cortan por mes CR
mientras `earned_on` sale de `date.today()`. Un rojo que depende de la hora no detecta nada: bloquea despliegues
(Wait for CI) y enseña a saltarse el CI.

El borde de las 18:00-24:00 CR (sección 2, regla 8 del plan) se prueba **a propósito**, no con la suerte de la hora:

- Una prueba nueva toma "hoy" de donde lo toma el código que prueba: `cr_now().date()` si el endpoint corta en la
  fecha CR (backfill, *Mis números*, reportes por mes), nunca `date.today()` "porque en mi máquina da lo mismo".
- Las de dinero fijan la hora del borde (un `ahora`/`now` explícito o `monkeypatch` sobre la función de la fecha).
- Para ver a cualquier hora en local cómo se comporta con el reloj de producción ya en el borde:
  `TZ=XXX-14 uv run pytest -q tests/test_x.py` (desde las 04:00 CR la fecha local ya es la de mañana, como en
  producción después de las 18:00 CR; funciona igual en Windows y en Linux). El fin de mes no se ve así: hay que
  correrlo un último día de mes de noche.

Errores conocidos de este tipo, **de la app** (los corrige 2.1): `commissions.py` fecha con `date.today()` los
movimientos creados "ahora" (`earned_on`, líneas ~874, ~1348 y ~1413), y *Mis números*, *Inicio* y
`/reports/comisiones` cortan en la fecha CR. `test_ganancias_aliados.py::test_ajuste_del_pago_acordado_no_resta_dos_veces`
queda marcada `xfail` (`strict`) para cuando el reloj del CI esté en el borde: con el reloj de Costa Rica no aplica.

**Al cerrar 2.1 y 3.4:** volver a `TZ: UTC` en `.github/workflows/ci.yml` (y aquí), correr la suite con `TZ=XXX-14`
y quitar la marca `xfail` que sobre.

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

Para los **secretos** de la prueba mensual de restauración proteger `main` no alcanza: el environment
`respaldo-prueba` exige *Required reviewers* antes de activarla (`infra/RESPALDO.md` 4.4).

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

## 6. Tienda: humo después de cada despliegue y cómo volver atrás

Vale para todo lo que sale del plan de la tienda (olas 0 a 4). Cada tarea se publica sola y es reversible; esta
sección dice qué se corre al terminar, cuándo se revierte, cómo y quién lo decide.

### 6.1 Guion de humo (`scripts/humo_tienda.sh`)

Se corre **una vez**, cuando los servicios desplegados quedan *Active* en Railway, desde cualquier máquina con
`bash` y `curl` (Git Bash en Windows sirve):

```bash
bash scripts/humo_tienda.sh                      # https://tienda.crimsoncr.com, slug crimson
```

Solo hace GET (9 peticiones; el guion se corta solo si pasara de 12), cada una con 8 s de límite, y tiene que
terminar en menos de 30 s. Sale con **0** si todo está bien y con **1** si algo falla; cada línea dice `OK`,
`AVISO` o `FALLA` y qué vio. Revisa: portada 200 con el `<title>` esperado; `robots.txt` con la línea `Sitemap`;
el sitemap y su cantidad de fichas `/p/` frente al `n` del snapshot (±5); `/api/public/store/crimson` 200 en JSON
y con `X-Robots-Tag: noindex`; una ficha y una categoría del sitemap (200, indexables y con canónica a sí mismas);
`/catalogo`; y una ficha inexistente con 404 real.

Variables: `TIENDA_URL` (o el primer argumento), `TIENDA_SLUG`, `HUMO_TITULO` (texto que el `<title>` de la
portada contiene; por defecto `Crimson`), `HUMO_TOLERANCIA` (5), `HUMO_EXIGIR_NOINDEX` (1; en 0 la falta de
noindex en el JSON solo avisa: sirve únicamente mientras O0-05a no esté publicado) y `HUMO_HOST`.

- **Fallas conocidas hoy (10/11/2026, única corrida contra producción, 9 GET en 11 s):** el sitemap trae 2 184
  fichas y el snapshot `n=726` (el sitemap incluye los agotados ocultos; lo corrige O1-07 «Sitemap fiel al catálogo
  visible»), y el JSON del API aún no lleva `noindex` (llega con O0-05a). Hasta que esas dos tareas estén
  publicadas se corre con `HUMO_EXIGIR_NOINDEX=0 HUMO_TOLERANCIA=1500` y se compara la línea del sitemap con estos
  números: si cambian mucho, es una falla nueva. Al publicar cada una, se quita su variable.
- **Probar el propio guion** (tiene que salir con 1): `TIENDA_SLUG=crimsom bash scripts/humo_tienda.sh`.
- **En local, contra nginx de verdad** (necesita Docker): con el entorno de «Compra real en local» levantado
  (`infra/docker-compose.e2e.yml`, ver `portal/e2e/README.md`),
  `TIENDA_SLUG=<slug que imprime la semilla> HUMO_TITULO=Ficticia bash scripts/humo_tienda.sh http://tienda.localhost:5301`.
- **Sin Docker** no hay nginx local: el `nginx -t` de `portal/nginx.conf` lo hace el CI (trabajo «Portal · imagen
  + nginx -t»), y el guion se prueba contra producción después de publicar, una sola vez.
- No reemplaza al monitor (`scripts/monitor/tienda_check.py`, cada 30 min en GitHub Actions): el monitor vigila
  que la tienda siga viva; el humo confirma que **este** despliegue no rompió nada.

### 6.2 Cuándo se vuelve atrás

| Resultado del humo | Qué se hace |
|---|---|
| Todo `OK` | Nada. Se anota en el PR: «humo OK, N s». |
| `FALLA` en portada, `/catalogo`, ficha, categoría, JSON del API o la ficha inexistente da 200 | **Se revierte ya** (6.3), sin esperar a entender la causa. Se investiga con producción en la versión anterior. |
| `FALLA` solo por sitemap frente a snapshot (fuera de ±5) | El worker puede estar rearmando el snapshot: se espera hasta 10 min y se corre el humo **una** vez más. Si sigue, se revierte. |
| `FALLA` solo por tiempo (≥ 30 s) | Se repite una vez. Si sigue lento y el despliegue tocó consultas o caché, se revierte; si no, se abre un issue. |
| Despliegue con **cambio de dinero** (precio, IVA, envío, total, pagos) | Además del humo, se compara el total de un carrito de prueba en local (`npm run e2e:compra`) con lo esperado antes de publicar. Ante cualquier `FALLA` o un total distinto en producción, se revierte de inmediato y se revisan los pedidos creados en la ventana. |

### 6.3 Cómo se revierte, según el tipo de tarea

- **Solo portal** (React, CSS, `nginx.conf`): Railway → servicio `portal` → Deployments → el despliegue anterior →
  **Redeploy** (vuelve en un minuto). Después, `git revert <commit>` en un PR para que `main` quede igual a lo que
  corre; el CI de ese PR también tiene que estar en verde.
- **API o worker sin migración**: lo mismo en el servicio `api` (y en `worker` si el cambio lo tocó), y el
  `git revert` en un PR.
- **API con migración aditiva** (tabla o columna nueva): se revierte **el código** (Redeploy del anterior + `git
  revert` del commit de código). **La migración no se baja**: es aditiva, el código anterior funciona con ella y
  queda sin usar hasta el arreglo. Nunca `alembic downgrade` para salir de un apuro (sección 4).
- **Cambio de contrato del API** (campo nuevo en un JSON): como todo cambio es aditivo y el navegador tolera que el
  campo falte, se puede revertir el API sin revertir el portal, y al revés.
- **Cambios de datos por lote** (nombres, fichas técnicas, categorías de la ola 3): no se revierten con git; se usa
  el «deshacer» del lote (O3-11) desde el panel.
- **Ajustes de la tienda guardados desde el panel**: se vuelven a dejar como estaban desde el panel; no requieren
  despliegue.

### 6.4 Quién decide

- Quien despliega corre el humo y **revierte sin pedir permiso** cuando la tabla 6.2 lo dice: volver a la versión
  anterior no cambia datos ni precios.
- Avisa al responsable técnico de la plataforma y al administrador de Crimson: qué se publicó, qué falló (pegar la
  salida del guion) y que ya está revertido.
- Volver a publicar lo revertido lo decide el responsable técnico, con el arreglo y el CI en verde. Si el cambio era
  de dinero, además con el visto bueno del administrador de Crimson.
