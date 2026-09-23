# PLAN MAESTRO — Plataforma de Facturación, Cobros y Comercio (réplica funcional de Fygaro)

Proyecto: Crimson Consulting · Desarrollado por JC Analytics
Versión: 1.0 · Fecha: 2026-09-17
Ubicación en repo: `crimson/PLAN_PLATAFORMA.md` (fuente de verdad para trabajar en Cowork)

---

## 0. Cómo usar este documento

Este archivo consolida (a) la investigación sobre Fygaro (sitio oficial, centro de ayuda, API, precios, ecosistema) y (b) la extracción completa del video de la reunión con el cliente (66 secciones de pantallas, campos, acciones y flujos). Es el blueprint para construir nuestra propia plataforma con **nuestro diseño e interfaz**, replicando el 100 % de la funcionalidad observada.

Reglas de trabajo:
- Cada fase se ejecuta como una sesión (o varias) de Cowork sobre esta carpeta.
- No se implementa nada que no esté en este documento sin agregarlo aquí primero.
- Cada épica del backlog (sección 12) se marca `[x]` al cerrarse con su criterio de aceptación.
- No se copia código, texto, marca ni diseño visual de Fygaro. Se replica comportamiento y estructura de información.

---

## 1. Decisiones ya tomadas

- Pasarela de pagos: **ONVO Pay** (SINPE Móvil + tarjetas). PayPal como segunda pasarela opcional. Métodos manuales (efectivo, transferencia, SINPE) siempre disponibles.
- Facturación electrónica: proveedor API homologado en Costa Rica (v4.4 / TRIBU-CR): **Alanube o GTI** (pendiente elegir; ambos vía adaptador). Hacienda directo solo en fase avanzada.
- Infraestructura: **Railway** (workspace jcanalyticscr). Un proyecto con servicios `portal`, `api`, `worker`, `Postgres`, `Redis`.
- Repositorio: monorepo en `C:\Users\memo-\OneDrive\Desktop\crimson` (landing actual en `docs/`, nuevas carpetas `api/`, `portal/`, `worker/`, `infra/`).
- Frontend portal: React + Vite (SPA) con sistema de diseño propio derivado de la landing (fuentes Manrope, Sora, Space Grotesk, Instrument Serif, JetBrains Mono ya presentes en `docs/assets/fonts`).
- Backend: Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic + Celery/Redis + PostgreSQL 16.
- Multi-tenant: sí, desde el día 1 (`tenant_id` en toda tabla de negocio). Primer tenant: Crimson Consulting.
- Login y roles: portal con autenticación propia (JWT + refresh cookie httpOnly + 2FA TOTP) y roles por módulo/acción.
- País inicial: Costa Rica. Diseño preparado para multi-país vía proveedor fiscal.

Pendientes de definición (no bloquean Fase 0): proveedor fiscal (Alanube vs GTI); dominio del portal (`app.<dominio>` vs `/app`); si la landing se queda en GitHub Pages o pasa a Railway.

---

## 2. Qué es Fygaro (síntesis de la investigación)

### 2.1 Posicionamiento y hechos
- Fundada 2015 (Ciudad de Panamá). Lanzó facturación electrónica en Costa Rica en 2018. Hoy se presenta como plataforma de "comercio unificado": Links (enlaces/botones de pago), Shops (tienda no-code), vPOS (Tap to Pay Android), Events (entradas), Invoicing (facturas, recurrentes), Plugins.
- Reclama presencia en 35+ países y >USD 270 M procesados/12 meses. Premio Mastercard Engage 2025 (vPOS con Promerica y Mypinpad). Visa Everywhere Initiative 2020.
- Facturación electrónica **fiscal** confirmada solo para Costa Rica (Hacienda, v4.4, CABYS, notas, recepción, contingencia). Fuera de CR: cobro en línea sin evidencia pública de certificación fiscal.
- Métodos de pago: tarjetas vía banco local (Promerica, Banco General, Lafise, Scotiabank, etc.), PayPal, Yappy (Panamá), métodos personalizados. PCI-DSS, tokenización, 3DS.

### 2.2 Modelo de negocio (lo que sí vale la pena replicar)
- Tarifa plana mensual por plan (Links / Shops Lite-Basic-Pro-Advanced / vPOS) + costo fijo por transacción (≈ USD 0,25 con banco local; aprobadas y declinadas). Sin comisión sobre ventas.
- Prueba gratis 14 días sin tarjeta. Contador como usuario sin costo. Cada "Business" adicional paga su propia mensualidad.
- Diferencias entre planes: usuarios, nivel de tienda, constructor completo, Links, Events, vPOS, cantidad de dispositivos, plugins, cantidad de documentos electrónicos.

### 2.3 API pública de Fygaro (referencia para nuestra propia API)
- Solo **pagos**: checkout por redirección con JWT (HS256 + `kid`; payload `amount, currency, tax, custom_reference, exp, nbf`), webhook firmado HMAC-SHA256 (`Fygaro-Signature: t=..,v1=..`, ventana 300 s, responder 200), endpoint `POST /api/v1/external/payment/refund/` con JWT (`transactionId, iat, exp, amount?`).
- Credenciales: Settings → API Credentials → public key (kid) + secret no recuperable. Requiere plan Pro+.
- **No existe** API de facturas, clientes, productos, inventario, órdenes ni listado de transacciones. Captura/void/reautorización solo desde dashboard.
- Plugins: WooCommerce, Magento, Odoo 19, guía Wix. SDKs solo para verificar webhooks (`fygaro-webhook` PyPI, `@fygaro/webhook` npm).
- Conclusión: nuestra API pública debe superar esto ofreciendo también CRUD de facturas, clientes, productos e inventario (ventaja competitiva).

### 2.4 Migración desde Fygaro
- Exportar desde el dashboard: clientes, productos, inventario (Excel), facturas + XML de Hacienda (documento y respuesta). Importar con script a nuestra BD. No scraping.

---

## 3. Alcance funcional completo (extraído del video)

### 3.1 Estructura general de la aplicación
- Cabecera: nombre/perfil de empresa (arriba izquierda), buscador global "Buscar", engranaje de configuración y menú hamburguesa (arriba derecha). Botón flotante de soporte/chat (abajo derecha).
- Menú lateral principal: Links de pago, Mi Tienda, Pagos, Facturación, Órdenes, Eventos, Productos, Clientes, Contabilidad, Reportes. Cada módulo tiene submenú propio con "Volver al Inicio".
- Patrón de página: contenido central + **columna derecha** con totales, pagos, acciones y ajustes cuando se edita un documento.
- Colores de estado: verde (positivo/seleccionado/pagado), amarillo (advertencia), rojo (anulado/destructivo). Botones principales oscuros. Fondo blanco con tarjetas gris claro.
- Controles recurrentes: "+" crear, refrescar listado, botón "Ver" para abrir, "Más Resultados" (paginación progresiva), estados con círculos/barras de color.

### 3.2 Dashboard
- Accesos rápidos: Crear Cliente, Crear Producto, Crear Factura.
- Bloque PAGOS: Este mes / Hoy / Mes anterior, con variación porcentual en verde/rojo.
- Bloque FACTURADO: mismas tres medidas.
- Widget Pagos Recientes: referencia, cliente, fecha, monto, estado, refrescar.
- Widget Facturas Recientes: código, cliente, importe, estado, refrescar/crear.
- Mejora propia: bloque "Acciones pendientes" (cotizaciones sin respuesta, facturas vencidas, pagos pendientes, stock bajo, documentos rechazados por Hacienda).

