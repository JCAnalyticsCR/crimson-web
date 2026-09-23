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
| reportes | `/reports`, `/reports/{key}?format=xlsx` | facturación, pendientes, impuesto, resultados, cierre, transacciones, gastos, IVA, inventario, productos, movimientos, órdenes, recepciones, propinas, D-151, planilla, conciliación, rentabilidad por proyecto |
| recurrencias | `/recurrences`, `/recurrences/{id}/run` | plantilla + frecuencia → factura (worker diario) |
| ajustes | `/settings`, `/settings/bank-accounts`, `/billing-groups/{id}`, `/settings/users`, `/settings/invitations[/accept]`, `/settings/gateways`, `/settings/outbox` | vigencias, mensajes, BCC, métodos manuales, consecutivos, roles, invitaciones, pasarelas (secreto cifrado) |
| webhooks | `/webhooks/onvo/{tenant_id}` | firma HMAC `t=..,v1=..` (300 s), idempotente por evento, aplica pago/reembolso |
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
| disponibilidad | `PATCH /products/{id}/web`, `/products/{id}/availability` | interruptor de publicación en la tienda y existencias propias + del proveedor |
| soporte | `/settings/support[/{id}/revoke\|log]` | acceso temporal de solo lectura con bitácora por request |
| bandeja XML | `/settings/inbox`, `/settings/inbox/run` | IMAP cada 15 min (worker); contraseña cifrada |
| contacto | `/public/store/{slug}/contact` | formulario del sitio → nota en la ficha + correo a administradores |

Roles: `admin`, `ventas`, `caja`, `inventario`, `contabilidad`, `lectura` — permisos por módulo × acción en `app/core/deps.py`.

## Reglas que el código ya respeta (plan §5 y §14)
- `tenant_id` en toda tabla de negocio; documentos con `ON DELETE RESTRICT`.
- Consecutivos bajo `SELECT … FOR UPDATE` (Postgres); nunca se saltan ni reutilizan.
- Notas internas nunca salen del sistema; externas van al PDF/correo.
- Factura con pagos o emitida no se edita; anular emitida = nota de crédito (Fase 2).
- Pagos en línea se confirmarán solo por webhook (adapter ONVO, Fase 3). Nunca se guardan datos de tarjeta.
- Logs sin Authorization ni cuerpos; CORS por lista; cookies httpOnly + SameSite.

## Pendiente (requiere credenciales o decisiones del cliente)

1. Proveedor fiscal real (Alanube o GTI): hoy emite el adapter `sandbox`, sin validez ante Hacienda.
2. ONVO real (llaves de prueba y producción): cobro con tarjeta, tarjetas guardadas, reautorizaciones. PayPal igual.
3. BCCR: correo + token del servicio de indicadores para el tipo de cambio automático.
4. Resend: llave para que los correos salgan de verdad (hoy quedan en la bandeja de salida como `simulado`).
5. Dominio propio (app.crimsoncr.com) y SSL: requiere el CNAME en el DNS del cliente.
6. Tasas de planilla 2026: verificar tramos del impuesto al salario y cargas CCSS (valores por defecto: base 2025).
