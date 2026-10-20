# Crimson API · plataforma de facturación, cobros y control financiero

Fase 0 (fundaciones) + primer tramo del eje comercial (Fase 1). Blueprint: `../PLAN_PLATAFORMA.md`.

## Correr en local

```bash
cd api
uv sync                                  # crea .venv con versiones fijas (uv.lock)
cp ../.env.example ../.env               # ajustar JWT_SECRET; con SQLite no hace falta Postgres
uv run alembic upgrade head              # migraciones
uv run python -m app.seeds --admin-email tu@correo.com --admin-password '<fuerte>'
uv run uvicorn app.main:app --reload     # http://127.0.0.1:8000/docs
uv run pytest -q                         # 25 tests: totales v4.4, consecutivos, auth, flujo, roles, PDF/correo, inventario, e-invoice+NC, reportes/Excel, ajustes, recurrencias, webhook ONVO, tienda+checkout, órdenes→tiquete, recepción XML, búsqueda, API pública
```

Portal: `cd portal && npm install && npm run dev` → http://localhost:5173 (proxy `/api` → `:8000`).
Todo junto con Docker: `docker compose -f infra/docker-compose.yml up --build`.

## Staging en Railway

Proyecto `crimson-plataforma` (entorno `production` de Railway, datos de prueba): servicios `api` (raíz `/api`), `portal` (raíz `/portal`, nginx con proxy `/api` a la URL pública de la API), `worker` (raíz del repo, `RAILWAY_DOCKERFILE_PATH=worker/Dockerfile`), Postgres y Redis. Cada push a `main` redespliega.

- Portal: https://portal-production-4312.up.railway.app · API: https://api-production-f07a.up.railway.app (`/health`, `/docs`).
- Arranque sin contraseñas: con la base vacía y `BOOTSTRAP_ADMIN_EMAIL` definido, `python -m app.bootstrap` (corre en cada arranque) emite una invitación de admin de un solo uso y la escribe en el log de `api`. Cada reinicio sin usuarios la reemplaza; una vez aceptada, queda inerte.
- `JWT_SECRET` se generó dentro del comando de la CLI y nunca se mostró. Rotarlo cierra todas las sesiones y vuelve ilegibles los secretos de pasarelas guardados (Fernet derivado).
- Migraciones probadas en Postgres real: `uv run --with pgserver ...` o `TEST_DATABASE_URL=postgresql://... uv run pytest` corre la suite completa contra Postgres.

## Qué hay

