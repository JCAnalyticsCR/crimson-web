/* Solicitudes de compra: lo que hay que pedirle al proveedor.
   Nacen de tres lados: del proyecto (lo que falta), del inventario bajo el minimo (el worker las arma de madrugada)
   o a mano. Al marcarlas recibidas, el material entra a bodega con su costo. */
import { useCallback, useEffect, useState } from "react";
import { api, fmtDate, fmtMoney, parseTs } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";
import { Lookup, searchProducts } from "../../ui/Lookup";

type Line = { id?: number; product_id: number | null; name: string; quantity: string; unit_cost: string };
type Req = {
  id: number; number: string; status: string; reason: string; supplier_id: number | null; supplier: string | null;
  project_id: number | null; project: string | null; total_cost: string; currency: string; notes: string | null; created_at: string; lines: Line[];
};
type Named = { id: number; name: string };

const STATUS: Record<string, { label: string; tone: string }> = {
  borrador: { label: "Borrador", tone: "muted" }, solicitada: { label: "Solicitada", tone: "info" },
  recibida: { label: "Recibida", tone: "ok" }, cancelada: { label: "Cancelada", tone: "muted" },
};
const REASON: Record<string, string> = { proyecto: "Materiales de proyecto", stock_bajo: "Inventario bajo el mínimo", manual: "Pedido manual" };

