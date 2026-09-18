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
| auth | `/auth/login`, `/refresh`, `/logout`, `/me`, `/password`, `/2fa/setup`, `/2fa/verify`, `/roles` | Argon2, JWT 15 min, refresh rotativo en cookie httpOnly, bloqueo 6 intentos, TOTP |
| catálogo | `/customers`, `/products`, `/taxes`, `/categories` | búsqueda + cursor ("Más resultados"), CABYS, impuestos por producto |
| ventas | `/documents/preview`, `/quotes[...]`, `/invoices[...]`, `/billing-groups` | totales v4.4 (5 decimales, prorrateo), consecutivo 20 dígitos, convertir/duplicar/anular/enviar |
| cobros | `/invoices/{id}/payments`, `/invoices/{id}/payment-link`, `/payments` | método ≠ tipo de transacción, saldo, enlace firmado + WhatsApp |
| público | `/public/pay/{token}` | página de pago sin sesión (ONVO en Fase 3) |
| dashboard | `/dashboard`, `/alerts` | KPIs hoy/mes/mes anterior + variación, recientes, acciones pendientes (incl. stock bajo) |
| documentos | `/{quotes|invoices}/{id}/html`, `/pdf`, `/email` | plantilla propia; PDF con WeasyPrint (Docker) o HTML imprimible; correo por Resend con outbox y BCC |
| factura electrónica | `/invoices/{id}/emit`, `/credit-note`, `/xml`, `/xml/{doc}/{document|response}` | adapter `sandbox` (clave 50 dígitos, XML v4.4 simulado); Alanube/GTI se enchufan en `app/providers/einvoice.py` |
| inventario | `/warehouses`, `/stock`, `/stock/movements`, `/stock/transfer` | ledger por ubicación; venta descuenta, anulación repone, alertas de mínimo |
| contabilidad | `/expenses`, `/expense-categories`, `/suppliers` | gastos con condición de IVA (crédito / no / prorrata) |
| reportes | `/reports`, `/reports/{key}?format=xlsx` | facturación, pendientes, impuesto, resultados, cierre, transacciones, gastos, IVA, inventario, productos, movimientos |
| recurrencias | `/recurrences`, `/recurrences/{id}/run` | plantilla + frecuencia → factura (worker diario) |
| ajustes | `/settings`, `/settings/bank-accounts`, `/billing-groups/{id}`, `/settings/users`, `/settings/invitations[/accept]`, `/settings/gateways`, `/settings/outbox` | vigencias, mensajes, BCC, métodos manuales, consecutivos, roles, invitaciones, pasarelas (secreto cifrado) |
| webhooks | `/webhooks/onvo/{tenant_id}` | firma HMAC `t=..,v1=..` (300 s), idempotente por evento, aplica pago/reembolso |
| Mi Tienda | `/store`, `/store/pages[/{id}]`, `/coupons` | personalización (colores, fuente, tipo catálogo/tienda, dominio, envíos), páginas por bloques JSON |
| tienda pública | `/public/store/{slug}[/pages/{p}\|/products[/{id}]\|/quote\|/checkout]` | catálogo, ficha con relacionados, carrito, cupón, envío; total = factura al céntimo |
| órdenes | `/orders`, `/orders/{id}` (estado), `/orders/{id}/invoice?doc_type=TE\|FE` | pedido → tiquete o factura (crea cliente si trae correo) y descuenta inventario |
| recepción | `/reception`, `/reception/upload`, `/reception/{id}/respond`, `/reception/{id}/xml/{document\|response}` | XML de proveedor → aceptar/parcial/rechazar (MensajeReceptor) → gasto con IVA acreditable |
| búsqueda | `/search?q=` | facturas, cotizaciones, clientes (+ sus facturas), productos, órdenes |
| API pública | `/settings/api-credentials`, `/v1/{customers,products,inventory,invoices,payment-links,checkout}` | kid + secret (hash), checkout por JWT HS256 con `kid`, webhooks salientes HMAC |

Roles: `admin`, `ventas`, `caja`, `inventario`, `contabilidad`, `lectura` — permisos por módulo × acción en `app/core/deps.py`.

## Reglas que el código ya respeta (plan §5 y §14)
- `tenant_id` en toda tabla de negocio; documentos con `ON DELETE RESTRICT`.
- Consecutivos bajo `SELECT … FOR UPDATE` (Postgres); nunca se saltan ni reutilizan.
- Notas internas nunca salen del sistema; externas van al PDF/correo.
- Factura con pagos o emitida no se edita; anular emitida = nota de crédito (Fase 2).
- Pagos en línea se confirmarán solo por webhook (adapter ONVO, Fase 3). Nunca se guardan datos de tarjeta.
- Logs sin Authorization ni cuerpos; CORS por lista; cookies httpOnly + SameSite.

## Pendiente (requiere credenciales o es Fase 4-5)
1. Adapter real Alanube/GTI (sandbox del proveedor) y recepción de XML de compras (bandeja IMAP).
2. ONVO: llamadas reales de Payment Intent/checkout (el adapter y el webhook ya están; falta la clave secreta de ONVO).
3. Eventos/tickets con QR · POS web · dominio propio con SSL para la tienda (CNAME) · carga de imágenes (bucket).
4. Planillas/empleados, opciones/variantes de producto, acceso de soporte auditado, D151, conciliación bancaria automática.
