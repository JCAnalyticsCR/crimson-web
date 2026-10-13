# Paridad de la tienda: Fygaro (tienda.crimsoncr.com) vs. tienda propia

Relevamiento: 2026-10-12. Objetivo: que Andrés pueda apagar Fygaro sin perder ventas, enlaces, datos ni obligaciones con Hacienda.
Alcance: solo páginas públicas de `tienda.crimsoncr.com`, documentación pública de Fygaro (help.fygaro.com, fygaro.com) y el código de este repo.
No se compró, no se enviaron formularios, no se crearon cuentas. `robots.txt` de la tienda prohíbe `/cart` y `/checkout`: no se abrieron; lo que se dice del carrito y del checkout sale del JavaScript público del tema (marcado **[tema]**) y de los términos de Crimson.
Pausas de 1,6–1,8 s entre pedidos. Sin datos personales de clientes en este documento (la tienda no publica reseñas ni nombres de compradores).

Leyenda de la matriz: **EXISTE** = ya lo tenemos y sirve tal cual · **AJUSTE** = existe pero hay que completarlo · **NUEVO** = no existe.
Dueño sugerido: **(a)** tienda↔inventario · **(b)** checkout/pagos/pedidos · **(c)** vitrina/UX/SEO/páginas · **(A)** Andrés (tarea manual fuera del código).

---

## 0. Resumen ejecutivo

1. **La tienda de Fygaro es sencilla**: 435 productos, 7 categorías con 40 subcategorías, sin variantes, sin ofertas/precio tachado, sin reseñas, sin cuentas de cliente, sin banners, sin páginas propias publicadas. Precios en **USD con IVA incluido**. Casi todo lo visible ya lo cubre la vitrina propia.
2. **Lo que de verdad hay que reemplazar está detrás del checkout**: cobro con tarjeta (pasarela del banco vía Fygaro, con 3‑D Secure), métodos manuales con instrucciones (SINPE/transferencia), envío con dirección CR (provincia/cantón/distrito), cupones, nota del pedido, correos al comprador y la **factura electrónica automática** del pedido. Ahí están las brechas críticas (sección 9).
3. **Enlace roto hoy**: la landing (`docs/index.html:638` y `docs/page/solicitud-de-asesoria-personalizada/index.html`) manda a `tienda.crimsoncr.com/page/solicitud-de-asesoria-personalizada/`, que en Fygaro responde **302 → /404** (la página ya no existe). El formulario de asesoría no se pudo relevar; los campos de la sección 7 se toman del formulario equivalente de la landing.
4. **Correo**: `crimsoncr.com` tiene `DMARC p=reject; pct=50` y **no** hay registro DKIM de Resend (`resend._domainkey`). Hoy los correos transaccionales de la plataforma con remitente `@crimsoncr.com` serían rechazados en parte. Bloqueante para el corte (confirmaciones de pedido y facturas).
5. **SEO/compartir**: Fygaro sirve `og:title`/`og:image`/JSON‑LD de producto en el HTML. La vitrina propia es una SPA: WhatsApp/Facebook/Google no ejecutan JS, así que los enlaces compartidos saldrán sin foto ni título y las redirecciones de URLs viejas son solo del lado del cliente (no 301). Hay que resolverlo antes del cambio de DNS.

---

## 1. Infraestructura actual (observada)

| Elemento | Valor | Nota |
|---|---|---|
| `tienda.crimsoncr.com` | CNAME `shops3.fygaro.com` (AWS, openresty/Express) | Lo único que hay que mover en DNS para la tienda |
| `www.crimsoncr.com` | CNAME `jcanalyticscr.github.io` | Landing en GitHub Pages (ya fuera de Fygaro) |
| `crimsoncr.com` | A → GitHub Pages | |
| Correo | MX `mail.crimsoncr.com` (A2 Hosting); SPF `+mx +a +ip4:68.66.226.86 include:spf.a2hosting.com include:mailchannels.net ~all`; DMARC `p=reject; pct=50` | Falta autorizar el remitente de la plataforma (Resend: DKIM + SPF del subdominio de envío) |
| Imágenes | `fygaro-subscribers.s3.amazonaws.com/<id-negocio>/products/<n>/…` (original) y `/t/…` (miniatura) | Se borran al cancelar Fygaro: descargar antes (`fygaro_crawl --images`) |
| Plantilla | tema "typewr" de Fygaro (jQuery 3.6, Swiper 11, Font Awesome 6.4, Google Fonts Montserrat/Lato) | Colores `--primary #dc143c`, `--secondary #000000` |
| Seguridad | HSTS `max-age=31536000; includeSubdomains; preload`, `X-Frame-Options SAMEORIGIN`, reCAPTCHA v3 (clave pública en el HTML) | Al migrar, servir HTTPS desde el primer minuto (HSTS ya está en navegadores de clientes) |
| 404 | Cualquier ruta inexistente responde **200 con la portada** ("soft 404"); `/page/<x>` inexistente → 302 a `/404` (también 200) | Mala práctica que no hay que copiar |

---

## 2. Relevamiento de la tienda pública

### 2.1 Mapa de URLs (sitemap.xml + navegación)

| Patrón Fygaro | Qué es | En sitemap |
|---|---|---|
| `/` | Portada = listado de productos (12 por página) | sí (prio 1) |
| `/products/` y `/products/?page=N` | Tienda completa (37 páginas) | sí |
| `/products/<uuid>/` | Ficha de producto (435) | sí, con `lastmod` |
| `/products/category/<id>/` (`?page=N`) | Listado por categoría (7) | sí |
| `/products/search/?keywords=&category=&subcategory=&page=&sort=` | Buscador, filtros por categoría/subcategoría, orden | no |
| `/terms` y `/terms/` | Términos y condiciones | sí |
| `/policies` y `/policies/` | Políticas de privacidad | sí |
| `/page/<slug>/` | Páginas propias del constructor (hoy ninguna publicada) | no |
| `/cart/`, `/cart/add/` (POST) | Carrito | prohibido en robots |
| `/checkout/…` (`/checkout/countries/`, `/checkout/country/<id>/provinces/`, `/checkout/province/<id>/cantons/`) | Checkout y catálogos de dirección | prohibido en robots |