### 3.3 Mi Tienda (e-commerce)
- Submenú: Mi Tienda, Navegación, Productos & Servicios, Formularios, Librería Multimedia, Personalizar, Volver al Inicio.
- Personalizar: "Configura el sitio a tu gusto"; Ajustes generales; Dominio gratis vs Dominio personalizado; logo y favicon; Color principal y secundario (hex/selector); Fuente principal y secundaria; Configuración del dominio (URL, DNS); Tipo de tienda + "Cambiar tipo de tienda"; estados borrador/publicado.
- Constructor visual por bloques: canvas con la página; botones circulares "+" entre secciones; controles por bloque (configurar/editar, mover arriba/abajo, duplicar, eliminar); barra de texto enriquecido; layouts de columnas (1, 2, 3); bloques hero (título, texto, CTA), imagen+texto, CTA ("¿Querés que evaluemos tu caso?"), proyectos/casos; previsualizar y publicar.
- Frontend público: breadcrumb, nombre, precio, cantidad (−/valor/+), "Agregar al carrito", imagen, "Items relacionados", carrito y checkout.

### 3.4 Facturación
- Submenú: Facturas, Cotizaciones, Recurrencias, Facturas de Compra, Pagos, Recepción de Facturas, Bandeja de Entrada, Volver al Inicio.
- Listado de Facturas: "Facturas modificadas recientemente"; refrescar; "+"; filas con código, cliente, monto, estado (Creado, Anulado, Pagado); colores; "Más Resultados".
- Listado de Cotizaciones: igual, con estados Creado y Convertida a Factura.
- Detalle de cotización ("Cotización: 188"): Cliente, Grupo de Facturación, Divisa (CRC/USD); columna derecha Subtotal, Descuento, Impuestos, Total; líneas con nombre, descripción, código, cantidad editable, precio, impuestos (ej. "IVA - Tarifa general 13%"), subtotal, eliminar; "Agregar Productos & Servicios"; acciones Guardar, Guardar & Enviar al Cliente, Convertir a Factura, Duplicar Cotización, Anular Cotización; panel Tipos de Cambio (venta/compra); imprimir.
- PDF de cotización: logo y datos de empresa, número, datos del cliente, contacto, fecha, tabla de líneas, subtotal, impuestos, total, términos y condiciones; pie con marca del sistema.
- Nueva cotización: mismos campos; recalcula al cambiar CRC/USD.
- Modal "Productos & Servicios": buscador, resultados con código, nombre, precio, checkbox; "Más Resultados"; Cancelar / Seleccione; empty state "Sin Resultados" con invitación a crear.
- Línea agregada: título, cantidad, eliminar, "Descripción (Click para Editar)", precio, impuestos, subtotal.
- Campos adicionales: Descuento (porcentaje o monto); Impuestos ("Agregar Impuesto"); Notas Internas (no salen en factura); Notas Externas (sí salen); Otros ajustes: Orden Externa #, Código de Actividad (dropdown, ej. 6202.0), casilla "Exoneración Médica IVA (Tarjeta de Crédito)".
- Conversión cotización → factura conserva cliente, líneas, cantidades, descripciones, impuestos y montos.
- Detalle de factura: igual a cotización + indicación de inventario por línea, campo Saldo, bloque Pagos (fecha, método, tipo, monto), botones Agregar Pago, Enlace de Pago, Pagar; acciones Guardar, Guardar & Enviar, Documentos Electrónicos (XMLs), Duplicar Factura, Anular Factura; panel tipos de cambio.
- Modal Pago: aviso "guardar la factura para registrar"; Divisa, Monto, Método, Tipo (Seleccione, Autorización, Captura (Recibido), Devolución, Reembolso, Re-Autorización), Cuenta Bancaria, Referencia Externa, Fecha (date picker), casilla "Enviar confirmación de pago al guardar la factura"; Cancelar / Agregar Pago.
- Modal Enlace de Pago para Factura: URL única por factura; compartir por WhatsApp, Copiar al Portapapeles, Ir al Enlace, Cerrar.
- Modal Documentos Electrónicos (XMLs): "Descargar los Documentos y Respuestas"; estado Aceptada; por documento: Documento, Respuesta, ver/imprimir; descarga individual y conjunta.
- Recepción de Factura Electrónica ("Recepción: 348"): Información del Emisor (nombre, cédula); Estado (Acción realizada: Aceptada; notificación a Hacienda: Aprobado); Información del Gasto (clave, total impuestos, total, condición del impuesto IVA — ej. "Genera crédito IVA", código de actividad, total impuesto por acreditar, total gasto aplicable); descargar XML, respuesta XML, todos.
- Bandeja de Entrada: recibidos recientes; refrescar; empty state "Nada que mostrar... ¡Por ahora!".
- Recurrencias, Facturas de Compra y Órdenes: existen como módulos; no se observó detalle. Diseñar con estándar de mercado (ver sección 4).

### 3.5 Productos
- Submenú: Productos & Servicios, Opciones de Producto, Categorías, Proveedores, Inventarios, Impuestos, Cupones, Volver al Inicio.
- Listado: nombre, código, precio, "Ver"; refrescar; crear.
- Detalle — pestaña Producto: Nombre, Código, Precio, Divisa, Tipo de Item (producto/servicio), Peso (kg), Mostrar en Sitio Web; Descripción "Facturación" (larga, va a documentos) y "Comercio electrónico" (corta, va a Links y Shop); galería (miniaturas, estrella = principal, X = eliminar, "Agregar Imagen" click o arrastrar); Proveedor; Número de Registro (regulaciones/certificaciones); Código CABYS (código + descripción); Partida Arancelaria; Opciones de Producto + "Agregar Opción"; botones Guardar, Borrar, "Crear Link para Producto".
- Pestaña Tienda: "Detalle del Producto" con editor enriquecido (negrita, cursiva, subrayado, alineación, enlaces) + constructor de bloques ("+", layouts "1 Col.", configurar, mover, eliminar).
- Pestaña Categorías e Impuestos: asignación de categorías e impuestos aplicables.
- Pestaña Inventario: filas Inventario/Local + Cantidad; "Agregar a Inventario"; modal Inventarios con buscador y checkboxes múltiples; inventarios con nombre y dirección.
- Categorías: nombre, descripción, "Ver"; crear; Mostrar en sitio web; Subcategorías (eliminables). Ejemplos: cómputo, energía y respaldo, herramientas, redes, repuestos automotrices, seguridad y acceso, servicios, servidores, videovigilancia.
- Cupones: listado; crear; empty state.
- Inventarios: listado nombre, ubicación, "Ver"; detalle "Inventario: <nombre>" con Descripción y Localización (dropdown); Guardar.
- Proveedores e Impuestos: catálogos maestros.

