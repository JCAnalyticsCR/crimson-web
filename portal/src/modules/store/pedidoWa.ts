/* Venta asistida (O1-02): el mensaje de WhatsApp de un producto «por confirmar». Modulo puro (sin React) para poder
   probarlo en Vitest. Lleva todo lo que Andres necesita para cotizar sin preguntar de vuelta: nombre, codigo, cantidad,
   el precio que el cliente vio (de referencia), la direccion de la ficha y una referencia corta (P + id del producto). */

export type PedidoWa = {
  id: number;
  nombre: string;
  codigo?: string | null;
  cantidad: number;
  /** precio con IVA que se mostro en la ficha; 0 o vacio = sin precio publicado (no se menciona) */
  precio?: number | string | null;
  moneda?: string;
  url: string;
};

export const precioTexto = (v: number | string, cur = "CRC") =>
  new Intl.NumberFormat("es-CR", { style: "currency", currency: cur, currencyDisplay: "narrowSymbol", minimumFractionDigits: cur === "CRC" ? 0 : 2, maximumFractionDigits: cur === "CRC" ? 0 : 2 }).format(Number(v) || 0);

/** «Hola, quiero pedir {nombre} (código {código}), cantidad {n}. Precio que vi en la web: ₡{precio} con IVA.
    ¿Me confirman precio, existencia y entrega? {url} · ref. web P{id}» */
export function mensajePedido(p: PedidoWa): string {
  const n = Math.max(1, Math.floor(Number(p.cantidad) || 1));
  const cod = p.codigo ? ` (código ${p.codigo})` : "";
  const precio = Number(p.precio) > 0 ? ` Precio que vi en la web: ${precioTexto(p.precio as number, p.moneda || "CRC")} con IVA.` : "";
  return `Hola, quiero pedir ${p.nombre}${cod}, cantidad ${n}.${precio} ¿Me confirman precio, existencia y entrega? ${p.url} · ref. web P${p.id}`;
}