### 2.2 Portada y navegación
- Cabecera: logo, menú vacío (sin páginas), botón "Tienda", buscador desplegable, carrito. Menú móvil con "Tienda" y "Carrito".
- **Sin banners, sin hero, sin bloques**: la portada es directamente la grilla de productos con barra lateral (buscador + categorías) y orden.
- Pie: logo, sello "Secure Transaction", "Enlaces rápidos" (Inicio, Tienda), "Políticas" (Términos, Privacidad), "¡Síguenos!" (Facebook `facebook.com/crimsonconcr`, Instagram `instagram.com/cr.crimsoncr`), "© 2026", "Creado con FYGARO".
- **No hay** botón de WhatsApp, teléfono, correo ni dirección visibles en la tienda (solo dentro de Términos/Privacidad).
- Idioma: solo español (`lang="es"`); moneda única **USD** (símbolo `$`); sin selector de idioma ni de moneda.

### 2.3 Categorías y subcategorías
Siete categorías (ids de Fygaro), productos por categoría según el listado: Videovigilancia (12755) 165 · Seguridad y Acceso (13657) 94 · Redes (12836) 76 · Herramientas (14090) 48 · Almacenamiento (12981) 21 · Energía y respaldo (12837) 21 · Colaboración (12849) 2. Ocho productos no aparecen en ningún listado de categoría.

Las **subcategorías** solo aparecen en la barra lateral del buscador (`/products/search/?category=<id>`), no en `/products/category/<id>/`. El crawler ahora las recorre (ver sección 2.11). Detalle:

| Categoría (id) | Subcategorías (id) · productos |
|---|---|
| Almacenamiento (12981) | Discos Duros Internos (9930) 12 · Memorias SD (9931) 8 · Discos Duros Externos (9932) 1 |
| Colaboración (12849) | Headsets (9849) 0 · Pantallas Interactivas (11450) 2 · Videoconferencia (11451) 0 · Salas de Reuniones (11452) 0 |
| Energía y respaldo (12837) | UPS (9840) 18 · Cables (9841) 0 · Baterias (9842) 0 · Regletas y protectores (9843) 1 · Accesorios (9844) 2 |
| Herramientas (14090) | Ferreteria (11640) 45 |
| Redes (12836) | Switches (9827) 22 · Routers (9828) 11 · Access Point (9829) 16 · Cables (9830) 7 · Accesorios (9831) 12 · Fibra Optica (9832) 0 · Gabinetes y Racks (9833) 5 · Antenas y Enlaces WiFi (10306) 3 |
| Seguridad y Acceso (13657) | Control de acceso (10784) 33 · Accesorios (10785) 19 · Cables (10786) 0 · Agujas Vehiculares (11453) 0 · Videoporteros (11454) 6 · Lectores (11455) 3 · Alarmas (11456) 13 · Sensores (11457) 16 |
| Videovigilancia (12755) | Camaras IP (9775) 67 · Cámaras Analogas (9776) 16 · Camaras WIFI (9834) 32 · Grabadores IP (NVR) (9835) 13 · Grabadores Análogos (DVR) (9836) 17 · Pantallas para Monitoreo (9837) 0 · Cables (9838) 1 · Accesorios (9839) 8 · Kits (9912) 3 · Camaras Vehiculares (9917) 10 · Monitores y Pantallas (11458) 1 |

40 subcategorías, 10 vacías (no publicarlas: una subcategoría vacía es un callejón sin salida). 16 productos sin subcategoría y 4 en dos subcategorías. Nombres a normalizar al importar ("Camaras"/"Cámaras", "Baterias", "Ferreteria", "Fibra Optica"; "Cables" y "Accesorios" se repiten en varias categorías y deben colgar de su padre).

Snapshot nuevo con subcategorías y precios al día: `infra/fygaro/catalogo-fygaro-2026-10-12.json` (mismos 435 productos que el del 08‑10; 11 precios subieron entre 0,2 % y 1,5 %: Fygaro sigue ajustando precios, así que la importación final debe correr cerca del corte). El del 08‑10 se conserva porque el importador ya lo usó.

### 2.4 Buscador, filtros, orden y paginación
- Buscador por texto (`keywords`), combinable con categoría y subcategoría. Búsqueda "camara" → 10 páginas.
- Filtros: solo categoría/subcategoría (no hay filtro por precio, marca ni disponibilidad). Chip de filtros activos con "Limpiar todo".
- Orden (`sort`): `0` Destacados · `1` Precio: mayor a menor · `2` Precio: menor a mayor. Solo aplica en `/products/search/`.
- Paginación clásica "« Anterior · Página X de N · Siguiente »", 12 por página.

### 2.5 Ficha de producto
- Migas: Inicio › Tienda › nombre.
- Galería: imagen principal + miniaturas (abajo), estilo "grid‑sticky", **lightbox** con navegación y contador. 382 productos con 1 foto, 45 con 2–5, 8 sin foto (placeholder).
- Título, **precio con IVA** (p. ej. $42.86 = 37.93 + 13 %). Sin precio tachado/oferta, sin texto "IVA incluido", sin SKU visible, sin existencias visibles.
- Cantidad con − / + (mínimo 1) y botón "Agregar al Carrito" (POST `/cart/add/`). `data-has-followup="true"`: al agregar, el tema muestra un aviso de seguimiento **[tema]**.
- "Descripción" (solo si existe; 16 productos sin descripción).
- "Items Relacionados": 4 productos (misma categoría).
- **No hay**: compartir, WhatsApp, reseñas, preguntas, ficha técnica/PDF, "agotado" (los 435 están `InStock`), variantes (0 productos con opciones; el tema las soporta con precio y existencia por versión **[tema]**).

