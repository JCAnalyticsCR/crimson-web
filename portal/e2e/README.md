# Pruebas del portal

Dos juegos de pruebas, ninguno necesita API, base de datos, Docker ni internet:

| Comando | Qué corre | Dónde viven |
|---|---|---|
| `npm test` | Vitest: módulos puros en Node (hoy `catalogFilter.ts`) | `src/**/__tests__/*.test.ts` |
| `npm run e2e` | Playwright: la tienda en un navegador de verdad, a cinco anchos | `e2e/*.spec.ts` |
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

No es la compra real: aquí no hay inventario, pago ni correo. La compra de punta a punta contra el API local
es la tarea O0-03.

## Qué se prueba

| Archivo | Qué revisa | Anchos |
|---|---|---|
| `scroll.spec.ts` | Sin scroll horizontal y nada cortado por el borde, en las 12 pantallas de `soporte/pantallas.ts` | 320, 360, 375, 768, 1280 |
| `capturas.spec.ts` | Capturas base de página completa (8 pantallas) | 375, 768, 1280 |
| `axe.spec.ts` | Accesibilidad WCAG 2.1 A/AA en **modo informe**: no bloquea | 375, 1280 |
| `compra-mock.spec.ts` | Recorrido ficha → carrito → 4 pasos del checkout → «Mi pedido», sobre el mock | 375, 1280 |
| `pendientes.spec.ts` | Fallos conocidos, como `test.fixme` con su tarea | todos |

Agregar una pantalla: una entrada en `soporte/pantallas.ts` (con `base: true` entra también a capturas y axe).

Al terminar, `e2e/informes/` (fuera de git) trae `axe.md` y `pendientes.md`; el resumen sale en la consola.

## Capturas base

Viven en `e2e/capturas-base/<sistema>/` y **sí se versionan**. Son por sistema porque las letras no se dibujan
igual en Windows que en Linux. Hoy solo están las de **`win32`** (donde se trabaja la tienda). Donde no hay
carpeta para el sistema, las pruebas de captura **se saltan con aviso** en vez de fallar: en el CI (Linux) todo
lo demás se exige y las capturas quedan pendientes hasta generar las de Linux.

- Cambio de estilos a propósito: `npm run e2e:base`, revisar las imágenes que cambiaron y confirmarlas en el
  mismo commit. Un cambio que no debía mover nada y mueve una captura es justo lo que esto detecta.
- Capturas de Linux (para que el CI también compare), con Docker encendido y la imagen de la misma versión
  que `@playwright/test` en `package.json`:

  ```
  docker run --rm --ipc=host -v "<ruta del repo>/portal:/work" -w /work mcr.microsoft.com/playwright:v1.64.1-noble sh -c "npm ci --no-audit --no-fund && npx playwright test capturas.spec.ts --update-snapshots"
  ```

  Deja `node_modules` con binarios de Linux: después, `npm ci` otra vez en Windows. Se confirma la carpeta
  `e2e/capturas-base/linux/`. Sin Docker no hay forma local de generarlas (el 10/11 el servicio de Docker de
  esta máquina estaba apagado y por eso no se hicieron); nada más de estas pruebas necesita Docker.
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
