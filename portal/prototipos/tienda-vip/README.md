# Prototipo «tienda VIP» (propuesta, no es código de producción)

Copia versionada de dos prototipos que solo existían en una carpeta temporal (tarea O0-01 del plan de la tienda).
Sirven de referencia para la Ola 2 (sistema visual). **Nada de esta carpeta entra a ningún build**: `vite build`
parte de `index.html` -> `src/main.tsx`, `tsc -b` solo mira `src/` y la demo parte de `src/demo/`.

| Archivo | Qué es |
|---|---|
| `tokens-vip.css` | Propuesta de tokens (color, tipografía, forma, espacio) con el contraste AA calculado por par. |
| `vip.html` | Maqueta estática de cabecera, portada, tarjeta y zona de compra de la ficha con esos tokens. |

## Antes de reutilizar algo de aquí

- **Es una maqueta visual, no un texto aprobado.** Trae rótulos que el plan prohíbe publicar sin respaldo
  («Última unidad», «Recomendados por nuestros técnicos», «queda 1», «Garantía de fábrica» sin meses). Lo que se
  toma de aquí son los tokens y las proporciones, no las frases.
- **Tipografía sin decidir.** La maqueta usa Archivo; la tienda sigue en Arial (decisión del dueño del 19/10) hasta
  que él la reconfirme. `vip.html` baja Archivo desde Google Fonts **solo para verla en el navegador**; la tienda
  no usa servicios de Google: si el tema Archivo se aprueba, la fuente se aloja en `/fonts` como indica
  `tokens-vip.css`.
- **Fotos y precios.** `vip.html` enlaza fotos del distribuidor y muestra precios tomados del catálogo el día que
  se hizo; son de muestra y no se actualizan. Abrir el archivo pide esas fotos al sitio del distribuidor.
- Los números de línea que cita `tokens-vip.css` (`shop.css:5-17`, `vitrina.css:6-8`) son del árbol del 10/11.

## Pendiente

- `scripts/prototipos/proto_name.py` (prototipo de nombre comercial corto, tercera pieza de O0-01) no se copió en
  esta tarea.