### 2.6 Carrito y checkout (deducido del tema, sin abrir)
- Carrito: cambiar cantidad, eliminar línea, **nota del pedido** (`cart-note`), **cupón** (`toggle-coupon`) **[tema]**.
- Checkout por pasos: (1) cliente: nombre, apellido, correo, teléfono, país y dirección de facturación; casilla "enviar a otra dirección" con país/dirección de envío; cascada **país → provincia → cantón → distrito** y código postal según país **[tema]**. (2) envío: elección del servicio (`data-fy-checkout-shipment`) **[tema]**. (3) pago **[tema]**:
  - Tarjeta por la pasarela del banco (`subscriber gateway`) con **3‑D Secure** (Cardinal Commerce, iframe 3DS).
  - **Cuotas** ("Pay in installments", tasa cero por emisor) y **pago con puntos** si el banco lo ofrece.
  - **PayPal** (Smart Buttons), Yappy y Lynk (Panamá).
  - **Métodos personalizados** (`subscriber_payment_method`) con instrucciones: aquí entran SINPE Móvil / transferencia.
  - Cobro en otra moneda con tipo de cambio (aviso "el total a pagar en <moneda>").
  - reCAPTCHA v3 como escudo anti‑bots en el pago.
- Qué está activo en Crimson no se puede ver sin entrar al checkout. Los términos de Crimson dicen: "tarjeta de crédito, Transferencia (SINPE, Otros) y/o Paypal". **Andrés debe confirmar en Ajustes → eCommerce y Pagos** qué pasarela/banco, si PayPal está activo, qué métodos manuales y qué tarifas de envío tiene configuradas (sección 8.1).

### 2.7 Envíos, devoluciones y pagos (según los términos publicados)
- Pagos: tarjeta, transferencia (SINPE u otros), PayPal. Precios en USD, sujetos a cambio sin aviso.
- Gestión de pedidos L–V 09:00–18:00; fuera de horario o feriado → siguiente día hábil.
- Entregas nacionales e internacionales "sujeto a disponibilidad"; plazo según servicio y lugar.
- Reclamo por daño de transporte: anotarlo al recibir y avisar por correo en **12 horas**.
- Devoluciones/reembolsos: pedir dentro de **3 días naturales** desde la factura; 5 motivos aceptados; requisitos (número de factura, descripción, motivo, forma de pago); reembolso en 5–10 días hábiles tras aprobarse. Anulación de pedidos dentro de 12 h si no se facturó ni despachó.
- Inconsistencias a corregir al migrar (son textos de Crimson): plazo de reclamo 12 h vs. devolución 3 días; motivo 5 habla de "color o talla… con mal olor" (plantilla de ropa); Términos citan "www.crimsoncr.com" como sitio; Privacidad da la dirección "Desamparados, Alajuela 20110" y teléfono con formato distinto. Conviene que un abogado revise antes de publicarlos en la tienda nueva.

### 2.8 Cupones
- Hay campo de cupón en el carrito **[tema]**. Según la ayuda de Fygaro: código promocional, fecha de inicio, fecha de vencimiento, activo/inactivo; requiere pagos en línea activos. No documentan tipo de descuento, mínimos ni límites de uso. No se sabe si Crimson tiene cupones vigentes → **(A)** exportar la lista.

### 2.9 Páginas propias y formularios
- **No hay páginas propias publicadas** (menú vacío; `/page/solicitud-de-asesoria-personalizada/` y `/page/contacto/` → 302 `/404`). No hay archivo en Wayback Machine.
- El tema soporta formularios propios (POST por AJAX con reCAPTCHA y `external_id`) **[tema]**.

### 2.10 SEO, analítica y rendimiento
- `<title>`: portada "Crimson Consulting"; ficha = nombre en "Title Case" (p. ej. "Camara Hikvision 2Mp Color Vu…"); listados "Search Results" / "Videovigilancia products" (en inglés).
- **Sin `meta description`** en ninguna página. Sin `canonical`. `og:title`, `og:site_name`, `og:type` (`product` en fichas), `og:image` + `twitter:card summary_large_image` en fichas.
- JSON‑LD `Product` en fichas: `sku`, `name`, `offers.price` (**sin IVA**, distinto del precio exhibido), `priceCurrency USD`, `image`, `availability`. Sin `brand`, `description`, `url` ni `BreadcrumbList`.
- `sitemap.xml` con portada, tienda, 7 categorías, 435 productos (`lastmod`), términos y privacidad. `robots.txt` bloquea carrito y checkout.
- Analítica visible: **Google Analytics 4 `G-0PQ0N2M5TF`** (gtag). Sin Meta Pixel, sin GTM. reCAPTCHA v3 de Google.
- Rendimiento: HTML 15–27 KB, respuesta 0,3–0,5 s (sin caché de navegador por ETag débil). Bloquean render: 3 CSS de Font Awesome, Swiper CSS, 2 CSS del tema, 2 Google Fonts; jQuery + Swiper + tema al final. Imágenes sin `srcset`, originales JPG/PNG/WebP (~24 KB la principal medida, miniatura ~18 KB: casi igual de pesada). Lazy‑load en listados; imagen principal `fetchpriority=high`.

### 2.11 Calidad del catálogo (para el importador)
- 435 productos, todos en USD, todos "disponibles" (Fygaro no muestra existencias reales).
- IVA deducido: 384 al 13 %, 23 al 0 % (¿exentos o error?), y 20 con tasas raras 11,8–14 % por redondeo de precios → revisar CABYS/IVA (el importador ya los marca).
- SKU presente en todos; 8 sin foto; 16 sin descripción; precios de $0,19 a $7.664,36 (mediana $103,40).
- `lastmod`: 318 productos editados por última vez en marzo 2026 y 97 en febrero: el catálogo de Fygaro **no** está al día con el inventario real (motivo del proyecto).

---

## 3. Funciones de la plataforma Fygaro (documentación pública)

Fuente: help.fygaro.com (categorías Tienda Web, Ajustes Generales, Facturación, Primeros Pasos), fygaro.com/es/w/shops, PLAN_PLATAFORMA.md §2. "Visible en Crimson" = se ve en la tienda pública o en sus términos.