### 3.6 Reportes
- Cuadrícula: Facturación, Facturas Pendientes, Impuesto Facturado, Estado de Resultados, Cierre Diario, Transacciones, Propina, Gastos, Órdenes, IVA, Prorrata - IVA, Recepciones, Inventario, Borrador D151, Venta de Productos.
- Reporte de Inventario: "Descargar Reporte de Inventario" → Excel con columnas de producto, inventario, marca/modelo/proveedor, precios, impuestos, costos.
- Regla: todo reporte se visualiza en pantalla y se exporta a Excel/CSV.

### 3.7 Contabilidad
- Submenú: Gastos, Gastos Recurrentes, Categoría de Gastos, Planillas, Empleados, Bancos, Cuentas de Banco, Volver al Inicio.
- Gastos: "Gastos recientemente modificados"; id, monto, "Ver"; crear.
- Categorías de gastos: administración, combustible, planilla, servicios/subcontratistas, etc.
- Cuentas de Banco: nombre, moneda (USD/CRC), número/referencia; "Ver"; crear. Se vinculan al modal de Pago.
- Bancos, Planillas, Empleados: catálogos y registro básico.

### 3.8 Mi Cuenta y Ajustes
- Mi Cuenta: Restablecer contraseña, 2FA, Cambiar correo, Administrar direcciones, Administrar tarjetas guardadas. Submenú: Mi Cuenta, Ajustes, Invitaciones, Importar.
- Ajustes (grupos): Empresa → Ajustes Generales; Comercio electrónico y pagos; Facturación Electrónica (credenciales y consecutivos); Administrar Suscripción. Facturación → consecutivos, expiración, recordatorios. Credenciales API. Dispositivos vPOS. Administración de usuarios → Usuarios, Roles. Botón "Permitir Acceso de Soporte".
- Ajustes Generales de Empresa: logo; Sector económico; Idioma primario; Empresa, Razón Social, Cédula; Divisas múltiples con "Predeterminada"; Teléfonos con tipo (WhatsApp) y "Principal", "Agregar Teléfono"; Direcciones (País, Provincia, Cantón, Distrito, Señas) con Principal/predeterminada; Redes sociales (Facebook, Twitter, Instagram); 2FA.
- Ajustes de Facturación: Días de vigencia de factura; Mensaje personalizado para toda factura; Días de vigencia de cotización; Mensaje personalizado para cotizaciones (puede incluir instrucciones de pago/bancarias).
- Grupos de facturación: Facturas Electrónicas (Prefijo, Valor Actual, Predeterminado, Comercio electrónico, "Agregar Grupo"); Facturas de Exportación; Tiquetes Electrónicos; Grupos de Cotizaciones. Prefijos observados: FEC, FEE, TE.
- Notificaciones: Enviar reporte de cierre de caja diario; notificar vencimiento al cliente; recordar expiración; "¿Cuántos días antes?"; BCC toda factura (correos en copia); Guardar.
- Comercio Electrónico y Pagos: inventario que usa la tienda; Legal (Política de privacidad, Términos y condiciones, enlaces a generadores); Reautorizaciones (Habilitado + explicación); Tarifas de envío personalizadas ("Administrar tarifas"); Integraciones de envío (courier dropdown, ej. Correos de Costa Rica; Número de cuenta; Tipo de overhead: Fijo/%; Monto; Activo; Predeterminado; "Agregar Integración"); Pasarelas de pago (Pasarela, Client ID, Client Secret, Principal, Activo, "Agregar Pasarela"; ej. PayPal Business); Métodos de pago personalizados (Nombre, Instrucciones de pago, Activo; ej. Efectivo contra entrega, Transferencia SINPE, SINPE Móvil, Efectivo; "Agregar Método").

### 3.9 Links de pago, Shops, vPOS, Eventos, Órdenes, Clientes, Importar, Invitaciones
- Links: crear enlace compartible por chat, correo, redes, factura o QR; "Crear Link para Producto" desde la ficha de producto; enlace por factura.
- Shops: catálogo, pedidos y pagos; construido con Mi Tienda.
- vPOS: Tap to Pay con teléfono compatible; "Dispositivos vPOS" en ajustes. (Requiere SDK bancario certificado; diferido.)
- Eventos: módulo existente (entradas); detalle no observado → diseñar como producto tipo ticket con QR.
- Órdenes: módulo con reporte propio; detalle no observado → diseñar como pedido de tienda/POS previo a factura.
- Clientes: entidad independiente reutilizada en cotizaciones, facturas, pagos y XML; "Crear Cliente" desde dashboard. Ficha no observada en detalle → diseñar con cédula/tipo, nombre, correo, teléfonos, direcciones, exoneración, moneda preferida, notas, timeline.
- Importar: entrada masiva/migración (formatos no observados) → CSV/Excel para clientes, productos e inventario.
- Invitaciones: alta de usuarios a la empresa por correo.

---

## 4. Flujos de negocio (ejes que no se pueden romper)

Eje 1 (ventas): Cliente → Producto/Servicio → Cotización → Enviar al cliente → Convertir a Factura → Registrar/solicitar pago → Enlace de pago (WhatsApp) → Factura electrónica / XML → Reportes / Contabilidad.

Eje 2 (catálogo): Producto → Categoría → Proveedor → Inventario → Tienda / Link → Carrito / Orden → Venta → Factura.

Eje 3 (compras): Factura recibida (XML por correo) → Recepción electrónica → Aceptar/Rechazar/Parcial ante Hacienda → Gasto → Cuenta bancaria → Reportes (IVA acreditable, prorrata, D151).

Flujos derivados que deben existir:
- Orden (tienda/POS) → Factura o Tiquete electrónico.
- Recurrencia → genera factura en la fecha programada → cobra con tarjeta tokenizada (si aplica) → envía correo.
- Factura pagada parcialmente → saldo → nuevo enlace de pago por el saldo.
- Factura anulada → Nota de Crédito electrónica referenciando la clave original.
- Reembolso ONVO → Devolución/Reembolso registrado como pago negativo + Nota de Crédito.

Prioridad observada del cliente (video, sección 61): 1) cotizaciones/facturas; 2) enlace de pago por WhatsApp; 3) facturación electrónica CR; 4) producto + inventario; 5) e-commerce; 6) reportes/control; 7) multimoneda; 8) flexibilidad de pago; 9) usuarios/permisos/API.

---

## 5. Reglas de negocio y reglamentos