| Módulo | Endpoints | Notas |
|---|---|---|
| core | `/health`, `/tenant`, `/fx`, `/fx/today` | multi-tenant, tipo de cambio BCCR (worker) o manual |
| auth | `/auth/login`, `/refresh`, `/logout`, `/me`, `/password`, `/2fa/setup`, `/2fa/verify`, `/roles`, `/auth/password-reset/{check\|confirm\|request}` | Argon2, JWT 15 min, refresh rotativo en cookie httpOnly, bloqueo 6 intentos, TOTP · 2FA con códigos de recuperación (`/auth/2fa/{setup\|verify\|disable\|recovery-codes}`): el código de un solo uso entra en el mismo campo que el de la app · compuerta de contraseña temporal: el login responde 401 con `X-Password-Change: required` y la nueva va en `new_password` del mismo login · `session_version` en el token (`sv`): cerrar sesiones invalida los access tokens ya emitidos · enlaces de restablecer de un solo uso (token en el cuerpo, solo su SHA-256 en la BD) |
| catálogo | `/customers`, `/products`, `/taxes`, `/categories` | búsqueda + cursor ("Más resultados"), CABYS, impuestos por producto |
| ventas | `/documents/preview`, `/quotes[...]`, `/invoices[...]`, `/billing-groups` | totales v4.4 (5 decimales, prorrateo), consecutivo 20 dígitos, convertir/duplicar/anular/enviar |
| cobros | `/invoices/{id}/payments`, `/invoices/{id}/payment-link`, `/payments` | método ≠ tipo de transacción, saldo, enlace firmado + WhatsApp |
| público | `/public/pay/{token}` · `/onvo` · `/onvo/sync` | página de pago sin sesión; abre el Checkout de ONVO por el saldo y, al volver, confirma consultando a ONVO |
| dashboard | `/dashboard`, `/alerts` | KPIs hoy/mes/mes anterior + variación, recientes, acciones pendientes (incl. stock bajo) |
| documentos | `/{quotes|invoices}/{id}/html`, `/pdf`, `/email` | plantilla propia; PDF con WeasyPrint (Docker) o HTML imprimible; correo por Resend con outbox y BCC |
| factura electrónica | `/invoices/{id}/emit`, `/credit-note`, `/xml`, `/xml/{doc}/{document|response}` | adapter `sandbox` (clave 50 dígitos, XML v4.4 simulado); Alanube/GTI se enchufan en `app/providers/einvoice.py` |
| inventario | `/warehouses`, `/stock`, `/stock/movements`, `/stock/transfer` | ledger por ubicación; venta descuenta, anulación repone, alertas de mínimo |
| contabilidad | `/expenses`, `/expense-categories`, `/suppliers` | gastos con condición de IVA (crédito / no / prorrata) |
| reportes | `/reports`, `/reports/{key}?format=xlsx` | 19 reportes con Excel, incluidas rentabilidad por proyecto y por tipo de solución |
| recurrencias | `/recurrences`, `/recurrences/{id}/run` | plantilla + frecuencia → factura (worker diario) |
| ajustes | `/settings`, `/settings/bank-accounts`, `/billing-groups/{id}`, `/settings/users`, `/settings/invitations[/accept\|/check]`, `/settings/invitations/{id}/resend\|revoke`, `/settings/users/{id}/reset-password`, `/settings/users/{id}/sessions/revoke`, `/settings/gateways`, `/settings/outbox` | vigencias, mensajes, BCC, métodos manuales, consecutivos, roles, invitaciones (reenviar rota el token, revocar, sin duplicadas; invitar como admin o reenviar esa invitación solo lo hace un admin y pide su contraseña; aceptar con una cuenta que ya existe pide la contraseña de esa cuenta), usuarios (`routers/users_admin.py`: editar nombre/correo/rol, restablecer contraseña por enlace o temporal con la contraseña del admin, cerrar sesiones, desactivar; las contraseñas no se ven nunca; soporte y aliados se gestionan en sus pantallas), pasarelas (secreto cifrado) · los correos sensibles (invitaciones, restablecer, avisos de seguridad) no van al BCC y su enlace se borra del outbox |
| webhooks | `/webhooks/onvo/{tenant_id}` | header `X-Webhook-Secret`; cada pago se vuelve a consultar a la API de ONVO antes de aplicarse; idempotente por `<type>:<data.id>` y por intención de pago; lo no confirmado responde 503 sin consumirse |
| Mi Tienda | `/store`, `/store/pages[/{id}]`, `/coupons` | personalización (colores, fuente, tipo catálogo/tienda, dominio, envíos), páginas por bloques JSON |
| tienda pública | `/public/store/{slug}[/pages/{p}\|/products[/{id}]\|/quote\|/checkout]` | catálogo, ficha con relacionados, carrito, cupón, envío; total = factura al céntimo |
| órdenes | `/orders`, `/orders/{id}` (estado), `/orders/{id}/invoice?doc_type=TE\|FE` | pedido → tiquete o factura (crea cliente si trae correo) y descuenta inventario |
| recepción | `/reception`, `/reception/upload`, `/reception/{id}/respond`, `/reception/{id}/xml/{document\|response}` | XML de proveedor → aceptar/parcial/rechazar (MensajeReceptor) → gasto con IVA acreditable |
| búsqueda | `/search?q=` | facturas, cotizaciones, clientes (+ sus facturas), productos, órdenes |
| API pública | `/settings/api-credentials`, `/v1/{customers,products,inventory,invoices,payment-links,checkout}` | kid + secret (hash), checkout por JWT HS256 con `kid`, webhooks salientes HMAC |
| multimedia | `/media` (subir, listar, borrar), `/media/f/{key}` (público) | tipo validado por firma de bytes, sin SVG, 5 MB imagen / 10 MB PDF, llave aleatoria en la URL |
| ficha de cliente | `/customers/{id}/overview`, `/contacts`, `/notes`, `DELETE /customers/{id}` (archiva) | KPIs + línea de tiempo con cotizaciones, facturas, pagos, pedidos y notas |
| variantes | `/products/{id}/variants` (lista completa), `/products/{id}/link` | código y precio propio; se usan en tienda, carrito y POS |
| POS | `/pos/catalog`, `/pos/sale` | tiquete o factura + pagos mixtos + vuelto + inventario + emisión en un paso |
| planillas | `/payroll/employees`, `/payroll/runs[/{id}/approve\|pay\|lines/{l}\|slip/{l}]`, `/payroll/settings` | CCSS obrero/patronal, renta por tramos con créditos, provisiones; al pagar crea el gasto |
| conciliación | `/banking/accounts`, `/banking/{cuenta}/import\|auto\|lines`, `/banking/lines/{id}/candidates\|match\|unmatch\|ignore\|expense` | estado de cuenta CSV/Excel, casado automático sin ambigüedades, gasto desde débito |
| eventos | `/events[/{id}[/tickets]]`, `/events/checkin`, `/tickets/{id}/void`, `/public/events/{slug}[/{evento}[/checkout]]`, `/public/tickets/{code}` | entradas con QR, se activan al marcar pagada la orden, una sola entrada por código |
| importador | `/import/{customers\|products\|suppliers\|invoices\|catalogo}[?commit=true]`, `/import/{tipo}/template` | vista previa y aplicación; migración desde Fygaro; `catalogo` = lista de precios de proveedor (costo + margen → precio) |
| oportunidades | `/opportunities[/board\|/{id}[/touch]\|/meta/config]` | embudo por estado con monto ponderado, seguimiento con fecha, bitácora |
| levantamientos | `/field/specs`, `/field/technicians`, `/surveys[/{id}[/suggest\|send\|costing\|quote]]` | formulario por tipo de solución, sugerencia de materiales, costeo (solo con `catalog.precios`) y cotización en un clic |
| órdenes de trabajo | `/work-orders[/{id}[/{arrive\|start\|progress\|finish\|cancel}]]`, `/work-orders/meta/today` | pantalla del técnico; al finalizar descuenta el material usado una sola vez |
| proyectos | `/projects[/{id}[/requirements\|purchase-request\|report\|invoice]]`, `/quotes/{id}/project` | cotización → proyecto con su primera orden; rentabilidad real e informe de entrega |
| activos | `/assets[/{id}]?expiring=` | equipo instalado con serie, ubicación y garantía (aviso 45 días antes) |
| compras | `/purchase-requests[/{id}]` | desde el proyecto, por stock bajo (worker) o a mano; al recibir entra a bodega con su costo |
| disponibilidad | `PATCH /products/{id}/web`, `/products/{id}/availability`, `/products/meta/filters` | interruptor de publicación en la tienda, existencias propias + del proveedor y los filtros del catálogo |
| soporte al cliente | `/tickets[/{id}[/notes\|/work-order]]`, `/tickets/meta/config` | casos con SLA de primera respuesta, nivel 1-3, horas y si se cobra; el ticket genera la orden de trabajo |
| mantenimientos | `/contracts[/{id}[/ticket]]` | cada cuántos meses toca; el worker abre el ticket y reprograma la próxima |
| comisiones | `/commissions*` (ver «Comisiones v2») | libro único de comisiones, bonos y pagos acordados a aliados; reglas por persona, aliado o proyecto; bandeja de aprobación |
| soporte | `/settings/support[/{id}/revoke\|log]` | acceso temporal de solo lectura con bitácora por request |
| bandeja XML | `/settings/inbox`, `/settings/inbox/run` | IMAP cada 15 min (worker); contraseña cifrada |
| contacto | `/public/store/{slug}/contact` | formulario del sitio → nota en la ficha + correo a administradores |

