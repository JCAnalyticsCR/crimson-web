/* "Dar entrada al inventario" desde una factura de proveedor aceptada: cada línea del XML se liga a un producto propio
   (sugerido por el código del proveedor) y entra a una bodega de venta. Una sola vez por comprobante; lo que entra
   aparece como existencia propia en la tienda, el panel, el POS y el portal del aliado. */
import { useEffect, useState } from "react";
import { api } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Card } from "../../../ui/components";
import { Lookup, type LookupItem } from "../../../ui/Lookup";

type Line = { line: number; code: string | null; cabys: string | null; detail: string; quantity: string | number; unit: string | null; unit_price: string | number; product_id: number | null; product_name: string | null; product_code: string | null; match: string | null };
type Data = { lines: Line[]; accepted: boolean; entered: number; can_stock: boolean; warehouses: { id: number; name: string; is_default: boolean }[] };
type Pick = { product_id: number | null; label: string; quantity: string; on: boolean };

const searchGoods = async (q: string): Promise<LookupItem[]> => {
  const r = await api<{ items: { id: number; name: string; code: string; item_type: string }[] }>(`/products?limit=20&item_type=producto${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  return r.items.map((p) => ({ id: p.id, label: `${p.code} · ${p.name}` }));
};

export default function StockIn({ docId }: { docId: number }) {
  const { toast } = useSession();
  const [d, setD] = useState<Data | null>(null);
  const [picks, setPicks] = useState<Record<number, Pick>>({});
  const [wh, setWh] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  const load = () => api<Data>(`/reception/${docId}/stock-lines`).then((r) => {
    setD(r);
    setWh((w) => w ?? r.warehouses[0]?.id ?? null);
    setPicks(Object.fromEntries(r.lines.map((ln) => [ln.line, { product_id: ln.product_id, label: ln.product_id ? `${ln.product_code} · ${ln.product_name}` : "", quantity: String(Number(ln.quantity)), on: !!ln.product_id }])));
  }).catch(() => setD(null));
  useEffect(() => { load(); }, [docId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!d || !d.accepted || !d.can_stock || d.lines.length === 0) return null;
  const chosen = d.lines.filter((ln) => picks[ln.line]?.on && picks[ln.line]?.product_id);
  const go = async () => {
    if (!wh) return;
    setBusy(true);
    try {
      const r = await api<{ movimientos: number }>(`/reception/${docId}/stock-in`, { method: "POST", json: { warehouse_id: wh, items: chosen.map((ln) => ({ line: ln.line, product_id: picks[ln.line].product_id, quantity: picks[ln.line].quantity })) } });
      toast(`Entrada al inventario: ${r.movimientos} línea${r.movimientos !== 1 ? "s" : ""}`);
      load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  return (
    <Card title="Entrada al inventario">
      {d.entered > 0 ? (
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>Esta factura ya dio entrada al inventario ({d.entered} movimiento{d.entered !== 1 ? "s" : ""} en el kardex).</p>
      ) : !open ? (
        <>
          <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>Si es mercadería para vender o instalar, dale entrada: cada línea se liga a un producto y suma a la existencia propia.</p>
          <button className="btn btn--soft btn--sm" onClick={() => setOpen(true)}>Dar entrada al inventario</button>
        </>
      ) : (
        <div style={{ display: "grid", gap: 10 }}>
          <label className="meta" style={{ textTransform: "none", display: "flex", gap: 8, alignItems: "center" }}>Bodega
            <select className="select select--sm" value={wh ?? ""} onChange={(e) => setWh(Number(e.target.value))}>{d.warehouses.map((w) => <option key={w.id} value={w.id}>{w.name}{w.is_default ? " (predeterminada)" : ""}</option>)}</select>
          </label>
          {d.lines.map((ln) => {
            const p = picks[ln.line];
            return (
              <div key={ln.line} style={{ display: "grid", gap: 4, paddingTop: 8, borderTop: "1px solid var(--hair)" }}>
                <label className="acc-check"><input type="checkbox" checked={!!p?.on} onChange={(e) => setPicks({ ...picks, [ln.line]: { ...p, on: e.target.checked } })} /><span><b>Línea {ln.line}</b> · {ln.detail}{ln.code ? <span className="mono"> · {ln.code}</span> : null} · {Number(ln.quantity)} {ln.unit || ""}</span></label>
                {p?.on && (
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                    <div style={{ flex: "1 1 240px" }}><Lookup value={p.label} placeholder="Producto propio…" fetcher={searchGoods} onSelect={(it, text) => setPicks({ ...picks, [ln.line]: { ...p, product_id: it ? it.id : null, label: text } })} /></div>
                    <input className="input input--mono" style={{ width: 90 }} inputMode="decimal" aria-label="Cantidad" value={p.quantity} onChange={(e) => setPicks({ ...picks, [ln.line]: { ...p, quantity: e.target.value } })} />
                    {ln.match && p.product_id === ln.product_id && <span className="meta">{ln.match === "catalogo_proveedor" ? "sugerido por el catálogo del proveedor" : "sugerido por el código"}</span>}
                  </div>
                )}
              </div>
            );
          })}
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--crimson btn--sm" disabled={busy || !wh || chosen.length === 0} onClick={go}>{busy ? "Registrando…" : `Dar entrada (${chosen.length})`}</button>
            <button className="btn btn--ghost btn--sm" onClick={() => setOpen(false)}>Cancelar</button>
          </div>
        </div>
      )}
    </Card>
  );
}