### 5.1 Facturación electrónica Costa Rica (v4.4 / TRIBU-CR)
- Base legal: Resolución MH-DGT-RES-0001-2025 (versión 4.4 obligatoria desde 1-sep-2025) y Ley del Impuesto al Valor Agregado (Ley 9635). Verificar siempre la resolución vigente antes de cada fase fiscal.
- Tipos de comprobante a soportar: Factura Electrónica (FE), Tiquete Electrónico (TE), Nota de Crédito (NC), Nota de Débito (ND), Factura Electrónica de Exportación (FEE), Factura Electrónica de Compra (FEC), y mensajes de receptor (aceptación, aceptación parcial, rechazo).
- Consecutivo: 20 dígitos = sucursal (3) + terminal (5) + tipo de documento (2) + consecutivo (10). Se administra por **grupo de facturación** (prefijo, valor actual, predeterminado, comercio electrónico). Nunca reutilizar ni saltar consecutivos; secuencia por sucursal/terminal/tipo bajo bloqueo transaccional.
- Clave: 50 dígitos generada por el proveedor/Hacienda; se guarda y se muestra en XML, PDF y reportes.
- Cada línea requiere código CABYS (13 dígitos) con descripción; cantidad; unidad de medida; precio unitario; descuento con naturaleza; impuesto con código, tarifa (0, 1, 2, 4, 8, 13 %) y exoneración cuando aplique (tipo, número, institución, fecha, porcentaje).
- Código de actividad económica del emisor obligatorio en el documento (dropdown de actividades registradas ante Hacienda; ej. 6202.0).
- Receptor: tipo de identificación (física, jurídica, DIMEX, NITE, extranjero), número, nombre, correo. Tiquete electrónico no requiere receptor.
- Condición de venta (contado, crédito, consignación, apartado, arrendamiento, etc.), plazo de crédito en días, medio de pago (efectivo, tarjeta, cheque, transferencia, recaudado por terceros, SINPE Móvil, plataforma digital, otros).
- Moneda: CRC por defecto; USD u otras con tipo de cambio del día (BCCR, tipo de venta) guardado en el documento y no modificable después de emitido.
- Redondeos: montos con 5 decimales en XML; totales calculados con las reglas del esquema; validar contra sandbox del proveedor.
- Exoneración médica IVA con tarjeta de crédito: casilla que aplica el tratamiento tarifario correspondiente a servicios de salud pagados con tarjeta (verificar norma vigente antes de implementar).
- Estados del documento: borrador → emitido/enviado → aceptado / rechazado (Hacienda) → anulado (vía NC). Rechazos se muestran con motivo y permiten corregir y reemitir con nuevo consecutivo.
- Contingencia: si Hacienda o el proveedor no responde, el documento queda "pendiente de envío" y un worker reintenta con backoff; nunca se entrega al cliente como "aceptado" sin respuesta de Hacienda.
- Recepción: todo XML recibido de proveedores se procesa (validar firma/clave), se registra como Factura de Compra y se responde a Hacienda con aceptación, aceptación parcial o rechazo dentro del plazo reglamentario; se calcula IVA acreditable según condición (genera crédito / no genera / proporcional).
- Conservación: XML documento + XML respuesta + PDF almacenados mínimo 5 años (Código de Normas y Procedimientos Tributarios), inmutables, en almacenamiento de objetos con versionado.
- Reportes fiscales: IVA (D-104 base), Prorrata IVA, Borrador D151 (informativa de clientes/proveedores), Impuesto Facturado, Recepciones.

### 5.2 Cotizaciones y facturas (reglas comerciales)
- Días de vigencia configurables por empresa para cotización y factura; vencimiento genera recordatorios automáticos (N días antes) y estado "vencida".
- Notas internas nunca se imprimen ni se envían; notas externas van al PDF y al correo.
- Descuento global por porcentaje o monto; descuentos por línea; impuestos por línea y adicionales por documento.
- Conversión cotización → factura crea la factura con estado Creado y marca la cotización como Convertida (no editable).
- Duplicar crea un nuevo documento en estado borrador con nuevo consecutivo del grupo.
- Anular factura emitida = emitir NC; anular cotización = estado Anulado.
- Saldo = total − suma de pagos capturados + devoluciones/reembolsos.
- Mensaje personalizado por empresa al pie de facturas y cotizaciones (instrucciones de pago, cuentas bancarias).
- BCC configurable para copiar a contabilidad en todo envío.

### 5.3 Pagos y cobros
- Separar **método** (efectivo, tarjeta, SINPE Móvil, transferencia, PayPal, ONVO) de **tipo de transacción** (Autorización, Captura, Devolución, Reembolso, Re-Autorización, Void).
- Todo pago se asocia a una cuenta bancaria del tenant y a una referencia externa (número de comprobante SINPE, id ONVO, etc.).
- Enlace de pago: URL pública única por factura/orden/producto, con token firmado y expiración; registra creado/abierto/pagado; comparte por WhatsApp (deep link `https://wa.me/?text=`), copiar, abrir.
- ONVO: crear Payment Intent en servidor con monto en la moneda de la factura; checkout hosteado o embebido; confirmar solo por **webhook** firmado (nunca por redirect); idempotencia por `payment_intent_id`; reintentos y registro de todos los eventos crudos; reembolsos por API; conciliación diaria contra reporte ONVO.
- PCI: el sistema nunca ve ni almacena PAN/CVV; solo tokens del proveedor. Tarjetas guardadas del cliente = tokens.
- Reautorizaciones: si está habilitado, antes de que venza una autorización (≈7 días; 30 en hoteles/rent-a-car) se genera una nueva.
- Métodos manuales: instrucciones de pago visibles en checkout y en el enlace; la confirmación es manual por Caja con referencia.
- Cierre de caja diario: reporte por método, usuario y cuenta; envío por correo opcional.
- Propinas: campo opcional en pagos con tarjeta (reporte Propina).

### 5.4 Inventario
- Stock por ubicación; los movimientos se registran en un ledger (entrada, salida por factura, ajuste, transferencia, devolución); el saldo se calcula, no se edita.
- Venta descuenta del inventario asignado al canal (tienda usa el inventario configurado en Ajustes de e-commerce).
- Alertas de stock mínimo por producto/ubicación (mejora propia).
- Reporte de inventario exportable con costo, precio, impuestos, proveedor, marca/modelo.

### 5.5 Seguridad, privacidad y accesos
- Autenticación: correo + contraseña (Argon2), 2FA TOTP opcional por usuario y obligatorio para Admin; bloqueo por intentos; refresh tokens rotativos.
- Roles predefinidos: Administrador, Ventas, Caja, Inventario, Contabilidad, Solo lectura, Cliente-final (portal de cliente opcional). Permisos por módulo × acción (ver, crear, editar, anular, exportar, configurar).
- "Permitir acceso de soporte": impersonación por JC Analytics con consentimiento explícito, tiempo limitado y auditoría.
- Auditoría: toda creación/edición/anulación de documentos y pagos registra usuario, fecha, IP y diff.
- Ley 8968 (Protección de la Persona frente al Tratamiento de sus Datos Personales, Costa Rica): consentimiento y finalidad para datos de clientes; política de privacidad y términos configurables por tenant; derechos ARCO; minimización de datos; cifrado en tránsito (TLS) y en reposo (secretos por tenant con clave KMS/Fernet).
- Secretos de proveedores (ONVO, Hacienda/proveedor fiscal) nunca en frontend ni en variables globales compartidas entre tenants.
- Backups diarios de Postgres y del bucket; prueba de restauración trimestral.

### 5.6 Multimoneda
- Divisas habilitadas por empresa con una predeterminada. Tipo de cambio de venta y compra del BCCR consultado diariamente (worker) y editable manualmente por documento antes de emitir.
- Los documentos se emiten en la moneda seleccionada; los reportes consolidan en CRC usando el tipo de cambio guardado en cada documento.