| Función Fygaro | Qué hace (según la documentación) | Visible en Crimson |
|---|---|---|
| Constructor visual de páginas | Páginas (Inicio, FAQ…), secciones, videos, productos en portada, formularios | No (sin páginas publicadas) |
| Dominio propio | CNAME a `shopsN.fygaro.com` | Sí |
| Categorías y subcategorías | Árbol de dos niveles, "mostrar en sitio web" | Sí |
| Opciones de producto (variantes) | Tipos (talla, color…) y valores; versiones con precio/existencia | No (0 productos) |
| Inventario conectado a ventas en línea | Un inventario "eCommerce" descuenta existencias y puede bloquear la venta sin stock | No se ve (todo "InStock") |
| Cupones | Código, inicio, vencimiento, activo | Campo en carrito; uso real desconocido |
| Tarifas de envío personalizadas | Zonas (países/provincias) → servicios (nombre, proveedor, días, moneda, precio) con condición por monto o peso, o sin condición | Probable (términos hablan de envío); configuración desconocida |
| Couriers integrados | Correos de Costa Rica (Pymexporta) y DHL Express con recargo (overhead) | Desconocido |
| Pasarela de tarjetas | Banco local adquirente o Credix; PCI‑DSS, tokenización, 3DS 1 y 2; USD 0,25 por transacción | Sí (términos + tema) |
| Cuotas / puntos | Por emisor, en el checkout | Desconocido |
| PayPal | Smart Buttons, pagar sin cuenta | Mencionado en términos |
| Yappy / Lynk | Panamá | No aplica |
| Métodos de pago personalizados | Nombre + instrucciones (se muestran al confirmar y van por correo) | Sí (SINPE/transferencia según términos) |
| Links de pago | Enlace/botón de pago por producto o monto | Plataforma (no en tienda) |
| Factura electrónica CR | FE/TE/NC/ND v4.4, CABYS, código de actividad, contingencia, recepción de XML, consecutivos | Plataforma; los términos citan "número de factura enviado por email al completar el pago" → la compra en línea emite comprobante automático |
| Correos | Factura/cotización al cliente, copia BCC al comercio, marca de "visto" | Probable confirmación de pedido + factura |
| Cuentas de cliente | No hay inicio de sesión en la tienda | No |
| Políticas legales | Términos, devoluciones, privacidad en Ajustes → eCommerce y Pagos | Sí (`/terms`, `/policies`) |
| Google Analytics / Meta Pixel | Integración por ID | GA4 sí; Pixel no |
| Redes sociales | Enlaces en el pie | Facebook, Instagram |
| Importar productos / inventario desde Excel | Plantillas Excel | Plataforma |
| API | Solo pagos: checkout JWT, webhook HMAC, reembolso; sin API de órdenes/productos | Plataforma |
| Reportes | Facturación, pendientes, IVA, cierre diario, transacciones, órdenes, inventario, venta de productos… | Plataforma |
| Roles, usuarios, 2FA, BCC | Administración | Plataforma |
| Recuperación de carritos abandonados, reseñas, lista de deseos, multimoneda en vitrina | No documentado | No |

---

## 4. Matriz de paridad (Fygaro → tienda propia)

Rutas relativas a la raíz del repo. Líneas a la fecha del commit `f7681e6`.

### 4.1 Vitrina y catálogo

| Función | Estado | Evidencia / qué falta | Dueño |
|---|---|---|---|
| Listado de productos con foto, nombre y precio | EXISTE | `api/app/routers/store.py:489-525`, `portal/src/modules/store/PublicStore.tsx:130-145` | — |
| Precio exhibido con IVA | EXISTE | `_gross` y `display_price` `store.py:391-431` | — |
| Moneda USD como Fygaro | AJUSTE | La tienda convierte todo a la moneda configurada (`Pricer`, `store.py:372-388`; default `CRC`, `store.py:40`). Decidir con Andrés: mantener USD (términos dicen USD) o pasar a colones y actualizar términos | (c)+(A) |
| Categorías | EXISTE | `store.py:458-468` (solo con productos), filtro `category_id` `store.py:507-508` | — |
| Subcategorías | AJUSTE | El modelo tiene `Category.parent_id` (`api/app/models/catalog.py:29`) pero la API pública y la vitrina no lo usan; el importador solo toma la primera categoría (`api/app/services/fygaro_import.py:223-254`). Usar `products[].subcategories` del nuevo JSON | (a)+(c) |
| Buscador | EXISTE | Sin tildes, todas las palabras, nombre/código/marca/modelo/categoría `store.py:511-514` | — |
| Orden (destacados / precio ↑ / precio ↓) | EXISTE | `sort=name|price_asc|price_desc|recent` `store.py:495`; selector `PublicStore.tsx:239-240`. Falta "Destacados" (orden manual) | (c) |
| Paginación | EXISTE | `limit/offset` + `X-Total-Count`, "Ver más" `PublicStore.tsx:141-145` | — |
| Ficha: galería | EXISTE | `PublicStore.tsx:410-411`. Falta lightbox/zoom | (c) |
| Ficha: cantidad y agregar al carrito | EXISTE | `PublicStore.tsx:100-111` | — |
| Ficha: descripción | EXISTE | `description_store` o de factura `store.py:405` | — |
| Ficha: relacionados (4, misma categoría) | EXISTE | `store.py:534-541` | — |
| Ficha: variantes | EXISTE | `ProductVariant` (`api/app/models/extra.py:51`), `store.py:352-359`, `PublicStore.tsx:426` | — |
| Disponibilidad real | EXISTE (mejor que Fygaro) | `store_availability` en bodega / bajo pedido / consultar `api/app/routers/catalog.py:263-271` | (a) valida datos |
| WhatsApp (consulta/cotización) | EXISTE (no lo tenía Fygaro) | `PublicStore.tsx:196, 438, 305` | — |
| Compartir producto | NUEVO | No hay botón compartir; y sin metadatos OG del lado servidor el enlace compartido sale sin foto (ver 4.4) | (c) |
| Precio tachado / ofertas | NUEVO (opcional) | Fygaro tampoco lo usa | (c) |
| Fotos alojadas fuera de Fygaro | AJUSTE | El importador baja y re‑aloja fotos solo de `fygaro-subscribers` (`fygaro_import.py:18, 40`). Verificar que **todas** queden en nuestro almacenamiento antes de cancelar Fygaro | (a) |

