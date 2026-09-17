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
uv run pytest -q                         # 13 tests: totales v4.4, consecutivos, auth, flujo completo, roles
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
| dashboard | `/dashboard` | KPIs hoy/mes/mes anterior + variación, recientes, acciones pendientes |

Roles: `admin`, `ventas`, `caja`, `inventario`, `contabilidad`, `lectura` — permisos por módulo × acción en `app/core/deps.py`.

## Reglas que el código ya respeta (plan §5 y §14)
- `tenant_id` en toda tabla de negocio; documentos con `ON DELETE RESTRICT`.
- Consecutivos bajo `SELECT … FOR UPDATE` (Postgres); nunca se saltan ni reutilizan.
- Notas internas nunca salen del sistema; externas van al PDF/correo.
- Factura con pagos o emitida no se edita; anular emitida = nota de crédito (Fase 2).
- Pagos en línea se confirmarán solo por webhook (adapter ONVO, Fase 3). Nunca se guardan datos de tarjeta.
- Logs sin Authorization ni cuerpos; CORS por lista; cookies httpOnly + SameSite.

## Siguiente sesión (plan §11)
1. Fase 1.2: PDF (WeasyPrint) + correo transaccional para cotización/factura.
2. Fase 1.4: ajustes de facturación (vigencias, mensajes, BCC), reportes básicos con Excel.
3. Fase 2: adapter fiscal (Alanube/GTI) → emisión FE/TE/NC y recepción.