### 5.7 Reglas de interfaz (patrones a conservar)
- Navegación jerárquica: módulo → submenú → listado → "Ver" → detalle.
- Listados "Modificados recientemente" con refrescar, "+ Crear" y "Más Resultados".
- Documentos con columna derecha fija de totales/acciones/pagos/tipo de cambio.
- Modales para: seleccionar productos, registrar pago, seleccionar inventarios, enlace de pago, XMLs.
- Formularios largos divididos en tarjetas; ayudas bajo cada input.
- Empty states con mensaje ("Nada que mostrar... ¡Por ahora!", "Sin Resultados" + crear).
- Estados por color; botones destructivos diferenciados y con confirmación.
- Componentes: input, textarea, dropdown con búsqueda, checkbox, date picker, buscador, upload drag-and-drop, editor enriquecido, constructor de bloques.
- Diferenciadores propios: dashboard con acciones pendientes; autosave en cotizaciones; vista previa en tiempo real del PDF; botón único WhatsApp (cotización, factura, recordatorio, enlace, comprobante); timeline por cliente y por factura (borrador → enviada → vista → vencida → pagada); tracking de enlaces de pago; alertas de stock; transferencias entre ubicaciones; modo claro/oscuro.

---

## 6. Arquitectura técnica

```
[portal (React+Vite)]  [landing docs/ (estático)]  [tienda pública por tenant]  [página de pago / Link]
              \                 |                        /                          /
               \----------------+------------------------+-------------------------/
                                        HTTPS
                                          |
                               [api (FastAPI, monolito modular)]
   módulos: core(auth, tenants, roles, settings) · crm · catalog · inventory · sales(quotes, invoices,
            orders, recurrences) · payments · einvoice · ecommerce(pages, store) · accounting · reports · public_api
                                          |
                 +------------------------+-------------------------+
                 v                                                  v
      [PaymentProvider (interfaz)]                       [EInvoiceProvider (interfaz)]
        OnvoAdapter · PayPalAdapter · ManualAdapter        AlanubeAdapter · GTIAdapter · (HaciendaDirectAdapter)
                 |                                                  |
        [ONVO / PayPal]                                   [Alanube / GTI] → Hacienda (TRIBU-CR)
                 |  webhooks firmados                              |  webhooks / polling
                 v                                                  v
   [Webhook ingress → tabla webhook_event → Redis/Celery → workers: emisión FE, conciliación,
    recurrencias, recordatorios, cierre diario, tipo de cambio BCCR, reportes pesados, correo]
                                          |
                     [PostgreSQL 16]  [Redis]  [Bucket S3/R2: PDF, XML, imágenes]  → [Power BI]
```

Principios: monolito modular (no microservicios); eventos internos persistidos en `outbox_event`; idempotencia por identificadores externos; adaptadores para todo proveedor; PDF con plantillas HTML (WeasyPrint); constructor de páginas = JSON de bloques renderizado por el mismo componente en editor y sitio público; API pública propia con key/secret por tenant, JWT de checkout y webhooks HMAC (mismo estándar que Fygaro, más CRUD).

### 6.1 Estructura del monorepo
```
crimson/
├── docs/                 # landing pública actual (sin cambios)
├── portal/               # SPA React + Vite (login, roles, todos los módulos)
│   ├── src/app/          # router, layout, guards por rol
│   ├── src/modules/      # dashboard, crm, catalog, inventory, sales, payments, einvoice, store, accounting, reports, settings
│   ├── src/ui/           # sistema de diseño (tokens, componentes base)
│   └── vite.config.ts
├── api/
│   ├── app/core/         # config, db, auth, tenants, roles, audit, settings
│   ├── app/modules/      # crm, catalog, inventory, sales, payments, einvoice, ecommerce, accounting, reports, public_api
│   ├── app/providers/    # payments/{base,onvo,paypal,manual}.py · einvoice/{base,alanube,gti}.py · fx/bccr.py · courier/correos.py
│   ├── app/workers/      # tareas Celery
│   ├── alembic/
│   ├── tests/
│   ├── Dockerfile · railway.toml · pyproject.toml
├── worker/               # mismo código que api, comando celery worker + beat (Dockerfile propio)
├── infra/                # docker-compose.yml, scripts de migración desde Fygaro, seeds
├── .env.example
└── PLAN_PLATAFORMA.md
```

### 6.2 Railway (proyecto único)
- Servicios: `portal` (build Vite, servir estático), `api` (Dockerfile), `worker` (Dockerfile, comando celery), `Postgres`, `Redis`. Bucket externo (Cloudflare R2 o S3) o bucket Railway.
- Red privada: `api` ↔ `Postgres`/`Redis` por `*.railway.internal`; no exponer BD.
- Variables por referencia (`${{Postgres.DATABASE_URL}}`, `${{Redis.REDIS_URL}}`); secretos de ONVO y proveedor fiscal como variables del servicio `api`/`worker` y, por tenant, cifrados en BD.
- Dominios: landing (raíz), `app.<dominio>` (portal), `api.<dominio>` (API y webhooks). Certificados automáticos.
- Entornos: `production` y `staging` (sandbox fiscal + ONVO test).
- Despliegue: repo GitHub conectado (push → deploy) o `railway up --service <nombre>`.
- Healthchecks `/health` en api; cron de beat para tipo de cambio, recordatorios, cierre diario.

---

## 7. Modelo de datos (entidades y campos clave)

Núcleo: `tenant` (nombre, razón social, cédula, sector, idioma, logo, plan, estado) · `user` (email, hash, 2fa_secret, activo) · `tenant_user` (rol) · `role` · `permission` · `invitation` · `audit_log` · `tenant_setting` (facturación, e-commerce, notificaciones, legal) · `currency` (código, predeterminada) · `exchange_rate` (fecha, venta, compra, fuente) · `phone` · `address` (país, provincia, cantón, distrito, señas, principal) · `social_link` · `api_credential` (kid, secret_hash, activo) · `support_access_grant`.

CRM: `customer` (tipo_id, identificación, nombre, correo, teléfonos, moneda, exoneración, notas, activo) · `customer_contact` · `customer_saved_card` (token proveedor).

Catálogo: `product` (nombre, código, precio, moneda, tipo_item, peso, mostrar_web, desc_facturación, desc_ecommerce, proveedor_id, número_registro, cabys, partida_arancelaria, stock_mínimo) · `product_image` (principal) · `product_option` / `product_option_value` · `category` (padre_id, mostrar_web) · `product_category` · `tax` (nombre, código, tarifa) · `product_tax` · `supplier` · `coupon` (código, tipo, valor, vigencia, usos) · `page_block` para detalle enriquecido.

Inventario: `warehouse` (nombre, descripción, localización) · `stock_level` (producto, bodega, cantidad calculada) · `stock_movement` (tipo, cantidad, referencia, usuario).

Ventas: `billing_group` (tipo_doc, prefijo, sucursal, terminal, valor_actual, predeterminado, ecommerce) · `quote` (cliente, grupo, moneda, tipo_cambio, subtotal, descuento, impuestos, total, notas_int, notas_ext, orden_externa, actividad, exoneración_médica, estado, vigencia) · `quote_line` · `invoice` (mismos campos + saldo, condición_venta, plazo, medio_pago, quote_id, estado) · `invoice_line` · `order` (canal tienda/POS/link, estado, envío) · `order_line` · `recurrence` (plantilla, frecuencia, próxima fecha, cobro automático).

Facturación electrónica: `einvoice_document` (invoice_id, tipo, consecutivo, clave, estado, proveedor_ref, xml_doc_url, xml_resp_url, pdf_url, mensaje_hacienda, intentos) · `received_document` (emisor, clave, totales, condición_iva, actividad, iva_acreditable, gasto_aplicable, acción, estado_hacienda, xml_urls, expense_id) · `activity_code`.

