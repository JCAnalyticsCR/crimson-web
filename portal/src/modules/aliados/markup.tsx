/* Punto 12 (parte A): el recargo del aliado es un multiplicador sobre el COSTO, no el margen de la tienda.
   "12 %" significa "Precio para el aliado = costo base × 1,12". Solo explica: el valor configurado no cambia. */
export const factorOf = (pct: number) => (1 + pct / 100).toLocaleString("es-CR", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
export const pctText = (pct: number) => `${pct.toLocaleString("es-CR", { maximumFractionDigits: 2 })} %`;

export function MarkupFormula({ pct, own }: { pct: number | null; own?: boolean }) {
  if (pct == null || Number.isNaN(pct)) return null;
  return (
    <div className="markup-box">
      <b>Precio para el aliado = costo base × {factorOf(pct)}</b>
      <span className="meta">
        Recargo {own ? "propio" : "por defecto"} de {pctText(pct)} sobre el costo. Ejemplo: costo ₡100 000 → aliado ₡{(100000 * (1 + pct / 100)).toLocaleString("es-CR", { maximumFractionDigits: 0 })}.
        Nunca pasa del precio público. No es el margen de la tienda: la tienda calcula su precio con su propio margen sobre la venta.
      </span>
    </div>
  );
}