### 4.2 Carrito, checkout y pedidos

| Función | Estado | Evidencia / qué falta | Dueño |
|---|---|---|---|
| Carrito persistente | EXISTE | `localStorage` por tienda `PublicStore.tsx:92-94`; precio recalculado en servidor `store.py:578-649` | — |
| Nota del pedido | EXISTE | `notes` en `CheckoutIn` `store.py:575` | — |
| Cupón en checkout | EXISTE | `PublicStore.tsx:557`, `valid_coupon` `store.py:209-222` | — |
| Datos del comprador | AJUSTE | Nombre, correo, teléfono, cédula, dirección (texto libre) `PublicStore.tsx:467, 493`; `Order.contact` JSON `api/app/models/store.py:52`. Falta dirección estructurada **provincia/cantón/distrito** + señas, y tipo de identificación para FE | (b) |
| Tarifas de envío | AJUSTE | Fijas o por kg estilo Correos + recargo % (`store.py:727-734`; editor `portal/src/modules/store/Store.tsx:73-85`). Falta: zonas (GAM/fuera de GAM/provincias), condición por monto (envío gratis desde X), plazo estimado, retiro en tienda con dirección | (b) |
| Integración Correos de CR / DHL (guía, rastreo) | NUEVO | No hay integración con courier; tampoco número de guía en `Order` | (b) |
| Métodos manuales (SINPE, transferencia, efectivo) con instrucciones | EXISTE | `api/app/routers/settings.py:33-37`, `store.py:44-49`, instrucciones en la respuesta `store.py:722` | — |
| **Pago con tarjeta en línea** | NUEVO | El checkout de tienda solo registra el método elegido (`store.py:668-724`). ONVO existe solo para facturas: adaptador sin activar (`api/app/providers/payments.py:25-76`), webhook aplica pagos únicamente con `metadata.invoice_id` (`api/app/routers/webhooks.py:57-79`), página de pago con `onvo: False` (`api/app/routers/public.py:53`) | (b) |
| 3‑D Secure / cuotas / puntos | NUEVO | Depende de lo que ofrezca ONVO (o el banco) | (b)+(A) |
| PayPal | NUEVO (si Andrés lo usa) | Solo existe el valor `paypal` en modelos (`api/app/models/payments.py:39`) | (b)+(A) |
| Reserva/descuento de existencias al vender en línea | AJUSTE | El inventario se descuenta solo al convertir la orden en comprobante (`store.py:331-332`, `api/app/services/inventory.py:77`). El checkout no valida existencias ni reserva: dos compradores pueden llevarse la última unidad | (a) |
| Bloquear venta sin existencias | AJUSTE | La vitrina muestra "Consultar disponibilidad" pero permite agregar; decidir política (vender bajo pedido vs. bloquear) | (a)+(A) |
| Número de pedido y estados | EXISTE | `ORD` consecutivo `store.py:677`; estados nuevo/pagado/preparando/enviado/entregado/cancelado `store.py:271-286`; pantalla `portal/src/modules/store/Orders.tsx` | — |
| Pedido → factura electrónica / tiquete | AJUSTE | Manual desde Órdenes `store.py:289-338`. En Fygaro el comprobante sale al pagar. Automatizar al confirmarse el pago (con FE si el comprador da cédula, TE si no) | (b) |
| Correo de confirmación al comprador | NUEVO | `store_checkout` no envía correo (`store.py:668-724`); solo el formulario de contacto avisa a admins (`store.py:744-767`) | (b) |
| Aviso de pedido nuevo al comercio | NUEVO | Igual que arriba | (b) |
| Correo de pago recibido / enviado / entregado | NUEVO | `order_status` solo activa entradas de eventos (`store.py:281-283`, `api/app/routers/events.py:337`) | (b) |
| Página "mi pedido" (seguimiento sin cuenta) | NUEVO | No existe; Fygaro tampoco tiene cuentas, pero manda correos | (b) |
| Reembolsos | AJUSTE | `OnvoAdapter.refund` existe (`payments.py`), sin flujo desde la orden | (b) |
| Anti‑bots en checkout | AJUSTE | Límite por IP `store.py:670`; Fygaro usa reCAPTCHA v3. Evaluar Turnstile/hCaptcha si aparecen pedidos basura | (b) |
| Aceptación de términos en checkout | NUEVO | Ni Fygaro lo pide explícito; recomendable casilla "acepto términos y política de devoluciones" | (b)/(c) |
| Carritos abandonados | NUEVO (opcional) | Fygaro no lo ofrece | — |

### 4.3 Cupones

| Función | Estado | Evidencia | Dueño |
|---|---|---|---|
| Código, % o monto, mínimo de compra, vigencia, máximo de usos, activo | EXISTE (supera a Fygaro) | `store.py:157-222`, modelo `api/app/models/store.py:27-40` | — |
| Conteo de usos sin carrera | AJUSTE | `coupon.uses += 1` sin bloqueo `store.py:709-710` | (b) |
| Cupón por producto/categoría | NUEVO (opcional) | No en ninguno de los dos | — |

### 4.4 Páginas, SEO y medición

