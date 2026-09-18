/* Cupones de descuento para la tienda en linea. */
import { useEffect, useState } from "react";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Card, Empty, Field, I, Icon, Modal } from "../../ui/components";

type Coupon = { id: number; code: string; kind: string; value: string; min_total: string; valid_from: string | null; valid_to: string | null; max_uses: number | null; uses: number; active: boolean };
const blank = { code: "", kind: "percent", value: "10", min_total: "0", valid_from: "", valid_to: "", max_uses: "", active: true };

export default function Coupons() {
  const { toast } = useSession();
  const [items, setItems] = useState<Coupon[]>([]);
  const [edit, setEdit] = useState<(typeof blank & { id?: number }) | null>(null);
  const load = () => api<Coupon[]>("/coupons").then(setItems);
  useEffect(() => { load(); }, []);
  const save = async () => {
    if (!edit) return;
    const { id, ...b } = edit;
    try {
      await api(id ? `/coupons/${id}` : "/coupons", { method: id ? "PUT" : "POST", json: { ...b, value: Number(b.value), min_total: Number(b.min_total || 0), valid_from: b.valid_from || null, valid_to: b.valid_to || null, max_uses: b.max_uses ? Number(b.max_uses) : null } });
      toast("Cupón guardado"); setEdit(null); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  return (
    <>
      <div className="page-head">
        <div><div className="meta">05 · Catálogo</div><h1 className="h1">Cupones</h1></div>
        <div className="page-head__actions"><button className="btn btn--crimson" onClick={() => setEdit({ ...blank })}><Icon d={I.plus} />Crear cupón</button></div>
      </div>
      <Card flush>
        {items.length === 0 ? <Empty hint="Descuentos por porcentaje o monto para la tienda en línea, con vigencia y límite de usos." /> : (
          <table className="table">
            <thead><tr><th>Código</th><th>Descuento</th><th>Mínimo</th><th>Vigencia</th><th className="num">Usos</th><th>Estado</th><th /></tr></thead>
            <tbody>{items.map((c) => (
              <tr key={c.id}>
                <td className="mono" style={{ fontWeight: 700, letterSpacing: ".06em" }}>{c.code}</td>
                <td>{c.kind === "percent" ? `${Number(c.value)} %` : fmtMoney(c.value)}</td>
                <td className="money muted">{Number(c.min_total) > 0 ? fmtMoney(c.min_total) : "—"}</td>
                <td className="muted">{c.valid_from || c.valid_to ? `${fmtDate(c.valid_from)} → ${fmtDate(c.valid_to)}` : "Sin vencimiento"}</td>
                <td className="num mono">{c.uses}{c.max_uses ? ` / ${c.max_uses}` : ""}</td>
                <td><Badge status={c.active ? "confirmado" : "anulada"} /></td>
                <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setEdit({ id: c.id, code: c.code, kind: c.kind, value: String(Number(c.value)), min_total: String(Number(c.min_total)), valid_from: c.valid_from || "", valid_to: c.valid_to || "", max_uses: c.max_uses ? String(c.max_uses) : "", active: c.active })}>Ver</button></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
      {edit && (
        <Modal title={edit.id ? "Cupón" : "Nuevo cupón"} onClose={() => setEdit(null)} foot={<><button className="btn btn--ghost" onClick={() => setEdit(null)}>Cancelar</button><button className="btn btn--crimson" onClick={save}>Guardar</button></>}>
          <div className="grid-3">
            <Field label="Código"><input className="input input--mono" style={{ textAlign: "left", textTransform: "uppercase" }} value={edit.code} onChange={(e) => setEdit({ ...edit, code: e.target.value.toUpperCase() })} /></Field>
            <Field label="Tipo"><select className="select" value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value })}><option value="percent">Porcentaje</option><option value="amount">Monto fijo</option></select></Field>
            <Field label="Valor"><input className="input input--mono" value={edit.value} onChange={(e) => setEdit({ ...edit, value: e.target.value })} /></Field>
            <Field label="Compra mínima"><input className="input input--mono" value={edit.min_total} onChange={(e) => setEdit({ ...edit, min_total: e.target.value })} /></Field>
            <Field label="Desde"><input className="input" type="date" value={edit.valid_from} onChange={(e) => setEdit({ ...edit, valid_from: e.target.value })} /></Field>
            <Field label="Hasta"><input className="input" type="date" value={edit.valid_to} onChange={(e) => setEdit({ ...edit, valid_to: e.target.value })} /></Field>
            <Field label="Usos máximos"><input className="input input--mono" value={edit.max_uses} placeholder="Ilimitado" onChange={(e) => setEdit({ ...edit, max_uses: e.target.value })} /></Field>
          </div>
          <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} />Activo</label>
        </Modal>
      )}
    </>
  );
}
