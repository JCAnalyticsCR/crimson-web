/* Cajon del producto -> «Precio» (O4-04 y O4-05): lo que paga el cliente (con IVA), el precio de siempre, el precio
   web, el historial (O0-07) y si hay oferta.
   - O4-05: el precio web se escribe FINAL con IVA («12.500», parseCRC) y debajo se ve el neto que se guarda (5
     decimales, la misma cuenta del API). Vacio = el precio de siempre. Se guarda con la barra, con «Deshacer».
   - Una oferta (precio tachado) solo se muestra con historial propio: el precio anterior tuvo que estar vigente al
     menos 30 dias. No existe el precio de comparacion escrito a mano ni ofertas automaticas. */
import { useEffect, useState } from "react";
import { useSession } from "../../../app/session";
import { formatCRCInput, parseCRC } from "../../../lib/parseCRC";
import { Field } from "../../../ui/components";
import { money } from "../storeKit";
import { finalDesdeNeto, netoDesdeFinal } from "./precioIva";
import type { Detail, Edit } from "./ProductoWebDrawer";

const fecha = (s: string) => new Date(s).toLocaleDateString("es-CR", { day: "numeric", month: "numeric", year: "numeric" });

export function PrecioTab({ full, draft, set }: { full: Detail; draft: Edit; set: (p: Partial<Edit>) => void }) {
  const { allows } = useSession();
  const rate = Number(full.tax_rate);
  const aTexto = (net: Edit["web_price"]) => (net == null || net === "" ? "" : formatCRCInput(finalDesdeNeto(Number(net), rate)));
  const [txt, setTxt] = useState(() => aTexto(draft.web_price));
  const [err, setErr] = useState<string | null>(null);
  // si el borrador cambia por fuera (descartar, deshacer), el campo lo sigue
  useEffect(() => {
    const r = parseCRC(txt);
    const mine = !txt.trim() ? null : r.ok ? netoDesdeFinal(r.value, rate) : undefined;
    const cur = draft.web_price == null || draft.web_price === "" ? null : Number(draft.web_price);
    if (mine !== undefined && mine !== cur && !(mine != null && cur != null && Math.abs(mine - cur) < 0.00001)) setTxt(aTexto(draft.web_price));
  }, [draft.web_price]); // eslint-disable-line react-hooks/exhaustive-deps
  if (full.price == null) return <p className="mt-lead">Su rol no ve precios de venta.</p>;
  const conIva = (net: number | string | null) => (net == null ? null : finalDesdeNeto(Number(net), rate));
  const enColones = full.currency === "CRC";
  const escribir = (v: string) => {
    setTxt(v);
    if (!v.trim()) { setErr(null); set({ web_price: null }); return; }
    const r = parseCRC(v);
    if (!r.ok || r.value <= 0) { setErr(r.ok ? "Escriba un precio mayor que cero." : r.error); return; }
    setErr(null);
    set({ web_price: netoDesdeFinal(r.value, rate) });
  };
  const neto = draft.web_price == null || draft.web_price === "" ? null : Number(draft.web_price);
  return (
    <div className="mt-stack">
      <div className="mt-box">
        <p className="mt-lead"><b>Lo que paga el cliente hoy:</b> {full.display_price != null && Number(full.display_price) > 0 ? `${money(full.display_price, full.store_currency)} (IVA incluido)` : "Precio a consultar"}</p>
        <p className="mt-lead">Precio de siempre: {money(conIva(full.price) ?? 0, full.currency)} con IVA ({money(full.price, full.currency)} sin IVA, IVA {rate} %).</p>
        {full.margin_hold && <p className="mt-warn">El guardián de margen lo tiene fuera de venta (precio a consultar) hasta que se revise en Aprobaciones.</p>}
      </div>
      {enColones && allows("catalog.precios") ? (
        <div className="mt-box">
          <Field label="Precio web final con IVA" hint="Lo que paga el cliente en la tienda, por ejemplo 12.500. Vacío = el precio de siempre.">
            <input className="input input--mono" inputMode="decimal" value={txt} aria-invalid={err ? true : undefined} onChange={(e) => escribir(e.target.value)} />
          </Field>
          {err ? <p role="alert" className="mt-warn">{err}</p> : <p className="meta" style={{ textTransform: "none", margin: 0 }}>{neto != null ? `Se guarda sin IVA: ${money(neto, full.currency)} (${neto.toFixed(5)}). El cliente paga ${money(finalDesdeNeto(neto, rate), full.currency)}.` : "Sin precio web: la tienda usa el precio de siempre."}</p>}
        </div>
      ) : (
        <p className="mt-lead">Precio web: {full.web_price != null ? `${money(conIva(full.web_price) ?? 0, full.currency)} con IVA` : "el de siempre"}.{!enColones && " Este producto tiene el precio en otra moneda: se cambia en su ficha."}</p>
      )}
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
