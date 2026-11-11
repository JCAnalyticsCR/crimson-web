/* Cajon del producto -> «Precio» (O4-04): lo que paga el cliente (con IVA), el precio de siempre, el precio web, el
   historial (O0-07) y si hay oferta. Una oferta (precio tachado) solo se muestra con historial propio: el precio
   anterior tuvo que estar vigente al menos 30 dias. No existe el precio de comparacion escrito a mano. */
import { money } from "../storeKit";
import type { Detail, Edit } from "./ProductoWebDrawer";

const fecha = (s: string) => new Date(s).toLocaleDateString("es-CR", { day: "numeric", month: "numeric", year: "numeric" });

export function PrecioTab({ full }: { full: Detail; draft: Edit; set: (p: Partial<Edit>) => void }) {
  if (full.price == null) return <p className="mt-lead">Su rol no ve precios de venta.</p>;
  const rate = Number(full.tax_rate);
  const conIva = (net: number | string | null) => (net == null ? null : Math.round(Number(net) * (1 + rate / 100) * 100) / 100);
  return (
    <div className="mt-stack">
      <div className="mt-box">
        <p className="mt-lead"><b>Lo que paga el cliente hoy:</b> {full.display_price != null && Number(full.display_price) > 0 ? `${money(full.display_price, full.store_currency)} (IVA incluido)` : "Precio a consultar"}</p>
        <p className="mt-lead">Precio de siempre: {money(conIva(full.price) ?? 0, full.currency)} con IVA ({money(full.price, full.currency)} sin IVA, IVA {rate} %).</p>
        <p className="mt-lead">Precio web: {full.web_price != null ? `${money(conIva(full.web_price) ?? 0, full.currency)} con IVA` : "el de siempre"}.</p>
        {full.margin_hold && <p className="mt-warn">El guardián de margen lo tiene fuera de venta (precio a consultar) hasta que se revise en Aprobaciones.</p>}
      </div>
      <div className="mt-box">
        <b style={{ fontSize: 13 }}>Oferta</b>
        <p className="mt-lead">{full.compare_backed
          ? "Tiene oferta con respaldo: el precio anterior estuvo vigente al menos 30 días y la tienda lo muestra tachado."
          : `Sin oferta. La tienda solo tacha un precio que estuvo vigente al menos ${full.compare_min_days} días según el historial.`}</p>
      </div>
      {full.price_history.length > 0 && (
        <table className="table table--stack" aria-label="Historial de precios">
          <thead><tr><th>Desde</th><th className="num">Precio final con IVA</th><th>Origen</th></tr></thead>
          <tbody>
            {full.price_history.map((h, i) => (
              <tr key={i}><td data-label="Desde">{fecha(h.valid_from)}</td><td className="num" data-label="Precio final">{money(h.price_final, h.currency)}</td><td data-label="Origen">{h.source}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
