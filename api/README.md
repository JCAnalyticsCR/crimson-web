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
uv run pytest -q                         # 20 tests: totales v4.4, consecutivos, auth, flujo, roles, PDF/correo, inventario, e-invoice+NC, reportes/Excel, ajustes, recurrencias, webhook ONVO
```

Portal: `cd portal && npm install && npm run dev` → http://localhost:5173 (proxy `/api` → `:8000`).
Todo junto con Docker: `docker compose -f infra/docker-compose.yml up --build`.

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
3. Mi Tienda (constructor de bloques, catálogo público, carrito, envíos) · eventos/tickets · POS · API pública con credenciales.
4. Planillas/empleados, facturas de compra manuales, cupones, opciones de producto.