Pagos: `payment` (documento, método, tipo, moneda, monto, cuenta_bancaria, referencia_externa, fecha, proveedor, external_id, estado, propina) · `payment_link` (documento/producto, token, url, expira, abierto_en, pagado_en) · `payment_gateway_config` (tenant, proveedor, client_id, secret_cifrado, principal, activo) · `manual_payment_method` (nombre, instrucciones, activo) · `bank` · `bank_account` (nombre, moneda, número) · `webhook_event` (proveedor, raw, firma_ok, procesado_en) · `outbox_event`.

E-commerce: `store_settings` (dominio, tipo, logo, favicon, colores, fuentes, inventario, estado publicado) · `page` · `page_block` (tipo, orden, json) · `nav_item` · `media_asset` · `form` / `form_submission` · `shipping_rate` · `courier_integration` (tipo, cuenta, overhead_tipo, overhead_monto, activo, predeterminado) · `cart`.

Contabilidad: `expense` (categoría, monto, moneda, fecha, cuenta, adjunto, received_document_id) · `expense_category` · `recurring_expense` · `employee` · `payroll`.

Eventos: `event` · `ticket_type` · `ticket` (QR firmado, estado, validado_en).

Reglas de esquema: `tenant_id` + índices compuestos en toda tabla de negocio; claves foráneas con `ON DELETE RESTRICT` para documentos fiscales; `created_at/updated_at/created_by`; soft delete solo en catálogos, nunca en documentos emitidos.

---

## 8. Integraciones

- **ONVO Pay**: API REST con claves pública/secreta; crear Payment Intent (monto, moneda, descripción, metadata = ids internos); checkout hosteado/embebido; webhooks de `payment_intent.succeeded/failed`; reembolsos; clientes y métodos de pago guardados (tokens). Confirmar en documentación oficial las firmas de webhook y montos en centavos antes de codificar el adapter.
- **Proveedor fiscal (Alanube o GTI)**: alta del tenant con credenciales de Hacienda (usuario, contraseña, llave criptográfica .p12 y PIN) cargadas en el proveedor; emisión por JSON; consulta de estado; webhooks; recepción; sandbox. Adapter debe mapear nuestro modelo → esquema v4.4.
- **BCCR**: servicio de indicadores económicos (tipo de cambio venta/compra) consultado diariamente; fallback manual.
- **WhatsApp**: deep links `wa.me` con texto prellenado (cotización, factura, enlace de pago, recordatorio). Integración con WhatsApp Business API queda como fase futura.
- **Correo**: proveedor transaccional (Resend/SendGrid) con plantillas: cotización, factura + PDF + XMLs, confirmación de pago, recordatorio, cierre de caja, invitación.
- **Courier**: Correos de Costa Rica (tarifas + overhead); DHL futuro.
- **Power BI**: conexión directa a Postgres (usuario de solo lectura) para reportes avanzados.
- **API pública propia**: `/v1/invoices`, `/v1/customers`, `/v1/products`, `/v1/inventory`, `/v1/payment-links`, `/v1/checkout` (JWT), `/v1/refunds`, webhooks HMAC con `X-Signature: t=..,v1=..`; credenciales por tenant; documentación OpenAPI.

---

## 9. Diseño e interfaz propia

- Sistema de diseño en `portal/src/ui`: tokens de color (marca Crimson + neutros), tipografía (Sora/Space Grotesk para títulos, Manrope para texto, JetBrains Mono para códigos y montos), espaciado, radios, sombras; modo claro/oscuro.
- Layout: barra lateral colapsable con módulos; submenú contextual; cabecera con buscador global, selector de empresa (multi-tenant), avatar, engranaje; botón flotante de ayuda.
- Componentes base: DataList (listado con estado, refrescar, crear, más resultados), DocumentEditor (dos columnas), Modal, Drawer, Form (tarjetas con ayudas), StatusBadge, MoneyInput con moneda, DatePicker, SearchSelect, Uploader, RichTextEditor, BlockBuilder, EmptyState, ConfirmDestructive.
- Accesibilidad: contraste AA, navegación por teclado, etiquetas en formularios.
- Responsive: portal usable en tablet (POS/caja) y móvil (consultar, cobrar, enviar enlace).
- Nada del diseño visual de Fygaro se reproduce: solo comportamientos y jerarquía de información.

---

## 10. Roadmap por fases

### Fase 0 — Fundaciones (2 semanas)
Entregables: monorepo con `api/`, `portal/`, `worker/`, `infra/docker-compose.yml`; Postgres + Redis locales; Alembic con migración inicial; auth (registro por invitación, login, refresh, 2FA); tenants; roles y permisos; ajustes de empresa (datos, divisas, teléfonos, direcciones, redes, 2FA); tipo de cambio BCCR diario; sistema de diseño y layout del portal; CI (lint, tests); despliegue inicial a Railway (staging) con dominios.
Criterio de salida: login con 2FA, cambio de empresa, roles aplicados en menú y API; `docker compose up` levanta todo; `railway up` despliega staging.

### Fase 1 — Eje comercial (6 semanas)
Entregables: dashboard (KPIs, recientes, acciones pendientes); clientes (CRUD, contactos, timeline); productos/servicios (ficha completa, descripciones, galería, CABYS, partida, proveedor, opciones, impuestos, categorías/subcategorías); proveedores; impuestos; cotizaciones (crear, líneas, modal productos, descuento, impuestos, notas, actividad, exoneración médica, tipos de cambio, PDF, enviar por correo, duplicar, anular, vigencia); conversión a factura; facturas (mismo editor + saldo + pagos manuales + tipos de transacción + cuentas bancarias + confirmación); enlace de pago (URL pública, WhatsApp, copiar, tracking); métodos de pago manuales; ajustes de facturación (vigencias, mensajes, grupos y consecutivos, notificaciones, BCC); reportes básicos (facturación, pendientes, transacciones, cierre diario) con exportación Excel.
Criterio de salida: el cliente opera ventas y cobros manuales sin Fygaro; PDF comparable; consecutivos correctos; checklist de secciones 2, 5–16, 20–24, 27, 35, 38–41 del video aprobado.

### Fase 2 — Facturación electrónica (4 semanas)
Entregables: adapter proveedor fiscal; alta de credenciales Hacienda por tenant; emisión FE/TE/NC/ND/FEE; estados y reintentos (contingencia); modal XMLs (documento, respuesta, descarga individual/conjunta); anulación vía NC; recepción de facturas (bandeja de entrada por correo IMAP o carga de XML, aceptación/parcial/rechazo, cálculo IVA acreditable, gasto); reportes IVA, Impuesto Facturado, Prorrata, Recepciones, Borrador D151; script de migración de históricos desde exportaciones/XML de Fygaro.
Criterio de salida: 100 documentos aceptados en sandbox cubriendo IVA 13/4/2/1/0, exoneración, descuento, USD, NC/ND; recepción de 20 XML reales; migración con conteos y sumas cuadradas.