| Función | Estado | Evidencia / qué falta | Dueño |
|---|---|---|---|
| Páginas propias con bloques | EXISTE | `store.py:89-153`, editor `Store.tsx`, render `Blocks.tsx` | — |
| Formulario en página | AJUSTE | El bloque `form` solo envía nombre/correo/teléfono/mensaje (`portal/src/modules/store/Blocks.tsx:97-120`); campos extra se pierden. Para la asesoría hacen falta tipo de proyecto, entorno y plazo (sección 7) | (c) |
| Términos y privacidad | EXISTE | Importados de Fygaro (`fygaro_import.py:375-388`), modal en la vitrina `PublicStore.tsx:283-284, 313` | (c) revisa texto |
| Política de envíos y devoluciones como página propia | AJUSTE | Hoy está dentro de Términos; conviene página separada (Google Merchant la exige) | (c) |
| `title` y `meta description` por vista | EXISTE (cliente) | `PublicStore.tsx:159-170` | — |
| `og:image` / `og:title` / JSON‑LD visibles para bots | NUEVO | Solo se escriben con JS; WhatsApp/Facebook/Google no los ven. Hace falta HTML pre‑renderizado o un endpoint que inyecte `<head>` por producto (nginx → API) | (c) |
| `sitemap.xml` y `robots.txt` de la tienda | NUEVO | No existen para `tienda.*` | (c) |
| Redirecciones de URLs de Fygaro | AJUSTE | `portal/src/modules/store/StoreHost.tsx:56-64` + `store.py:545-560` traducen en el navegador (no 301). Faltan: `/page/<slug>/` → página propia (hoy cae en portada), `subcategory=` y `page=` del buscador, `sort=`. Para Google, convertirlas en **301 del servidor** | (c) |
| Canonical | NUEVO | Mismo producto accesible como `?p=` en cualquier host | (c) |
| 404 real | NUEVO | Ruta desconocida → portada (`StoreHost.tsx:63`), igual que el soft‑404 de Fygaro. Devolver 404/410 a lo inexistente | (c) |
| Google Analytics 4 | NUEVO | No hay gtag en el portal. Si Andrés quiere continuidad, reutilizar `G-0PQ0N2M5TF` (con aviso de cookies) | (c)+(A) |
| Redes sociales en el pie | AJUSTE | La vitrina tiene WhatsApp; faltan Facebook/Instagram (están en Ajustes `social`, `settings.py:71`) | (c) |
| Indexar solo en dominio propio | EXISTE | `robots` meta `PublicStore.tsx:168`, `portal/nginx.conf:1-4` | — |

### 4.5 Administración y plataforma

| Función Fygaro | Estado | Evidencia | Dueño |
|---|---|---|---|
| Productos, categorías, impuestos, CABYS | EXISTE | `api/app/routers/catalog.py`, `cabys_masivo.py` | — |
| Inventarios por bodega, ajustes, Excel | EXISTE | `api/app/services/inventory.py`, `api/app/routers/imports.py` | (a) |
| Factura electrónica CR (FE/TE/NC/ND, recepción) | AJUSTE | Contrato y sandbox `api/app/providers/einvoice.py`; proveedor real (Alanube/GTI) pendiente (PLAN_PLATAFORMA §1) | (b)+(A) |
| Links de pago | AJUSTE | Por factura `api/app/routers/sales.py:561`; cobro en línea pendiente de ONVO | (b) |
| POS | EXISTE | `api/app/routers/pos.py:84` | — |
| API pública + webhooks salientes | EXISTE (supera a Fygaro) | `api/app/routers/public_api.py` | — |
| Reportes (ventas, IVA, inventario) | EXISTE | `api/app/services/reports.py` | — |
| Usuarios, roles, 2FA | EXISTE | `api/app/routers/auth.py`, `settings.py` | — |
| BCC de facturas al comercio | AJUSTE | Verificar en `services/mail.py` | (b) |
| Remitente de correo autorizado | NUEVO (bloqueante) | `services/mail.py:61-63` ya advierte del DMARC `p=reject`; falta DKIM de Resend en DNS | (A)+devops |

---

## 5. URLs públicas de Fygaro a redirigir

Regla: **301 del lado del servidor** (nginx del portal o un endpoint del API) para que Google traslade la autoridad; la traducción en el navegador (`StoreHost.tsx`) queda como respaldo. El mapa `uuid → id` y `id categoría → id` lo deja el importador en `tenant.settings["fygaro"]` (`fygaro_import.py:395`).

| Patrón viejo | Destino nuevo | Estado hoy |
|---|---|---|
| `/` | portada de la tienda | ok |
| `/products/` · `/products/?page=N` | catálogo (`/`) | ok (cae en `*`) |
| `/products/<uuid>/` (435) | ficha `?p=<id>`; si el producto ya no está → categoría o catálogo con **410** si se dio de baja | ok en cliente; falta 301 |
| `/products/category/<id>/` · `?page=N` | `?c=<id>` | ok en cliente; falta 301 |
| `/products/search/?keywords=<q>` | `?q=<q>` | ok en cliente |
| `/products/search/?category=<id>&subcategory=<sid>` | `?c=<id propio de la subcategoría>` | **falta**: hoy ignora `subcategory` y pierde `keywords` si viene con categoría |
| `/products/search/?…&sort=1|2` | `orden=price_desc|price_asc` | falta |
| `/terms` · `/terms/` | términos | ok en cliente |
| `/policies` · `/policies/` | privacidad | ok en cliente |
| `/page/<slug>/` (en especial `solicitud-de-asesoria-personalizada`) | `?pg=<slug>` (página propia con el mismo slug) | **falta**: hoy va a portada |
| `/cart/` · `/cart/*` | carrito (abrir cajón) o portada | ok (portada) |
| `/checkout/*` | portada (con aviso "tu carrito anterior no se trasladó") | ok (portada) |
| `/sitemap.xml` | sitemap propio | **falta** |
| `/robots.txt` | robots propio (bloquear carrito/checkout/API) | **falta** |
| `https://fygaro-subscribers.s3.amazonaws.com/…` (fotos enlazadas desde fuera) | no controlable: re‑alojar fotos y actualizar referencias (la landing `Crimson Hero Ideas.dc.html` y `Crimson 1c Pagina.dc.html` usan el logo de ese bucket) | **riesgo** al cancelar |

