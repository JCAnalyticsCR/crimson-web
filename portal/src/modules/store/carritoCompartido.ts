/* Enlace de carrito para ventas (O2-09): /?carrito=116x1,241x1. Modulo puro: lo prueba vitest. */

/** O2-09: «116x1,241x2» -> [{id: 116, qty: 1}, {id: 241, qty: 2}]. Ids y cantidades enteras (1 a 99); a lo sumo 12
    lineas; lo que no calza se ignora. */
export function parseCarrito(raw: string | null): { id: number; qty: number }[] {
  const out: { id: number; qty: number }[] = [];
  for (const parte of (raw || "").split(",").slice(0, 24)) {
    const m = /^\s*(\d{1,9})(?:x(\d{1,2}))?\s*$/i.exec(parte);
    if (!m) continue;
    const id = Number(m[1]), qty = Math.max(1, Math.min(99, Number(m[2] || 1)));
    const ya = out.find((x) => x.id === id);
    if (ya) ya.qty = Math.min(99, ya.qty + qty);
    else if (out.length < 12) out.push({ id, qty });
  }
  return out;
}