Roles: `admin`, `ventas`, `caja`, `inventario`, `contabilidad`, `lectura` — permisos por módulo × acción en `app/core/deps.py`.

Roles externos (nunca entran a un endpoint interno: `get_principal` los rechaza):
- Portal del cliente (`/cliente/*`, `routers/client_portal.py`): `cliente_admin`, `cliente_usuario`, ligados a un cliente (`tenant_user.customer_id`).
- Portal de aliados (`/aliado/*`, `routers/ally_portal.py`, puerta única `core/ally.py`): `aliado`, `tecnico_aliado`, ligados a una empresa aliada (`tenant_user.ally_company_id`). Solo ven lo que tienen EN COMÚN con la empresa (`services/ally_scope.py`, deny by default; lo ajeno responde 404) y salidas por lista blanca. Se invitan desde Administración → Aliados (`/allies/{id}/invitations`), no desde Ajustes. "Ver como este aliado" (solo admin): cabecera `X-View-As-Ally` (+ `X-View-As-User`), datos reales en solo lectura y con constancia en la bitácora. Precio de aliado: `services/ally_pricing.py` (recargo sobre el costo, precios fijos por producto). Mensualidad: recurrencia de factura (`/allies/{id}/plan`).

## Comisiones v2 (economía)
Motor en `services/commissions.py`, endpoints en `routers/commissions.py`. Libro único `commission` (comisión, bono, pago acordado, ajuste) para vendedores (`user_id`) y empresas aliadas (`ally_company_id`); reglas en `commission_rule` (gana la más específica: proyecto > persona/empresa > solución > tipo de negocio).
- **Eventos** (idempotentes por `source_key`): cobro confirmado (`captura` devenga proporcional sin IVA con tope por factura; `devolucion`/`reembolso` crean un ajuste negativo; autorizaciones y voids no hacen nada), cotización aceptada o convertida, proyecto completado (primera vez terminado/entregado/facturado: comisiones, bonos y pago acordado; cancelado revierte), cambio del precio aprobado del aliado y factura anulada. Una pagada nunca se toca: se corrige con un ajuste.
- **Defaults** (Comisiones → Configuración, `tenant.settings.commissions`): comisión sobre la venta sin IVA al cobrar; pago acordado del aliado (su precio aprobado) al completar el proyecto; aprobación manual; regla general de vendedores solo para el rol `ventas`. No se crean reglas con % solas.
- **Visibilidad**: el vendedor ve lo suyo (`/commissions/summary`, tarjeta en Inicio); base y % sobre utilidad solo para quien ve costos (el concepto tampoco lleva el %); "Mis números", el acuerdo económico y las ganancias de aliados (`/commissions/earnings*`, `/commissions/allies`: traen sus precios) exigen ver costos. El monto del pago acordado al aliado lo ven quien ve costos y quien lo aprueba y paga (contabilidad); lectura ve el libro sin esos montos. Nadie aprueba ni paga lo suyo salvo el admin. El aliado ve su dinero solo en su portal (`/aliado/ganancias*`, 24 meses), y solo de las participaciones que Crimson le comparte (`visible_to_ally`).
- **Aplicar reglas a ventas anteriores**: `POST /commissions/backfill {"from": "2026-10-01", "dry_run": true}` simula; con `dry_run: false` crea (repetirlo no duplica).