### Fase 3 — Cobro en línea e inventario (4 semanas)
Entregables: adapter ONVO (Payment Intent, checkout, webhook firmado, reembolso, tarjetas guardadas); PayPal opcional; página pública de pago (enlace) con métodos ONVO + manuales; reautorizaciones; conciliación automática pago ↔ factura y reporte diario; propinas; inventarios por ubicación, movimientos, descuento por venta, transferencias, alertas de stock mínimo, reporte Excel; cupones.
Criterio de salida: pruebas de fallo de webhooks (duplicado, firma inválida, timeout, fuera de orden) sin pagos huérfanos ni dobles; reembolso genera NC; inventario cuadra tras 500 movimientos simulados.

### Fase 4 — E-commerce (6 semanas)
Entregables: Mi Tienda (personalización: dominio propio con CNAME + SSL, logo, favicon, colores, fuentes, tipo, borrador/publicado); navegación; librería multimedia; formularios; constructor de bloques (hero, texto enriquecido, columnas 1–3, imagen+texto, CTA, galería, productos destacados, formulario); catálogo público, categorías, ficha de producto con página enriquecida y relacionados; carrito; checkout (ONVO + manuales); órdenes (estados, conversión a factura/tiquete); envíos (tarifas propias, Correos de CR con overhead); legal (privacidad, términos); inventario de e-commerce.
Criterio de salida: compra completa de punta a punta en dominio propio con emisión de tiquete electrónico y descuento de inventario.

### Fase 5 — Extensiones (4–6 semanas)
Entregables: recurrencias (facturas programadas, cobro automático con token); facturas de compra manuales; contabilidad (gastos, recurrentes, categorías, bancos, cuentas, empleados y planillas básicas, estado de resultados); eventos y tickets con QR y validación; POS web (caja, cierre, datáfono bancario registrado manualmente); API pública con credenciales, JWT de checkout y webhooks; importador CSV/Excel; invitaciones; acceso de soporte auditado; reportes restantes (Órdenes, Propina, Gastos, Venta de Productos).
Criterio de salida: paridad total con el mapa de módulos de la sección 65 del video (excepto vPOS).

### Fase 6 — Diferido / condicionado
- vPOS Tap-to-Pay (requiere SDK bancario certificado PCI MPoC; evaluar con Promerica/BAC).
- WhatsApp Business API (mensajería saliente automatizada).
- Segundo país fiscal (Panamá/RD) vía proveedor multi-país.
- Hacienda directo (firma XAdES propia) si el volumen justifica eliminar el costo por documento.

Estimación total: 26–28 semanas con 2–3 desarrolladores. El cliente sale de Fygaro al cierre de la Fase 2.

---

## 11. Plan de sesiones Cowork (primeras 8)

1. Fase 0.1 — scaffold `api/` (FastAPI, SQLAlchemy, Alembic, settings, health), `infra/docker-compose.yml`, `.env.example`, tests base.
2. Fase 0.2 — auth + tenants + roles + audit + 2FA; seeds (tenant Crimson, admin).
3. Fase 0.3 — scaffold `portal/` (Vite, router, guards, layout, sistema de diseño, login).
4. Fase 0.4 — ajustes de empresa, divisas, BCCR worker; despliegue staging Railway.
5. Fase 1.1 — clientes + productos (ficha completa) + categorías + proveedores + impuestos.
6. Fase 1.2 — cotizaciones (editor, modal productos, PDF, correo).
7. Fase 1.3 — facturas, conversión, pagos manuales, enlace de pago + WhatsApp.
8. Fase 1.4 — ajustes de facturación, grupos/consecutivos, notificaciones, reportes básicos, dashboard.

9. Sesión 5 — la otra mitad del negocio: oportunidades, levantamientos técnicos, costeo, proyectos, órdenes de trabajo, activos, compras y rentabilidad real (2026-09-22).

Cada sesión termina con: tests verdes, migración aplicada, `README` del módulo actualizado y épicas marcadas en este documento.

---

## 12. Backlog por épicas (checklist de paridad)

Núcleo
- [x] Multi-tenant, auth, refresh, 2FA, bloqueo por intentos — `api/app/routers/auth.py` (2026-09-17)
- [x] Roles y permisos por módulo × acción; roles predefinidos — `api/app/core/deps.py`
- [x] Invitaciones · acceso de soporte auditado (solo lectura por horas, bitácora por request, revocable) — `routers/support.py`
- [x] Límite de intentos de login por IP (429) además del bloqueo por cuenta
- [x] Ajustes de empresa (logo, razón social, cédula, divisa, actividad, teléfono, redes)
- [~] Tipo de cambio BCCR diario + manual por documento — tarea worker + `/fx` (falta credencial BCCR)
- [x] Buscador global (Ctrl+K: facturas, cotizaciones, clientes, productos, órdenes)
- [x] Auditoría y logs — `audit_log` en crear/editar/anular/pagar/login

Dashboard
- [x] Accesos rápidos · KPIs Pagos/Facturado (hoy, mes, mes anterior, variación) · Pagos recientes · Facturas recientes · Acciones pendientes — `/dashboard` + portal

CRM
- [x] Clientes: ficha con KPIs, contactos, notas y línea de tiempo (cotizaciones, facturas, pagos, pedidos) · archivar · tarjetas guardadas (requiere ONVO real)

Catálogo
- [x] Productos/servicios ficha completa: galería con imagen principal, proveedor, categoría, peso, partida, registro
- [x] Proveedores (editables) · Impuestos · Categorías · Cupones · Variantes con código y precio propio (tienda y POS) · Subcategorías (modelo con parent_id)
- [x] Página de producto en la tienda: galería, variantes, relacionados, consulta por WhatsApp
- [x] "Copiar link" de producto (abre la ficha en la tienda con `?p=`)

Inventario
- [x] Ubicaciones · stock por ubicación · ledger · descuento por venta · transferencias · alertas · reporte Excel

Ventas
- [x] Cotizaciones completas · PDF/HTML · envío por correo · duplicar · anular · vigencia
- [x] Conversión cotización → factura — conserva cliente, líneas, totales; marca `convertida`
- [x] Facturas completas · saldo · pagos · enlace · XMLs · anular (NC)
- [x] Recurrencias · Órdenes (tienda → tiquete/factura) · Facturas de compra (vía recepción XML)

Cobros
- [x] Modal Pago con método/tipo/cuenta/referencia/fecha/confirmación
- [x] Enlace de pago por factura (WhatsApp, copiar, abrir, tracking de aperturas)
- [~] ONVO: adapter + webhook firmado/idempotente + reembolso (falta credencial real) · métodos manuales (listo) · PayPal (pendiente)
- [~] Pasarelas configurables con secreto cifrado (listo) · Reautorizaciones (pendiente) · Propinas (campo listo)
- [x] Cuentas bancarias · Cierre de caja diario · Conciliación bancaria (importa CSV/Excel del banco, casa automático por monto+fecha+referencia, manual, gasto desde débito)

Facturación electrónica
- [x] Grupos y consecutivos (FE, TE, FEE, NC, ND, cotizaciones) — 20 dígitos v4.4, bloqueo por fila
- [~] Emisión · estados · XML documento/respuesta con adapter sandbox (listo) · credenciales Hacienda y contingencia con proveedor real (pendiente)
- [x] XMLs documento/respuesta, descarga individual
- [x] Recepción (emisor, clave, IVA acreditable, actividad, aceptar/parcial/rechazar) · Bandeja IMAP (cada 15 min, contraseña cifrada) + carga manual
- [x] Notificaciones: ajustes, BCC, recordatorios automáticos de vencimiento y cierre diario por correo

