# Pruebas del portal

Tres juegos de pruebas. Los dos primeros no necesitan API, base de datos, Docker ni internet; el tercero levanta
solo el API y el portal en esta máquina (tampoco necesita Docker ni internet):

| Comando | Qué corre | Dónde viven |
|---|---|---|
| `npm test` | Vitest: módulos puros en Node (hoy `catalogFilter.ts`) | `src/**/__tests__/*.test.ts` |
| `npm run e2e` | Playwright: la tienda en un navegador de verdad, a cinco anchos, con el API simulado | `e2e/*.spec.ts` |
| `npm run e2e:compra` | Playwright: una **compra real** contra el API local, a 375 y 768 px y solo con teclado | `e2e/compra.spec.ts` |
| `npm run e2e:tipos` | `tsc` sobre las pruebas de pantalla y sus configuraciones | `e2e/tsconfig.json` |

Primera vez en una máquina: `npm ci` y `npx playwright install --only-shell chromium`.

## Contra qué corren las pruebas de pantalla

Contra la **tienda de pruebas** (`e2e/app/`): el mismo árbol que el dominio de la tienda
(`StoreHost` → `PublicStore` en modo dominio propio) con el API simulado dentro del navegador.

- `app/datos.ts` — catálogo **100 % ficticio** (28 productos, precios de ₡2 000 o menos, fotos dibujadas en el
  propio archivo). Trae a propósito un producto de cada estado: en bodega, última unidad, bajo pedido,
  «por confirmar» (consultar), agotado, sin precio y sin foto.
- `app/api.ts` — el mock de la demo (`src/demo/mockApi.ts`) más dos cosas que la tienda real sí tiene: el
  snapshot del catálogo (`/snapshot`) y la disponibilidad con la forma completa del API.
- `vite.e2e.config.ts` la compila a `e2e/.dist` y la sirve en `127.0.0.1:5301` (`E2E_PUERTO` para cambiarlo).
  Playwright la levanta y la apaga solo; si el puerto está ocupado falla en vez de usar lo que haya ahí.
  Para verla a mano: `npm run e2e:servir`.

Cada prueba corre con el reloj fijo (10/11/2026 15:00, hora de Costa Rica) y **corta cualquier pedido a otro
sitio**: si la tienda intenta bajar algo de afuera (fotos de un proveedor, fuentes, medición), la prueba falla.

No es la compra real: aquí no hay inventario, pago ni correo. La compra de punta a punta contra el API local es
`npm run e2e:compra` (más abajo, «Compra real en local»).

## Qué se prueba

| Archivo | Qué revisa | Anchos |
|---|---|---|
| `scroll.spec.ts` | Sin scroll horizontal y nada cortado por el borde, en las 12 pantallas de `soporte/pantallas.ts` | 320, 360, 375, 768, 1280 |
| `capturas.spec.ts` | Capturas base de página completa (8 pantallas) | 375, 768, 1280 |
| `axe.spec.ts` | Accesibilidad WCAG 2.1 A/AA en **modo informe**: no bloquea | 375, 1280 |
| `compra-mock.spec.ts` | Recorrido ficha → carrito → 4 pasos del checkout → «Mi pedido», sobre el mock | 375, 1280 |
| `compra.spec.ts` | La compra real contra el API local: **no** corre con `npm run e2e`, sino con `npm run e2e:compra` | 375, 768 |
| `mi-tienda.spec.ts` | El panel (Mi Tienda) contra el API local, con un administrador de prueba de contraseña al azar: despublicar pide confirmación y escribir un titular y luego guardar un banner no pierde nada (O1-11b). Va con `npm run e2e:compra` | 768 |
| `portada.spec.ts` | Portada honesta (O1-04): sin textos de relleno ni nada que parpadee fuera del hero, y 10 recargas con `/showcase` entre 0 y 60 ms después del snapshot siempre con kits | todos (la carrera, 1280) |
| `pendientes.spec.ts` | Fallos conocidos, como `test.fixme` con su tarea | todos |
| `venta-asistida.spec.ts` | Ficha «por confirmar» (O1-02) y desglose de IVA con `/item` lento (O1-03) | todos |
| `tienda-carga.spec.ts` | La tienda reintenta si falla `/store-domain` y ofrece «Reintentar» (O1-18) | todos |
| `humo.spec.ts` | La portada pinta sin errores; también en **WebKit** (proyecto `webkit-375`: siempre en CI, local con `E2E_WEBKIT=1` tras `npx playwright install webkit`) | todos + WebKit |

Agregar una pantalla: una entrada en `soporte/pantallas.ts` (con `base: true` entra también a capturas y axe).

Al terminar, `e2e/informes/` (fuera de git) trae `axe.md` y `pendientes.md`; el resumen sale en la consola.