## Reglas que el código ya respeta (plan §5 y §14)
- `tenant_id` en toda tabla de negocio; documentos con `ON DELETE RESTRICT`.
- Consecutivos bajo `SELECT … FOR UPDATE` (Postgres); nunca se saltan ni reutilizan.
- Notas internas nunca salen del sistema; externas van al PDF/correo.
- Factura con pagos o emitida no se edita; anular emitida = nota de crédito (en emisión directa la factura se anula cuando Hacienda acepta la NC).
- Pagos en línea (ONVO: Checkout alojado) se aplican solo si la API de ONVO confirma el pago, por webhook o al volver el comprador; nunca por el regreso del navegador solo. Nunca se guardan datos de tarjeta. Llaves en Ajustes → Pagos → Pasarelas (cifradas por empresa); en producción solo `onvo_live_`.
- Logs sin Authorization ni cuerpos; CORS por lista; cookies httpOnly + SameSite.

### Acceso seguro (plan 1.10)

- **IP real**: el nginx del portal toma la IP del cliente (realip sobre `X-Forwarded-For`, solo desde redes internas) y la manda al API en `X-Crimson-Client-IP` con `X-Crimson-Proxy-Key`. El API la usa solo si la llave coincide (`core/clientip.py`); si no, la IP de la conexión. uvicorn ya no corre con `--forwarded-allow-ips='*'` (`FORWARDED_ALLOW_IPS` = redes internas, en `api/Dockerfile`) ni con log de acceso (`--no-access-log`).
- **Variables**: `PROXY_SHARED_KEY` en **portal y api** (la misma; hex de 64 caracteres, `openssl rand -hex 32`; menos de 32 caracteres se ignora). Sin ella todo sigue funcionando, pero el panel ve a todos con la IP de la salida del portal. Sonda (`GET /auth/ip-check`, sin sesión: solo devuelve tu IP y una palabra). Antes de confiar en los límites: `curl -H 'X-Crimson-Client-IP: 198.51.100.5' -H 'X-Crimson-Proxy-Key: x' https://<api>/auth/ip-check` debe decir `"cabeceras": "rechazadas"` (el edge del API deja pasar las cabeceras propias y el API no les cree; `ninguna` = el edge las quita). Después de cargar la llave: `https://panel.crimsoncr.com/api/auth/ip-check` debe dar tu IP pública con `"firmadas"`; `rechazadas` = la llave no coincide entre portal y api. En Ajustes → Bitácora el login también muestra la IP real.
- **Login**: solo cuentan los fallidos. Por IP: 20 y después espera progresiva; por cuenta + IP: 5 y espera progresiva (30 s, 1, 2… hasta 15 min). La cuenta se bloquea 15 min solo con fallidos de 4+ IPs (cada IP aporta 3) y no para la IP donde la persona ya entró bien.
- **2FA**: cambiarlo con el 2FA activo exige contraseña y código actual; el anterior sigue activo hasta confirmar el nuevo; correo "Cambió tu verificación en dos pasos"; un código no se usa dos veces. Si el rol lo exige (en cualquiera de sus empresas) no se puede desactivar: se cambia de teléfono, o el admin lo quita al restablecer la contraseña. Obligatorio por rol (Ajustes → Usuarios; por defecto admin y contabilidad, 7 días de gracia): vencida la gracia, el panel interno responde 403 `X-2FA-Setup` y el portal muestra la configuración forzada.
- **CORS**: con cookies solo los `CORS_ORIGINS` (el panel); `LANDING_ORIGINS` sin cookies y solo en `/public/incidents` y `/public/access-requests`. Un origen que figure en las dos listas (o un `*` en `CORS_ORIGINS`) va sin cookies. `/auth/refresh` rechaza otro `Origin` (también la landing) o `Sec-Fetch-Site: cross-site`.
- **Tienda**: en `tienda.*` nginx solo deja pasar `/api/public/` y `/api/media/f/` (el resto, 404). HSTS de un año en el portal.

