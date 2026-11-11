/* Lo que la tienda MUESTRA de un producto (Ola 3, O3-01 y O3-02). El API manda `name` y `code` fiscales (los de la
   factura) y, aparte, `display_name` (nombre comercial aprobado) y `public_code` (codigo limpio, sin prefijos ni
   sufijos internos). La vitrina pinta los visibles con respaldo a los de siempre: tarjeta, ficha, migas, carrito y
   mensaje de WhatsApp dicen lo mismo. La direccion de la ficha (path) no cambia. */

type Named = { name: string; code: string; display_name?: string | null; public_code?: string | null };

/** el nombre que ve el cliente: el comercial aprobado o, sin el, el de siempre */
export const nombreVisible = (p: Named): string => (p.display_name || "").trim() || p.name;

/** el mismo producto con el nombre y el codigo visibles en `name` y `code` (para pintar y para el carrito) */
export function conNombreVisible<T extends Named>(p: T): T {
  const name = nombreVisible(p);
  const code = (p.public_code || "").trim() || p.code;
  return name === p.name && code === p.code ? p : { ...p, name, code };
}
