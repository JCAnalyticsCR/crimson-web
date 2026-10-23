import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type Customer } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal, Loading } from "../../ui/components";
import { TrashButton } from "../../ui/TrashButton";
import { useRecordAutosave } from "../../lib/useRecordAutosave";

const blank = { id_type: "juridica", id_number: "", name: "", email: "", phone: "", whatsapp: "", currency: "CRC", notes: "" };
type Edit = typeof blank & { id?: number };
const custBody = (e: Edit) => { const b: Record<string, unknown> = { ...e }; delete b.id; return b; };

export default function Customers() {
  const { toast, allows } = useSession();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const [itemsSt, setItems] = useState<Customer[] | null>(null); // null = cargando
  const items = itemsSt ?? [];
  const [q, setQ] = useState("");
  const [edit, setEdit] = useState<typeof blank & { id?: number } | null>(params.get("nuevo") ? { ...blank } : null);
  const load = useCallback(() => api<{ items: Customer[] }>(`/customers?limit=50${q ? `&q=${encodeURIComponent(q)}` : ""}`).then((r) => setItems(r.items)), [q]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);

  /* Autoguardado (C1): un cliente existente se guarda al cerrar; uno nuevo queda de borrador hasta Guardar. */
  const auto = useRecordAutosave<Edit>({
    ns: "cli", mode: "close", newRecords: "local", value: edit, idOf: (e) => e.id, body: custBody,
    save: async (b, id, ctx) => { await api(`/customers/${id}`, { method: "PUT", json: b, headers: ctx.headers, keepalive: ctx.keepalive }); load(); },
    enabled: !!edit && (edit.id ? allows("crm.editar") : allows("crm.crear")),
    label: (e) => (e.id ? e.name || "El cliente" : "El cliente nuevo"),
    apply: (d) => setEdit((cur) => (cur ? { ...d, id: cur.id } : cur)),
  });
  const cancelar = () => { if (auto.isDirty() && !confirm("¿Descartar los cambios?")) return; auto.discard(); setEdit(null); setParams({}); };

  /* cartera con dueño (ventas etapa 2): el vendedor solo ve lo suyo; un cliente sin vendedor se toma por cédula o correo exactos */
  const soloCartera = allows("sales.crear") && !allows("sales.ver_todo");
  const claim = async (c: Customer) => {
    if (!confirm(`¿Tomar a ${c.name} para su cartera? Desde ahora usted será su vendedor.`)) return;
    try { await api(`/customers/${c.id}/claim`, { method: "POST", json: { query: q.trim() } }); toast("Cliente agregado a su cartera"); nav(`/clientes/${c.id}`); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const save = async () => {
    if (!edit) return;
    try {
      const { id } = edit;
      const saved = await auto.exclusive(edit, () => api<{ id: number }>(id ? `/customers/${id}` : "/customers", { method: id ? "PUT" : "POST", json: custBody(edit) }));
      toast("Cliente guardado"); setEdit(null); setParams({});
      if (!id && !params.get("volver")) nav(`/clientes/${saved.id}`); else load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  return (
    <>
      <div className="page-head">
        <div><div className="meta">04 · Clientes</div><h1 className="h1">Clientes</h1></div>
        <div className="page-head__actions">{allows("crm.crear") && <button className="btn btn--crimson" onClick={() => setEdit({ ...blank })}><Icon d={I.plus} />Crear cliente</button>}</div>
      </div>
      <Card flush>
        <div className="list-head"><div className="search" style={{ maxWidth: 420 }}><Icon d={I.search} size={16} /><input placeholder="Nombre, cédula o correo…" value={q} onChange={(e) => setQ(e.target.value)} /></div><button className="btn btn--ghost btn--sm" onClick={load}><Icon d={I.refresh} /></button></div>
        {soloCartera && <p className="muted" style={{ fontSize: 12.5, margin: "0 16px 10px" }}>Ve solo los clientes de su cartera. Para tomar un cliente que no tiene vendedor, búsquelo por su cédula o su correo exactos.</p>}
        {itemsSt === null ? <Loading /> : items.length === 0 ? <Empty hint={soloCartera ? "Todavía no tiene clientes en su cartera. Cree uno o búsquelo por su cédula o correo exactos." : "Creá el primer cliente para cotizar y facturar."} /> : (
          <table className="table table--stack">
            <thead><tr><th>Nombre</th><th>Identificación</th><th>Correo</th><th>WhatsApp</th><th>Vendedor de la cuenta</th><th>Divisa</th><th /></tr></thead>
            <tbody>{items.map((c) => (
              <tr key={c.id}>
                <td data-label="Nombre" className="stack-full">{c.claimable ? <b>{c.name}</b> : <Link className="row-link" to={`/clientes/${c.id}`}>{c.name}</Link>}</td>
                <td data-label="Identificación" className="mono muted">{c.id_type} · {c.id_number || "—"}</td>
                <td data-label="Correo" className="muted">{c.email || "—"}</td>
                <td data-label="WhatsApp" className="muted">{c.whatsapp || c.phone || "—"}</td>
                <td data-label="Vendedor de la cuenta">{c.account_owner_name || <span className="muted">Sin vendedor</span>}</td>
                <td data-label="Divisa">{c.currency}</td>
                <td className="num row-actions stack-actions"><span className="row-actions__in">{c.claimable
                  ? <button className="btn btn--crimson btn--sm" onClick={() => claim(c)}>Tomar para mi cartera</button>
                  : <><Link className="btn btn--ghost btn--sm" to={`/clientes/${c.id}`}>Ver ficha</Link><TrashButton kind="customer" id={c.id} number={c.name} onDone={load} /></>}</span></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
      {edit && (
        <Modal title={edit.id ? "Cliente" : "Nuevo cliente"} onClose={() => { setEdit(null); setParams({}); }} foot={<>{auto.bar}<button className="btn btn--ghost" onClick={cancelar}>Cancelar</button><button className="btn btn--crimson" onClick={save}>Guardar</button></>}>
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