## Hacienda y recepción de comprobantes

Emisión directa con Hacienda (FE, TE y NC v4.4, firma XAdES-EPES) y recepción de los comprobantes de proveedores
(buzón IMAP o carga manual, mensaje receptor v4.4 firmado, gasto ligado). Código en `app/providers/hacienda/`,
`app/services/reception*.py`, `app/services/hacienda_*.py` y `app/routers/{reception,hacienda_settings,accounting}.py`.

**Nada sale a Hacienda por defecto.** Modos (Ajustes → Hacienda): *Desactivada* (no se emite; las respuestas a
proveedores se simulan), *Simulado* (todo se simula, con la marca "sin validez fiscal"), *Pruebas de Hacienda*
(ambiente de pruebas real) y *Producción*, que solo se activa con el interruptor del administrador (contraseña +
escribir PRODUCCIÓN + lista de verificación en verde) **y** solo envía si, en el momento de cada envío, el servidor
tiene `HACIENDA_PRODUCTION_ALLOWED=true`, `ENV=production` y la misma huella de instancia que cuando se activó
(Railway copia las variables al duplicar un ambiente; la huella no). Cualquier cambio de modo apaga producción.

### Regla de credenciales

El usuario y la contraseña de ATV, el certificado `.p12` y su PIN **solo** se cargan en Ajustes → Hacienda (o como
variables de Railway). Nunca por chat, correo ni repositorio, tampoco al equipo de desarrollo. Se guardan cifrados con
AES-256-GCM (llave `FISCAL_KEYS`) y nunca se vuelven a mostrar: la API devuelve "cargado · vence el …".