Enlaces propios que apuntan a la tienda y deben seguir funcionando: `docs/index.html:638` (Tienda, Asesoría), `docs/404.html:46`, `docs/products/index.html`, `docs/page/solicitud-de-asesoria-personalizada/index.html` (redirige a la tienda: hoy termina en el 404 de Fygaro).

---

## 6. Contenido de las páginas propias de Crimson (resumen)

Texto completo ya importado en `catalogo-fygaro-*.json → legal.terms / legal.privacy` y cargado por el importador en Mi Tienda → legal.

**Términos y condiciones** (Grupo Empresarial Crimson SRL "El Comercio"):
1. Aceptación por uso del sitio.
2. Devoluciones y reembolsos: solicitud en ≤ 3 días naturales desde la factura; motivos: sin inventario y sin sustituto de igual o mayor valor, entrega > 3 días hábiles, extravío del courier, cobro incorrecto (por la diferencia), producto equivocado o dañado; casos no previstos se revisan uno a uno. Requisitos: número de factura, descripción, motivo, forma de pago. Envío de vuelta puede correr por el cliente. Reembolso 5–10 días hábiles; tarjeta según el emisor.
3. Envíos: anotar incidencias al recibir y avisar en ≤ 12 h; reclamos en ≤ 12 h o se da por conforme; devolución a cargo del cliente; horario de gestión L–V 9:00–18:00; pedidos fuera de horario o feriado al siguiente día hábil; debe haber quien reciba.
4. Servicios: anulación ≤ 12 h si no se facturó ni prestó.
5. Generales: pago total; medios (tarjeta, SINPE/transferencia, PayPal); datos completos de entrega; entregas nacionales e internacionales sujetas a cobertura; cancelación ≤ 12 h; responsabilidad por uso no autorizado de tarjetas y contracargos; Ley de Protección al Consumidor; SSL; **precios en USD**; confidencialidad y casos de revelación de datos.
6. Propiedad intelectual (a–e), obligaciones del usuario (a–g), legislación y tribunales de Costa Rica.
7. Contacto y notificaciones: (506) 8888‑9892, info@crimsoncr.com.

**Política de privacidad**: cookies y analítica; finalidades (inteligencia de negocio, relación contractual y garantía, transferencia a terceros/proveedores, contacto por teléfono/correo/SMS/WhatsApp/redes, mercadeo, promoción, transferencia a relacionadas); derechos de acceso, rectificación, cancelación, oposición y portabilidad; revocación del consentimiento; transferencias internacionales; revelación por orden judicial; contacto info@crimsoncr.com, (506) 888‑89892, Desamparados, Alajuela 20110.

Observaciones: falta una política de envíos con tarifas/plazos concretos y una política de garantía (la landing ofrece "garantía y soporte"); la privacidad debe citar la Ley 8968 y PRODHAB si se va a usar GA4/WhatsApp para mercadeo.

---

## 7. Formulario de "Solicitud de asesoría personalizada"

La página de Fygaro ya no existe (302 → /404) y no hay copia archivada, así que **no se pudieron leer sus campos**. El equivalente vigente es el formulario `#form` de la landing (`docs/index.html`, envía por WhatsApp desde `docs/assets/main.js:747-759`). Campos a replicar en la página propia `solicitud-de-asesoria-personalizada` de la tienda:

| Campo | Tipo | Obligatorio | Opciones |
|---|---|---|---|
| Tipo de proyecto (`tipo`) | radio | sí | Videovigilancia · Redes e infraestructura · Control de acceso · Respaldo eléctrico / UPS · Proyecto integral |
| Nombre (`nombre`) | texto | sí | |
| Teléfono (`telefono`) | tel | sí | |
| Correo (`email`) | email | no | |
| ¿Dónde se implementaría? (`entorno`) | select | no | Casa · Negocio · Condominio · Oficina · Otro |
| ¿En qué plazo? (`plazo`) | select | no | Inmediato · Próximas semanas · En evaluación · A futuro |
| Mensaje (`mensaje`) | texto largo | no | |

Recomendación (c): que el bloque `form` acepte campos configurables y los guarde como oportunidad/nota en CRM además de avisar por correo y abrir WhatsApp; hoy descarta lo que no sea nombre/correo/teléfono/mensaje (`Blocks.tsx:107`).

---

## 8. Plan de corte (apagar Fygaro)

### 8.1 Antes del corte: lo que Andrés debe sacar del panel de Fygaro (no se puede hacer por scraping)
Guardar todo fuera de OneDrive personal, en una carpeta de la empresa con respaldo.
1. **Configuración** (capturas o texto): Ajustes → eCommerce y Pagos (pasarela/banco, PayPal, métodos personalizados con sus instrucciones, tarifas de envío por zona/servicio, couriers, inventario eCommerce, textos legales); cupones vigentes; usuarios y roles.
2. **Órdenes de la tienda** (todas, con estado, método de pago y envío) → Reportes → Órdenes.
3. **Clientes** (listado con identificación, correo, teléfono, dirección) → Clientes → exportar.
4. **Cobros/transacciones** (aprobadas, declinadas, reembolsos, contracargos abiertos) → Reportes → Transacciones / Pagos; y los estados de cuenta del banco adquirente.
5. **Facturas y comprobantes electrónicos**: todas las FE/TE/NC/ND emitidas **con XML firmado y XML de respuesta de Hacienda**, más PDF; recepciones (XML de compras) y su aceptación. Obligación de conservación: no depender del panel de Fygaro después de cancelar.
6. **Facturas pendientes de cobro**, recurrencias y links de pago activos (para recrearlos).
7. **Productos e inventario** (Excel "Reporte de Inventario") para conciliar contra el importador.
8. **Consecutivos de Hacienda**: último número usado por tipo de documento, sucursal y terminal. El proveedor nuevo debe continuar sin repetir (o usar otra terminal).
9. **Credenciales de Hacienda** (usuario/contraseña ATV y llave criptográfica + PIN): cargarlas directamente en el proveedor nuevo; nunca por chat ni correo.
10. **Fotos**: correr `python -m app.tools.fygaro_crawl <carpeta> --images` y confirmar que el importador las re‑alojó.

