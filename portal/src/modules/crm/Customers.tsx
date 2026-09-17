import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Customer } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";

const blank = { id_type: "juridica", id_number: "", name: "", email: "", phone: "", whatsapp: "", currency: "CRC", notes: "" };

export default function Customers() {
  const { toast } = useSession();
  const [params, setParams] = useSearchParams();
  const [items, setItems] = useState<Customer[]>([]);
  const [q, setQ] = useState("");
  const [edit, setEdit] = useState<typeof blank & { id?: number } | null>(params.get("nuevo") ? { ...blank } : null);
  const load = useCallback(() => api<{ items: Customer[] }>(`/customers?limit=50${q ? `&q=${encodeURIComponent(q)}` : ""}`).then((r) => setItems(r.items)), [q]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);

  const save = async () => {
    if (!edit) return;
    try {
      const { id, ...body } = edit;
      await api(id ? `/customers/${id}` : "/customers", { method: id ? "PUT" : "POST", json: body });
      toast("Cliente guardado"); setEdit(null); setParams({}); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  return (
    <>
      <div className="page-head">
        <div><div className="meta">04 · Clientes</div><h1 className="h1">Clientes</h1></div>
        <div className="page-head__actions"><button className="btn btn--crimson" onClick={() => setEdit({ ...blank })}><Icon d={I.plus} />Crear cliente</button></div>
      </div>
      <Card flush>
        <div className="list-head"><div className="search" style={{ maxWidth: 420 }}><Icon d={I.search} size={16} /><input placeholder="Nombre, cédula o correo…" value={q} onChange={(e) => setQ(e.target.value)} /></div><button className="btn btn--ghost btn--sm" onClick={load}><Icon d={I.refresh} /></button></div>
        {items.length === 0 ? <Empty hint="Creá el primer cliente para cotizar y facturar." /> : (
          <table className="table">
            <thead><tr><th>Nombre</th><th>Identificación</th><th>Correo</th><th>WhatsApp</th><th>Divisa</th><th /></tr></thead>
            <tbody>{items.map((c) => (
              <tr key={c.id}><td style={{ fontWeight: 600 }}>{c.name}</td><td className="mono muted">{c.id_type} · {c.id_number || "—"}</td><td className="muted">{c.email || "—"}</td><td className="muted">{c.whatsapp || c.phone || "—"}</td><td>{c.currency}</td><td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setEdit({ id: c.id, id_type: c.id_type, id_number: c.id_number || "", name: c.name, email: c.email || "", phone: c.phone || "", whatsapp: c.whatsapp || "", currency: c.currency, notes: "" })}>Ver</button></td></tr>
            ))}</tbody>
          </table>
        )}
      </Card>
      {edit && (
        <Modal title={edit.id ? "Cliente" : "Nuevo cliente"} onClose={() => { setEdit(null); setParams({}); }} foot={<><button className="btn btn--ghost" onClick={() => setEdit(null)}>Cancelar</button><button className="btn btn--crimson" onClick={save}>Guardar</button></>}>
          <div className="grid-2">
            <Field label="Tipo de identificación"><select className="select" value={edit.id_type} onChange={(e) => setEdit({ ...edit, id_type: e.target.value })}>{["fisica", "juridica", "dimex", "nite", "extranjero"].map((t) => <option key={t} value={t}>{t}</option>)}</select></Field>
            <Field label="Número"><input className="input input--mono" style={{ textAlign: "left" }} value={edit.id_number} onChange={(e) => setEdit({ ...edit, id_number: e.target.value })} /></Field>
          </div>
          <Field label="Nombre o razón social"><input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
          <div className="grid-2">
            <Field label="Correo" hint="Recibe cotizaciones, facturas y XML."><input className="input" type="email" value={edit.email} onChange={(e) => setEdit({ ...edit, email: e.target.value })} /></Field>
            <Field label="WhatsApp" hint="Para enlaces de pago y recordatorios."><input className="input" value={edit.whatsapp} onChange={(e) => setEdit({ ...edit, whatsapp: e.target.value })} /></Field>
            <Field label="Teléfono"><input className="input" value={edit.phone} onChange={(e) => setEdit({ ...edit, phone: e.target.value })} /></Field>
            <Field label="Divisa preferida"><select className="select" value={edit.currency} onChange={(e) => setEdit({ ...edit, currency: e.target.value })}><option>CRC</option><option>USD</option></select></Field>
          </div>
          <Field label="Notas"><textarea className="textarea" value={edit.notes} onChange={(e) => setEdit({ ...edit, notes: e.target.value })} /></Field>
        </Modal>
      )}
    </>
  );
}