### Variables de entorno (todas opcionales)

| Variable | Uso |
|---|---|
| `FISCAL_KEYS` | `kid:llave` separadas por coma (llave = base64url de 32 bytes). La primera cifra; todas descifran (rotación). **La misma en `api` y `worker`** (variable compartida del proyecto), nunca en `respaldo`. Sin ella, fuera de `ENV=local`, guardar credenciales de Hacienda **o la contraseña del buzón** responde 503. Con ella, `app.bootstrap` pasa solo las contraseñas de buzón guardadas con el cifrado heredado (derivado de `JWT_SECRET`) a esta llave. Mal escrita: no tumba el arranque; queda un error en el log y todo lo fiscal sin llave (503). |
| `ENV`, `PUBLIC_BASE_URL` | `production` y la URL pública del portal, **iguales en `api` y `worker`** (variables compartidas): con `RAILWAY_ENVIRONMENT_ID` forman la huella de instancia, y el que envía y consulta a Hacienda es el worker. Los dos registran al arrancar `FISCAL_KEYS cargadas: … · huella de instancia …`: deben coincidir. |
| `HACIENDA_PRODUCTION_ALLOWED` | `true` en `api` **y** `worker` (variable compartida), solo en el servicio de producción y el día del cambio. |
| `HACIENDA_PROVEEDOR_SISTEMAS` | Cédula para `ProveedorSistemas`; vacío = la de la empresa (desarrollo propio). |
| `HACIENDA_ENV_TENANT` / `HACIENDA_ENVIRONMENT` | Empresa (slug) y ambiente (`pruebas`/`produccion`) de las credenciales de entorno. |
| `HACIENDA_USERNAME`, `HACIENDA_PASSWORD`, `HACIENDA_P12_B64`, `HACIENDA_P12_PIN` | Respaldo por entorno para una sola empresa (las de Ajustes mandan). |
| `RECEPTION_IMAP_HOST`, `RECEPTION_IMAP_PORT` (993), `RECEPTION_IMAP_USER`, `RECEPTION_IMAP_PASSWORD`, `RECEPTION_IMAP_FOLDER` (INBOX), `RECEPTION_IMAP_TENANT`, `RECEPTION_IMAP_SINCE_DAYS` (30) | Buzón de recepción. Sin host, usuario o contraseña: apagado (queda la configuración de Ajustes → XML de proveedores, que pide la contraseña del administrador al guardar la del buzón o cambiar servidor, puerto o usuario, y solo se conecta a direcciones públicas). |
| `ALLY_MEDIA_QUOTA_MB` | Tope de fotos y PDF por empresa aliada (defecto 1024). |
| `EINVOICE_PROVIDER` | Ya existía. `none` \| `sandbox` \| `alanube` \| `gti`; vacío o cualquier otro valor = `none` con un aviso en el log (antes un valor raro tumbaba el arranque). |

Una variable definida **sin valor** vale lo mismo que no tenerla (`env_ignore_empty`): no tumba `api`, `worker` ni
`alembic`. `HACIENDA_ENVIRONMENT` mal escrito queda en `pruebas` (nunca producción por error).

Generar `FISCAL_KEYS` **en la consola del servicio en Railway** (sin copiarla a ningún chat):