export default function Purchases() {
  const { toast, allows } = useSession();
  const [rows, setRows] = useState<Req[]>([]);
  const [open, setOpen] = useState<Req | null>(null);
  const [nuevo, setNuevo] = useState<{ supplier_id: string; notes: string; lines: Line[] } | null>(null);
  const [suppliers, setSuppliers] = useState<Named[]>([]);
  const verCostos = allows("catalog.precios");

  const load = useCallback(() => api<Req[]>("/purchase-requests").then(setRows), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    api<Named[]>("/suppliers").then(setSuppliers).catch(() => setSuppliers([]));
  }, []);

  const mover = async (r: Req, status: string) => {
    if (status === "recibida" && !confirm("Al recibirla, el material entra a bodega con su costo. ¿Confirmás?")) return;
    try { const up = await api<Req>(`/purchase-requests/${r.id}`, { method: "PATCH", json: { status } }); toast(`Solicitud ${STATUS[status]?.label.toLowerCase()}`); setOpen(up); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const crear = async () => {
    if (!nuevo) return;
    const lines = nuevo.lines.filter((l) => l.name.trim() && Number(l.quantity) > 0);
    if (!lines.length) return toast("Agregá al menos una línea", "bad");
    try {
      await api("/purchase-requests", { method: "POST", json: { supplier_id: nuevo.supplier_id ? Number(nuevo.supplier_id) : null, notes: nuevo.notes || null, lines: lines.map((l) => ({ product_id: l.product_id, name: l.name, quantity: Number(l.quantity), unit_cost: Number(l.unit_cost || 0) })) } });
      toast("Solicitud creada"); setNuevo(null); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const setLine = (i: number, patch: Partial<Line>) => setNuevo((n) => (n ? { ...n, lines: n.lines.map((l, j) => (j === i ? { ...l, ...patch } : l)) } : n));

  return (
    <>
      <div className="page-head">
        <div><div className="meta">11 · Operación</div><h1 className="h1">Solicitudes de compra</h1></div>
        <div className="page-head__actions">
          {allows("purchases.crear") && <button className="btn btn--crimson" onClick={() => setNuevo({ supplier_id: "", notes: "", lines: [{ product_id: null, name: "", quantity: "1", unit_cost: "0" }] })}><Icon d={I.plus} />Nueva solicitud</button>}
        </div>
      </div>
      <Card flush>
        {rows.length === 0 ? <Empty title="Nada por comprar" hint="Cuando a un proyecto le falte material o el inventario baje del mínimo, la solicitud aparece aquí." /> : (
          <table className="table">
            <thead><tr><th>Número</th><th>Motivo</th><th>Proveedor</th><th>Proyecto</th><th className="num">Líneas</th>{verCostos && <th className="num">Costo</th>}<th>Estado</th><th>Creada</th><th /></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id}>
                <td className="mono muted">{r.number}</td><td>{REASON[r.reason] || r.reason}</td><td className="muted">{r.supplier || "—"}</td>
                <td className="mono muted">{r.project || "—"}</td><td className="num mono">{r.lines.length}</td>
                {verCostos && <td className="num money">{fmtMoney(r.total_cost, r.currency)}</td>}
                <td><span className={`badge badge--${STATUS[r.status]?.tone || "muted"}`}>{STATUS[r.status]?.label || r.status}</span></td>
                <td className="muted">{fmtDate(parseTs(r.created_at).toISOString().slice(0, 10))}</td>
                <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setOpen(r)}>Abrir</button></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>

      {open && (
        <Modal title={`${open.number} · ${REASON[open.reason] || open.reason}`} onClose={() => setOpen(null)} wide foot={<>
          {allows("purchases.crear") && open.status === "borrador" && <button className="btn btn--soft" style={{ marginRight: "auto" }} onClick={() => mover(open, "solicitada")}>Marcar como solicitada</button>}
          {allows("purchases.crear") && open.status !== "recibida" && open.status !== "cancelada" && <button className="btn btn--crimson" onClick={() => mover(open, "recibida")}>Recibí el material</button>}
          <button className="btn btn--ghost" onClick={() => setOpen(null)}>Cerrar</button>
        </>}>
          <p className="muted" style={{ fontSize: 13.5, marginTop: 0 }}>
            {open.supplier || "Sin proveedor asignado"}{open.project ? ` · Proyecto ${open.project}` : ""} · <span className={`badge badge--${STATUS[open.status]?.tone}`}>{STATUS[open.status]?.label}</span>
          </p>
          <table className="table">
            <thead><tr><th>Material</th><th className="num">Cantidad</th>{verCostos && <><th className="num">Costo unit.</th><th className="num">Total</th></>}</tr></thead>
            <tbody>{open.lines.map((l, i) => (
              <tr key={l.id ?? i}>
                <td style={{ fontWeight: 600 }}>{l.name}</td><td className="num mono">{Number(l.quantity)}</td>
                {verCostos && <><td className="num money">{fmtMoney(l.unit_cost)}</td><td className="num money">{fmtMoney(Number(l.unit_cost) * Number(l.quantity))}</td></>}
              </tr>
            ))}</tbody>
          </table>
          {open.notes && <p className="muted" style={{ fontSize: 13 }}>{open.notes}</p>}
        </Modal>
      )}

      {nuevo && (
        <Modal title="Nueva solicitud de compra" onClose={() => setNuevo(null)} wide foot={<>
          <button className="btn btn--ghost" onClick={() => setNuevo(null)}>Cancelar</button>
          <button className="btn btn--crimson" onClick={crear}>Crear solicitud</button>
        </>}>
          <div className="grid-2">
            <Field label="Proveedor"><select className="select" value={nuevo.supplier_id} onChange={(e) => setNuevo({ ...nuevo, supplier_id: e.target.value })}><option value="">—</option>{suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
            <Field label="Notas"><input className="input" value={nuevo.notes} onChange={(e) => setNuevo({ ...nuevo, notes: e.target.value })} placeholder="Para la obra de Los Robles, urge el jueves" /></Field>
          </div>
          <table className="table">
            <thead><tr><th>Material</th><th>Inventario</th><th className="num">Cantidad</th><th className="num">Costo unit.</th><th /></tr></thead>
            <tbody>{nuevo.lines.map((l, i) => (
              <tr key={i}>
                <td><Lookup value={l.name} placeholder="Buscar en el catálogo…" fetcher={searchProducts} onSelect={(pr, text) => setLine(i, { product_id: pr ? pr.id : null, name: text })} /></td>
                <td className="muted" style={{ fontSize: 12.5 }}>{l.product_id ? "Entra a bodega" : "Sin código"}</td>
                <td className="num"><input className="input input--mono" style={{ maxWidth: 100 }} inputMode="decimal" value={l.quantity} onChange={(e) => setLine(i, { quantity: e.target.value })} /></td>
                <td className="num"><input className="input input--mono" style={{ maxWidth: 120 }} inputMode="decimal" value={l.unit_cost} onChange={(e) => setLine(i, { unit_cost: e.target.value })} /></td>
                <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setNuevo({ ...nuevo, lines: nuevo.lines.filter((_, j) => j !== i) })}><Icon d={I.x} size={14} /></button></td>
              </tr>
            ))}</tbody>
          </table>
          <button className="btn btn--soft btn--sm" style={{ marginTop: 10 }} onClick={() => setNuevo({ ...nuevo, lines: [...nuevo.lines, { product_id: null, name: "", quantity: "1", unit_cost: "0" }] })}><Icon d={I.plus} />Agregar línea</button>
        </Modal>
      )}
    </>
  );
}