### 8.2 Pagos con tarjeta
- Confirmar con Fygaro/banco quién es el adquirente (la afiliación con el banco suele ser de Crimson, no de Fygaro) y si puede reutilizarse.
- Abrir y aprobar la cuenta **ONVO** (KYC, cuenta de liquidación, comisiones, SINPE Móvil). Probar en modo test con el checkout de la tienda (pedido → intent → webhook con `order_id` → pago aplicado → comprobante).
- **No cancelar Fygaro el día del corte**: mantener el plan activo al menos 30 días (reversa) y la cuenta accesible el tiempo que el banco permita contracargos/reembolsos sobre ventas hechas en Fygaro (consultar al adquirente; típicamente 120–180 días).

### 8.3 Correo
- Verificar el dominio en Resend: DKIM (`resend._domainkey`), SPF del subdominio de envío y MX de rebote; luego subir DMARC a `pct=100` cuando los envíos pasen. Hasta entonces, no hacer el corte (confirmaciones y facturas no llegarían).
- Plantillas mínimas: confirmación de pedido (con instrucciones de pago manual), aviso al comercio, pago recibido, enviado (con guía), comprobante electrónico (PDF + XML), reembolso.

### 8.4 DNS y dominio
1. T‑2 días: bajar TTL del CNAME `tienda` a 300 s.
2. Agregar `tienda.crimsoncr.com` como dominio propio del servicio `portal` en Railway (certificado TLS emitido antes de mover el CNAME si Railway lo permite con TXT de verificación).
3. En Mi Tienda → Dominio: `tienda.crimsoncr.com`, tienda publicada, tipo "tienda" (carrito y checkout).
4. T0: CNAME `tienda` → destino de Railway. Probar `/`, una ficha vieja `/products/<uuid>/`, `/products/category/12755/`, `/terms`, `/policies`, `/page/solicitud-de-asesoria-personalizada/`, `/sitemap.xml`.
5. Quitar el dominio en el panel de Fygaro (Ajustes → dominio) para que no siga sirviendo una copia.
6. Reversa: volver el CNAME a `shops3.fygaro.com` (por eso Fygaro sigue activo 30 días).

### 8.5 Google y redes
- Search Console: propiedad `tienda.crimsoncr.com`, enviar el sitemap nuevo, vigilar 404/soft‑404 dos semanas.
- GA4: decidir si se reutiliza `G-0PQ0N2M5TF`.
- Revisar enlaces en Facebook, Instagram, WhatsApp Business (catálogo) y Google Business Profile que apunten a la tienda; las URLs viejas quedan cubiertas por la sección 5.

### 8.6 Secuencia sugerida
| Momento | Hito |
|---|---|
| T‑14 | Exportes 8.1 completos; ONVO aprobado en test; DKIM/SPF de Resend verificados |
| T‑7 | Importación final del catálogo (precios, fotos, subcategorías) y conciliación de existencias; textos legales revisados |
| T‑3 | Prueba punta a punta en dominio de prueba: pedido con SINPE, pedido con tarjeta (test), cupón, envío, correo, comprobante |
| T‑2 | TTL bajo |
| T0 (día hábil, mañana) | Cambio de CNAME; pruebas 8.4.4; anunciar en redes |
| T+1…T+14 | Monitoreo de pedidos, correos, 404 y Search Console |
| T+30 | Cancelar plan de Fygaro (si no hubo reversa), conservando acceso de lectura/reembolsos si el plan lo permite |

---

## 9. Brechas críticas por agente

**(b) checkout/pagos/pedidos — bloquean el corte**
1. Cobro con tarjeta en la tienda vía ONVO (intent desde la orden, webhook que acepte `order_id`, estado `pagado`, idempotencia); hoy el webhook solo conoce facturas (`webhooks.py:57-79`).
2. Correos: confirmación al comprador y aviso al comercio en `store_checkout` (`store.py:668-724`), y al cambiar estado.
3. Comprobante electrónico automático al confirmarse el pago (FE con cédula, TE sin ella), en lugar del paso manual `store.py:289-338`.
4. Dirección estructurada CR (provincia/cantón/distrito/señas) y tarifas por zona + envío gratis desde un monto + retiro en tienda.

**(a) tienda↔inventario**
5. Validar existencias y reservar en el checkout (hoy se descuenta solo al facturar, `store.py:331-332`); definir política "bajo pedido".
6. Importar subcategorías (`Category.parent_id`) y asignarlas con el nuevo `products[].subcategories`.
7. Revisar los 23 productos con IVA 0 % y los 20 con tasa rara; los 8 sin categoría; re‑alojar todas las fotos.

**(c) vitrina/UX/SEO/páginas**
8. HTML con `title`/`og:*`/JSON‑LD por producto visible para bots, `sitemap.xml`, `robots.txt`, canonical y 404 reales.
9. Redirecciones 301 del servidor para los patrones de la sección 5, incluidos `/page/<slug>/` y `subcategory=`.
10. Página `solicitud-de-asesoria-personalizada` con el formulario de la sección 7 (bloque `form` con campos configurables) — hoy el enlace de la landing está roto.
11. Moneda de exhibición (USD como hoy vs. CRC) alineada con los términos.

**(A) Andrés**
12. Exportes 8.1, credenciales/consecutivos de Hacienda al proveedor nuevo, alta en ONVO, decisión de moneda, y DNS de Resend.

---

## Anexo: cómo se reproduce este relevamiento

```
cd api
uv run python -m app.tools.fygaro_crawl ./fygaro            # catálogo + subcategorías + datos del sitio
uv run python -m app.tools.fygaro_crawl ./fygaro --images   # además, las fotos
```
Salida: `fygaro_catalog.json` con `site` (páginas, redes, analítica, orden), `categories[].subcategories`, `products[].subcategories`. La carpeta de salida no va a git (`.gitignore`); la copia de referencia vive en `infra/fygaro/`.