## Compra real en local (`npm run e2e:compra`)

Es el lugar para probar compras: **nunca con pedidos reales en producción** y no con la «Tienda de pruebas» del
panel (esa no usa el checkout, el carrito ni el inventario; queda solo para ONVO en modo Test). Todo criterio del
plan de la tienda que diga «en local» se verifica aquí.

Qué corre (`compra.spec.ts`, en dos anchos: 375 y 768 px, táctiles):

| Prueba | Qué hace |
|---|---|
| La tienda local está sembrada | Los seis productos en su estado, SINPE activo, tarjeta solo como pago simulado, sin medición |
| Compra SINPE completa | Catálogo → ficha → agregar → aviso «Ir al carrito» → carrito → los cuatro pasos del checkout → pedido creado → «Mi pedido» con las instrucciones de SINPE; después lo vuelve a buscar por número y correo |
| La misma compra solo con teclado | Tab, Enter, espacio y flechas; falla si la página recibe un solo evento de ratón, dedo o lápiz |

Al final de cada compra se lee el pedido **en el servidor** (estado, método, tarifa, total de ₡7 006) y se revisa
que apartó existencia (queda una unidad libre menos). La pantalla final de cada compra queda adjunta en
`playwright-report/compra`.

### Sin Docker (lo normal, y lo que corre el CI)

```
npm ci && npx playwright install --only-shell chromium      # una vez
(cd ../api && uv sync --locked)                              # una vez
npm run e2e:compra
```

Playwright levanta y apaga dos servidores, los dos solo en `127.0.0.1`:

- **API** en el puerto 8101: `api/scripts/tienda_e2e_local.py` crea una SQLite **nueva en cada corrida** en la
  carpeta temporal del sistema (fuera del repo), corre `alembic upgrade head`, siembra la tienda y arranca uvicorn.
  Antes de cargar la aplicación limpia el entorno (fuera llaves de correo, ONVO, Eurocomp, Hacienda, BCCR, IMAP,
  Railway) y no lee ningún `.env`: el correo queda «simulado», la tarjeta en modo de prueba y nada sale a internet.
- **Portal** en el puerto 5301: el build de verdad (`vite.compra.config.ts`: el mismo `index.html` y `src/main.tsx`
  de producción) con `/api` apuntando al API local. Se abre como `http://tienda.localhost:5301`: un nombre que
  empieza con «tienda.» monta la tienda en la raíz, igual que el dominio propio.

El navegador de la prueba no resuelve ningún nombre que no sea de esta máquina y cualquier pedido a otro sitio hace
fallar la prueba. Si el puerto 8101 o el 5301 están ocupados, falla en vez de usar lo que haya ahí.

Variables (todas opcionales):

| Variable | Para qué |
|---|---|
| `E2E_COMPRA_PORTAL=dev` | Servidor de desarrollo de Vite en vez del build (depurar con el código sin empaquetar) |
| `E2E_DATABASE_URL` | Otra base **local** para el API (el CI pasa una Postgres de servicio); remota se rechaza |
| `E2E_PYTHON` | Intérprete con las dependencias del API si no se usa `uv` (por defecto `uv run --locked python`) |
| `E2E_PUERTO`, `E2E_API_PUERTO` | Cambiar los puertos 5301 y 8101 |

El proxy `/api` del portal de la compra (`vite.compra.config.ts`) solo acepta un API de esta máquina
(`http://127.0.0.1|localhost|[::1]:<puerto>`): otro `E2E_API_URL` corta el arranque. Cuando Playwright levanta el
portal, además, fija `E2E_API_URL` al API que acaba de levantar, así una variable heredada no lo desvía.
`E2E_DATABASE_URL` con una Postgres que ya tiene tablas solo se acepta si el nombre de la base dice `e2e`, `test`
o `prueba`, y eso se revisa **antes** de migrar; las variables `RAILWAY_*` se buscan en el entorno original (dentro
de `railway run` no arranca). `--forzar` de la semilla tiene el mismo candado de nombre.

Para ver la tienda sembrada a mano: en `api/`, `uv run python -m scripts.tienda_e2e_local`; en `portal/`,
`npx vite --config vite.compra.config.ts`; abrir `http://tienda.localhost:5301`.

### La semilla (`api/scripts/seed_tienda_e2e.py`)

Una empresa con la tienda publicada en `tienda.localhost`, en colones, con datos 100 % ficticios:

| Código | Producto | Precio con IVA | Estado |
|---|---|---|---|
| `E2E-BOD-1` | Cable de red de prueba 1 m | ₡1 695 | En bodega (40) — el que se compra |
| `E2E-BOD-2` | Conector RJ45 de prueba | ₡565 | En bodega (3: últimas unidades) |
| `E2E-PED-1` | Soporte de pared de prueba | ₡1 356 | Bajo pedido, dato del proveedor vigente |
| `E2E-PED-2` | Caja de paso de prueba | ₡904 | Bajo pedido, dato del proveedor vigente |
| `E2E-CON-1` | Fuente 12 V de prueba | ₡1 921 | Por confirmar («Consultar»: dato de hace 30 días) |
| `E2E-AGO-1` | Sirena interior de prueba | ₡1 808 | Agotado |

- Envío: «Correos de Costa Rica» ₡3 955 a todo el país y «Fast Crimson» ₡5 311 en la GAM (con IVA), gratis desde
  ₡50 000. Son las tarifas que documenta el plan (O1-09); **no se leyeron de producción**: si las reales son otras,
  se cambian en `TIENDA["shipping_rates"]` y en `soporte/compra.ts`.
- Pago: SINPE Móvil (número y titular inventados) y tarjeta **sin llave de ONVO**, que fuera de producción el API
  ofrece solo como «Modo de prueba · pago simulado». Sin transferencia ni efectivo.
- Sin comprobante electrónico automático, sin revisión del dominio del correo por DNS y sin medición.
- No crea usuarios ni contraseñas. Es idempotente: repone la existencia y refresca la fecha del dato del proveedor.
- Candados: solo con `ENV=local`, sin variables de Railway, y contra SQLite o una Postgres de esta máquina o del
  docker-compose; si la empresa ya tiene productos propios en la web se niega (salvo `--forzar`, que no los toca).
  El guion no entra a las imágenes Docker (`.dockerignore`).
- `api/tests/test_seed_tienda_e2e.py` la prueba en el trabajo de la API (estados, idempotencia, compra SINPE y pago
  simulado por la API pública, candados).

Los montos de `soporte/compra.ts` están escritos a mano a propósito: si cambia un precio o una tarifa en la semilla,
la prueba se pone en rojo hasta que alguien confirme el número nuevo.

### Con Docker (api, worker, postgres, redis y portal con nginx)

Lo mismo contra los contenedores, con nginx sirviendo la tienda como en producción (SSI, 404 reales, cabeceras).
`infra/docker-compose.e2e.yml` lo vuelve un entorno aparte y desechable: proyecto `crimson-e2e` con su propia base,
**sin leer el `.env`**, y solo publica el portal (5301) y el API (8101) en `127.0.0.1`. Desde la raíz del repo:

```
docker compose -f infra/docker-compose.yml -f infra/docker-compose.e2e.yml up --build -d --wait
docker compose -f infra/docker-compose.yml -f infra/docker-compose.e2e.yml run --rm semilla
cd portal
E2E_COMPRA_URL=http://tienda.localhost:5301 npm run e2e:compra
cd ..
docker compose -f infra/docker-compose.yml -f infra/docker-compose.e2e.yml down -v
```

(En PowerShell la variable se pone antes: `$env:E2E_COMPRA_URL = "http://tienda.localhost:5301"`.)

Con `E2E_COMPRA_URL` Playwright no levanta nada: usa lo que ya está arriba (solo acepta direcciones de esta máquina).
Pendiente de verificar: el 10/11 el servicio de Docker de esta máquina estaba apagado; los dos archivos se validaron
con `docker compose config`, pero **la compra contra los contenedores todavía no se ha corrido**. Requiere Docker
Compose 2.24 o más nuevo.

### En el CI

Trabajo «Tienda · compra real en local (API + portal + Postgres)» de `.github/workflows/ci.yml`: el modo sin Docker
con una Postgres de servicio (`E2E_DATABASE_URL`). Tarda pocos minutos; si un día pasa de 10, se mueve a un workflow
nocturno y queda obligatoria antes de cada despliegue de dinero (`infra/DESPLIEGUE.md`).

Si algo falla, los traces, las capturas de la falla y el informe HTML quedan 7 días en los artefactos del run:
`e2e-compra` (este trabajo) y `e2e-portal` (vitest + playwright, que también guarda `e2e/informes/axe.md` y los
diffs de las capturas).

## Capturas base

Viven en `e2e/capturas-base/<sistema>/` y **sí se versionan**. Son por sistema porque las letras no se dibujan
igual en Windows que en Linux. Hoy solo están las de **`win32`** (donde se trabaja la tienda). En una máquina
local sin carpeta para su sistema, las pruebas de captura **se saltan con aviso**. En el CI la falta de la carpeta
es un **error**; mientras no existan las de Linux, el trabajo `portal-pruebas` declara `E2E_CAPTURAS_PENDIENTES=1`
y el salto queda explícito en el workflow. **Pendiente** (antes de la Ola 2, que compara contra estas capturas):
generar `e2e/capturas-base/linux/` con la receta de abajo, confirmarla y quitar esa variable de `ci.yml`. Hasta
entonces, la comparación de capturas de la Ola 2 se hace a mano en Windows (`npm run e2e`).