```bash
python -c "import os,base64;print('k'+__import__('datetime').date.today().strftime('%Y%m')+':'+base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Rotar: poner la llave nueva **al principio** de `FISCAL_KEYS` (dejando la vieja detrás), desplegar `api` y `worker`,
correr `python -m app.tools.rotate_fiscal_keys` (o `--dry-run`) y recién entonces quitar la vieja. El servicio cron
`respaldo` no recibe `FISCAL_KEYS` ni `HACIENDA_*` (`infra/RESPALDO.md`): el respaldo y la llave nunca están juntos.

Antes de desplegar la migración `f1c7a9d3e520`: `python -m app.tools.check_reception_dupes` (solo lectura) lista las
recepciones duplicadas por clave; las copias exactas sin respuesta se fusionan solas, el resto hay que resolverlo a mano.

### Desplegar la rama de la reunión sobre la base de producción

El contenedor que hoy corre en Railway no tiene las herramientas nuevas: los chequeos previos van en SQL (consola de
Postgres de Railway, solo lectura). Las 6 migraciones corren en **una** transacción con `lock_timeout` de 15 s: si
algo falla, la base queda en `c5d18e3f7a2b`, el contenedor nuevo no arranca y el viejo sigue sirviendo.

1. **Respaldo** inmediato (`pg_dump` o el cron `respaldo`): hay rellenos sin vuelta atrás (limpieza del costo/margen en
   `internal_notes`, token fuera del HTML de `email_outbox`, recepciones duplicadas exactas borradas, `aprobado` →
   `simulado`). En la consola del `api` actual, `alembic current` debe dar exactamente `c5d18e3f7a2b`.
2. **Chequeos** (deben dar vacío o lo esperado):
   ```sql
   -- recepciones duplicadas que la migracion no fusiona sola (vivas > 1 o xmls > 1: resolver a mano)
   SELECT tenant_id, clave, array_agg(id ORDER BY id), count(*) FILTER (WHERE action IS NOT NULL OR expense_id IS NOT NULL) AS vivas,
          count(DISTINCT md5(btrim(replace(replace(coalesce(xml_document, ''), E'\uFEFF', ''), E'\r\n', E'\n'), E' \t\r\n'))) AS xmls
     FROM received_document GROUP BY tenant_id, clave HAVING count(*) > 1;
   -- facturas "aceptadas" del proveedor sandbox de antes (quedan como simuladas; su NC se anula aqui, no va a Hacienda)
   SELECT count(*) FROM einvoice_document WHERE provider = 'sandbox' AND status = 'aceptada';
   -- recurrencias de gasto atrasadas: la primera corrida (05:00) genera hasta 12 periodos de cada una
   SELECT id, name, next_date FROM recurrence WHERE active AND kind = 'gasto' AND next_date < current_date;
   ```
   Si no se quieren los periodos atrasados de gasto, ajustar `next_date` antes.
3. **Variables**: revisar `EINVOICE_PROVIDER` en `api`, `worker` y `respaldo` (vacía o rara ya no tumba nada, pero
   queda `none`). Ninguna variable nueva es obligatoria para este despliegue.
4. **Orden**: desactivar el auto-deploy de `worker` y `respaldo` para este push (o redesplegarlos a mano después).
   Desplegar `api` y confirmar en su log la migración y `alembic current` = `f1c7a9d3e520`; recién entonces desplegar
   `worker` (y `respaldo`). Con el esquema viejo, 11 de las 20 tareas del worker nuevo fallan (recordatorios, SLA,
   mantenimientos, Hacienda) sin que nadie lo note. El `portal` puede ir con el `api`.
5. **Ventana** de poco uso: mientras migra, el api viejo espera los bloqueos (decenas de segundos).
6. **Volver atrás**: redesplegar la imagen anterior de `api` y `worker` **sin** `alembic downgrade` (el código de
   `main` funciona sobre el esquema nuevo; el downgrade no restaura los rellenos).

### Puesta en marcha (runbook)

1. En Railway: generar `FISCAL_KEYS` en la consola del servicio, sin copiarla a ningún chat, y definirla como
   **variable compartida** referenciada por `api` y `worker` (los dos registran al arrancar la misma huella de llave).
   Confirmar que `ENV=production` está definido en ambos (si falta, la API queda en `local` y producción nunca se
   habilita: falla segura, pero hay que saberlo). Dejar `HACIENDA_PRODUCTION_ALLOWED` sin definir. Si alguna vez se
   crea staging **duplicando** el ambiente de producción, borrar ahí `HACIENDA_PRODUCTION_ALLOWED` y poner `ENV=staging`.
2. El administrador entra a **Ajustes → Hacienda**: completa identificación, ubicación y actividades; en
   **Credenciales → Pruebas** carga el usuario y la contraseña **de pruebas** de ATV (son distintas a las de
   producción) y la llave criptográfica de pruebas con su PIN; "Probar conexión".
3. Modo **Pruebas**. Correr `python -m app.tools.hacienda_smoke --tenant <slug> --cabys <código> --confirmo-pruebas`
   para confirmar con el validador real: firma, ruta de estado del mensaje receptor (`{clave}-{consecutivo}`),
   clasificación servicio/mercancía y si hace falta la cadena del certificado.
4. Antes de producción: volver a bajar los XSD v4.4 (actualización del 1-nov-2026) a
   `app/providers/hacienda/xsd/esquemas/2024/v4.4/` y comparar; si cambiaron, ajustar y volver a correr las pruebas.
5. Con contabilidad: importar el histórico de recepciones de Fygaro (Importar datos → Recepciones de Fygaro, el zip de
   "Download All"); el importador muestra el último consecutivo de aceptación/parcial/rechazo de Fygaro (no lo aplica).
6. Crear el buzón `facturas@…` en el hosting de correo y cargar `RECEPTION_IMAP_*` en Railway (`worker` y `api`).
7. Día del cambio, **en este orden y el mismo día**: (a) en Fygaro se deja de emitir y de responder; (b) se importa de
   nuevo el histórico de recepciones de los últimos días y se baja de Fygaro el último FE/NC/TE emitido; (c) el
   administrador pone en **Consecutivos** el último número de Fygaro de cada tipo 01–07 y **Confirma consecutivos**
   (vale 24 h); (d) se cargan las credenciales y el `.p12` **de producción**; (e) se define
   `HACIENDA_PRODUCTION_ALLOWED=true` en Railway; (f) "Probar conexión" de producción; (g) **Activar producción**
   (contraseña + PRODUCCIÓN). Lo que llegó al buzón durante la convivencia y **no** se respondió en Fygaro se reabre
   uno por uno con "Reabrir para producción" dentro del plazo.
8. Primera semana: revisar a diario Contabilidad → Tributario (rechazos, plazos, crédito por confirmar) y los correos
   de alerta; confirmar con un cliente empresa que recibió el XML y la respuesta de Hacienda de su factura.

**Puntos a confirmar con el validador de pruebas** (marcados en el código): ruta de consulta del mensaje receptor,
clasificación servicio/mercancía por unidad de medida, totales gravados/exentos con tarifas 0 %, si hace falta la
cadena del certificado en la firma. **Fase 2**: ND, FEE, FEC, REP, exoneraciones, otros cargos en la emisión,
contingencia, callback, verificación de la firma del proveedor, catálogo de distritos y cédulas alfanuméricas en la
emisión.

## Pendiente (requiere credenciales o decisiones del cliente)

1. Hacienda: la conexión directa está lista (ver «Hacienda y recepción»); falta cargar las credenciales y el `.p12` de **pruebas**, correr `hacienda_smoke` contra el validador real y, el día del cambio, el runbook de arriba. Mientras tanto el modo por defecto no emite (o simula, sin validez fiscal).
2. ONVO: cobro con tarjeta (tienda), enlace de pago de facturas y reembolso desde el pedido ya están; falta probar de punta a punta con la llave de pruebas en staging y, después, tarjetas guardadas y reautorizaciones. PayPal pendiente.
3. BCCR: correo + token del servicio de indicadores para el tipo de cambio automático.
4. Resend: llave para que los correos salgan de verdad (hoy quedan en la bandeja de salida como `simulado`).
5. Dominio propio (app.crimsoncr.com) y SSL: requiere el CNAME en el DNS del cliente.
6. Tasas de planilla 2026: verificar tramos del impuesto al salario y cargas CCSS (valores por defecto: base 2025).