E-commerce
- [~] Personalización · Navegación · Multimedia (subida de imágenes y PDF, biblioteca) (listos) · SSL de dominio propio (depende del DNS del cliente)
- [x] Constructor de bloques (8 tipos, mismo render en editor y sitio) · Catálogo público · Carrito · Checkout · Relacionados
- [x] Envíos: tarifa fija o por peso estilo Correos de CR con recargo · Inventario de tienda · Textos legales en el pie del sitio · Formulario de contacto (nota en la ficha + correo)

Contabilidad
- [x] Gastos con adjunto y proveedor · categorías · cuentas · estado de resultados · gastos recurrentes · colaboradores · planillas (CCSS, renta por tramos, provisiones, colillas, gasto automático)

Reportes
- [x] Los 11 anteriores + Órdenes · Recepciones · Propinas · D-151 (borrador) · Planilla · Conciliación · Rentabilidad por proyecto — 18 reportes, todos con Excel

Comercial y campo (la otra mitad del negocio, sesión 5)
- [x] Oportunidades: embudo por estado con monto ponderado, seguimiento con fecha y bitácora, aviso a los 5 días sin movimiento — `routers/pipeline.py`
- [x] Levantamientos técnicos por tipo de solución (CCTV, cableado, acceso, asistencia, redes, UPS, ANPR): formulario dinámico desde `/field/specs`, puntos, sugerencia de materiales con 15 % de holgura — `routers/fieldwork.py`
- [x] Costeo del levantamiento (costo por línea, mano de obra por técnico/día, viáticos, margen) y "aprobar y generar cotización" en un clic
- [x] Cotización aprobada → proyecto con su primera orden de trabajo, materiales planificados y tareas — `routers/projects.py`
- [x] Orden de trabajo en el celular del técnico: llegué / iniciar / avanzar / finalizar; al finalizar descuenta de inventario una sola vez
- [x] Rentabilidad real por proyecto: venta contra equipos consumidos + mano de obra + viáticos + otros
- [x] Informe técnico de entrega imprimible (tareas, materiales, equipos instalados, fotos, tiempos)
- [x] Activos del cliente con serie, ubicación y garantía + aviso 45 días antes de vencer
- [x] Solicitudes de compra: desde el proyecto (lo que falta), automáticas por stock bajo el mínimo, o a mano; al recibirlas entran a bodega con su costo
- [x] Catálogo de proveedor: importa la lista de precios (costo + margen → precio de venta con el tipo de cambio del día), marca, modelo y existencias del proveedor
- [x] Interruptor "Web" en la lista de productos y disponibilidad visible en la tienda (en bodega · bajo pedido · agotado)
- [x] Fila de gerencia en el inicio: embudo, ponderado, por cobrar, utilidad del mes, proyectos, trabajos de la semana, garantías por vencer
- [x] Roles nuevos: supervisor técnico (todo el campo, ve costos) y técnico instalador (solo lo asignado, nunca ve plata)

Eventos / POS / API
- [x] API pública + credenciales + checkout JWT + webhooks salientes · Eventos con entradas QR (venta pública, activación al pagar, check-in con cámara) · POS web (vuelto, pagos mixtos, variantes) · Importador CSV/Excel (clientes, productos, proveedores, facturas históricas; migración desde Fygaro) · vPOS (diferido)

---

## 13. Validación y QA

- Pruebas unitarias en cálculo de totales, impuestos, redondeos v4.4, consecutivos y saldos.
- Pruebas de contrato contra sandbox fiscal: casos de rechazo (CABYS inválido, cédula inválida, exoneración mal referenciada, redondeo, actividad no registrada).
- Pruebas de webhooks ONVO: firma inválida, duplicado, fuera de orden, timeout, monto distinto.
- Pruebas de concurrencia: dos usuarios emitiendo a la vez no duplican consecutivo.
- Pruebas de rendimiento: listados con 50 000 facturas < 300 ms (paginación por cursor, índices).
- Checklist de paridad (sección 12) revisado con el cliente al cerrar cada fase.
- Migración: conteo y suma de facturas/clientes/productos importados = exportación de Fygaro.
- Restauración de backup probada antes de salir a producción.

---

## 14. Riesgos y "no hacer"

- No copiar código, textos, marca ni diseño visual de Fygaro (propiedad intelectual y ToS).
- No hacer scraping del dashboard de Fygaro; migrar solo con exportaciones y XML.
- No confirmar pagos por redirect; solo por webhook.
- No almacenar datos de tarjeta; solo tokens del proveedor.
- No emitir documentos sin respuesta de Hacienda; nunca marcar "aceptado" sin XML de respuesta.
- No prometer vPOS Tap-to-Pay en fases 0–5.
- No expandir alcance fuera de este documento sin registrarlo aquí.
- Riesgo regulatorio: cambios de Hacienda (esquema, TRIBU-CR); mitigación: proveedor fiscal + suscripción a boletines + tests de contrato.
- Riesgo de proveedor (ONVO/fiscal): mitigación: adaptadores intercambiables.
- Riesgo operativo: carpeta en OneDrive; excluir `node_modules` y `.venv` de sincronización o mover el repo.

---

## 15. Estado (2026-09-17)

Sesiones 1-3 ejecutadas (2026-09-17): sesión 3 = Mi Tienda completa (bloques, catálogo, carrito, checkout, cupones, envíos), órdenes → tiquete, recepción de XML de compras, buscador global, API pública con credenciales y checkout JWT, recordatorios y cierre diario por correo. Sesiones 1-2: PDF/correo, inventario, contabilidad, reportes con Excel, recurrencias, ajustes completos (usuarios, invitaciones, pasarelas, métodos, consecutivos), factura electrónica con adapter sandbox + NC + XMLs, webhook ONVO firmado. Sesión 1: `api/` (FastAPI + SQLAlchemy 2 + Alembic, 13 tests verdes), `portal/` (React + Vite, sistema de diseño Crimson, login, dashboard, cotizaciones, facturas, pagos, enlace de pago, clientes, productos, ajustes, página pública de pago), `worker/` (Celery + beat: BCCR, vencimientos), `infra/docker-compose.yml`, Dockerfiles y `railway.toml`. Ver `api/README.md`.

Staging (2026-09-17): desplegado en Railway (`crimson-plataforma`: api, portal, worker, Postgres, Redis) con arranque por invitación; migraciones y los 26 tests verificados en Postgres. URL del portal en `api/README.md`.

Sesión 4 (2026-09-17): cerró todo lo que no depende de credenciales externas (ver §12). 38 tests verdes en SQLite y Postgres.

## 16. Próximos pasos inmediatos

1. Elegir proveedor fiscal (Alanube vs GTI) y abrir sandbox; solicitar credenciales de prueba ONVO.
2. Definir dominio del portal (`app.<dominio>`) y si la landing pasa a Railway.
3. Sesión Cowork 1 (Fase 0.1): scaffold `api/` + `infra/docker-compose.yml`.
4. Validar con el cliente las prioridades 1–3 (cotización/factura, enlace WhatsApp, factura electrónica) y el plan/precio objetivo.