- Cambio de estilos a propósito: `npm run e2e:base`, revisar las imágenes que cambiaron y confirmarlas en el
  mismo commit. Un cambio que no debía mover nada y mueve una captura es justo lo que esto detecta.
- Capturas de Linux (para que el CI también compare), con Docker encendido y **la misma imagen que usa el CI**
  (`container.image` del trabajo `portal-pruebas` en `ci.yml`, que debe ser la versión de `@playwright/test`
  de `package-lock.json`; el CI lo exige). Generarlas en otro Linux daría otras fuentes de respaldo y otro
  antialias, y con `maxDiffPixelRatio 0.001` el CI quedaría en rojo:

  ```
  docker run --rm --ipc=host -v "<ruta del repo>/portal:/work" -v /work/node_modules -w /work mcr.microsoft.com/playwright:v1.64.1-noble sh -c "npm ci --no-audit --no-fund && npx playwright test capturas.spec.ts --update-snapshots"
  ```

  El `-v /work/node_modules` (un volumen anónimo encima) hace que el `npm ci` del contenedor **no** toque el
  `node_modules` de Windows. Se confirma solo la carpeta `e2e/capturas-base/linux/` y se quita
  `E2E_CAPTURAS_PENDIENTES` de `ci.yml` en el mismo commit. Sin Docker no hay forma local de generarlas (el 10/11
  el servicio de Docker de esta máquina estaba apagado y por eso no se hicieron); nada más de estas pruebas
  necesita Docker.
- Lo que está fijo a la ventana (barra de compra del celular, botones del checkout) sale donde cae en la
  primera pantalla, encima de lo que haya debajo.
- Los precios del catálogo ficticio son de 4 cifras. Un desborde que solo aparezca con precios de 6 cifras en
  una tarjeta o en la ficha no lo ven estas capturas (el carrito y el checkout sí llegan a 6 cifras con cantidad).

## Fallos conocidos (`test.fixme`) y quién los resuelve

Verificados en rojo el 10/11. Quien haga la tarea quita la marca (en `pendientes.spec.ts` cambia `test.fixme`
por `test`; en `scroll.spec.ts` borra la línea de `CONOCIDOS`).

| Tarea | Qué falla hoy | Dónde |
|---|---|---|
| O1-02 | Ficha «por confirmar» a 320, 360 y 375 px: el botón «Consultar disponibilidad» ensancha la columna y todo queda cortado a la derecha | `scroll.spec.ts` |
| O1-02 | Ficha «por confirmar»: botón apagado en la zona de compra y barra fija con «Sin existencias» | `pendientes.spec.ts` |
| O1-03 | Ficha comprable a 320 px: cantidad + «Agregar al carrito» no caben; la columna queda cortada | `scroll.spec.ts` |
| O1-03 | Ficha en celular: precio de 16,5 px e «IVA incluido» de 11 px (se piden 30 y 14) | `pendientes.spec.ts` |
| O1-08 | La tarjeta de la barra de sonido dice «Cámara WiFi» | `pendientes.spec.ts` |
| O1-10a | Checkout, paso de pago a 320 px: «Aplicar» del cupón cortado | `scroll.spec.ts` |
| O1-10a | Checkout, confirmar a 320, 360 y 375 px: «Atrás» + «Confirmar pedido · total» no caben | `scroll.spec.ts` |
| O1-14 | Buscar «camaras» no encuentra las cámaras | `pendientes.spec.ts` |
| O1-14 | `/c/no-existe` a veces lista todo el catálogo (depende de qué respuesta llega primero) | `pendientes.spec.ts` |

El criterio literal del plan (ancho del documento ≤ ancho de la ventana) hoy pasa en todas las pantallas y
anchos: los cuatro de `scroll.spec.ts` son contenido **cortado** por un contenedor con overflow oculto, que el
cliente ve igual de roto.

## Accesibilidad

`axe.spec.ts` no falla por hallazgos (modo informe hasta O2-02). La foto del 10/11 está en
`linea-base/axe-2026-11-10.md`: dos reglas, las dos «serious» — contraste insuficiente (`color-contrast`, en
casi todas las pantallas) y nombre accesible que no contiene el texto visible (`label-content-name-mismatch`,
en el botón del carrito y los pasos del checkout).

## Tipos de Node

No se instala `@types/node` a propósito: entraría también al programa de `src/` (por los tipos de vitest) y
puede cambiar los globales del navegador por los de Node, con `tsc -b` en rojo en archivos que nadie tocó. Lo poco de Node que usan las pruebas está declarado en `soporte/node-minimo.d.ts`.
