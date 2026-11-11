# Pasada de accesibilidad y de celular lento (O2-14)

Lo que la automatización no cubre. Se corre antes de cerrar cada ola visual; cada hallazgo se anota como tarea y la ola
no se cierra con hallazgos bloqueantes abiertos.

## Lo automático (ya corre en `npm run e2e`)

| Qué | Dónde |
|---|---|
| Contraste AA, enlaces y botones sin nombre (bloqueantes); el resto de WCAG 2.1 A/AA en modo informe | `axe.spec.ts` |
| Sin scroll de lado ni contenido cortado a 320, 360, 375, 768 y 1280 px | `scroll.spec.ts` |
| Texto al 200 % en portada, categoría, ficha y carrito (320 y 375 px) | `lento.spec.ts` |
| Objetivos de 44 px, foco atrapado en el cajón y en la hoja de filtros | `sistema-visual.spec.ts` |
| Compra completa solo con teclado (API local) | `compra.spec.ts` (`npm run e2e:compra`) |
| Celular lento simulado (3G lenta, CPU x4): LCP, CLS y tareas largas, como informe | `lento.spec.ts` con `E2E_LENTO=1` |

El perfil lento de `lento.spec.ts` corre contra la tienda de pruebas, que responde desde el propio navegador: sirve para
comparar una versión con otra, no como número de producción. El de producción sale de Lighthouse (abajo).

## Lectores de pantalla: guion de 12 pasos

Con **TalkBack** (Android, Chrome) y con **VoiceOver** (iOS, Safari), en un celular de verdad y contra la tienda local
de `npm run e2e:compra` (nunca con pedidos reales en producción). Anotar por paso: «bien», o qué se oyó y qué faltó.

| # | Paso | Qué tiene que pasar |
|---|---|---|
| 1 | Portada | Se oye el titular (encabezado 1), «Armá tu sistema» como botón-enlace y el producto de portada con marca, nombre y precio en una sola parada |
| 2 | Buscar | La lupa dice «Buscar productos»; al abrirla el foco cae en el campo; al escribir y confirmar se oyen los resultados |
| 3 | Filtrar | «Filtros» abre la hoja, el foco entra y no se escapa; al aplicar se oye la cantidad de resultados |
| 4 | Ficha | Encabezado 1 con el nombre; precio con «IVA incluido»; disponibilidad en una frase; «Foto siguiente» y miniaturas funcionan |
| 5 | Agregar | Al tocar «Agregar al carrito» se oye «Agregado. En tu carrito: 1 · Ver carrito» y el foco se queda en el botón |
| 6 | Carrito | «Carrito: N artículo(s)»; el cajón se anuncia como diálogo «Carrito»; cada línea se oye con su nombre y «Quitar {nombre}» |
| 7 | Checkout · tus datos | Cada campo se oye con su etiqueta; los pasos se oyen «Paso 1: Tus datos» |
| 8 | Checkout · entrega | Provincia y cantón como listas; la tarifa como grupo de opciones |
| 9 | Checkout · pago y confirmar | El método como grupo de opciones; «Confirmar pedido · total» con el monto |
| 10 | Error de formulario | Enviar con un campo vacío: se oye el error ligado a ese campo (no solo un color) |
| 11 | Mi pedido | Encabezado 1 con el número de pedido; el estado se dice con texto |
| 12 | Pedir por WhatsApp | En una ficha «por confirmar», el enlace dice que se abre WhatsApp |

Verificar además: anuncios en vivo al agregar al carrito y al filtrar, errores ligados a cada campo y ningún estado que
dependa solo del color.

## Celular lento de verdad (Lighthouse)

Chrome DevTools → Lighthouse → «Mobile», con 3G lenta y CPU x4, en portada, una categoría y una ficha. Objetivo: LCP
menor a 2,5 s, CLS menor a 0,1 y sin tareas largas que bloqueen. Si no se cumple, anotar la diferencia con su tarea.

## Primera pasada (11/11/2026, Ola 2, solo lo automatico)

- Texto al 200 %: portada, categoría, ficha y carrito sin scroll de lado ni cortes a 320 y 375 px (se arreglaron el
  cajón, el orden de la categoría, la zona de compra, el encabezado de sección y la foto del producto de portada).
- axe: sin hallazgos de contraste ni de nombres en las 8 pantallas base; el botón del carrito dejó de tener un
  aria-label distinto a su texto.
- Perfil lento simulado (tienda de pruebas, 3G lenta y CPU x4, 375 px): portada LCP ≈ 7,0 s · CLS 0,013; categoría
  LCP ≈ 8,3 s · CLS 0,117; ficha LCP ≈ 7,2 s · CLS 0. El LCP de la tienda de pruebas lo domina la descarga del
  JavaScript a 400 kbit/s, no sirve como número de producción.
- **Hallazgos abiertos (tareas):** (1) categoría con CLS 0,117 en el perfil lento: una tarjeta cambia de lugar al
  pasar del esqueleto a los productos; (2) LCP de producción sin medir con Lighthouse; (3) guion de TalkBack y
  VoiceOver sin ejecutar (requiere celulares de verdad).

## Registro

| Fecha | Quién | Lector / herramienta | Resultado por paso | Hallazgos (tarea) |
|---|---|---|---|---|
| | | TalkBack | | |
| | | VoiceOver | | |
| | | Lighthouse (lento) | | |
