/* Contratos de mantenimiento: "este cliente requiere mantenimiento cada 6 meses".
   El valor no esta en la lista sino en que el sistema abre el ticket solo cuando toca. */
import { useCallback, useEffect, useState } from "react";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";
import { Lookup, searchCustomers } from "../../ui/Lookup";

type Contract = {
  id: number; number: string; name: string; kind: string; customer_id: number; customer: string | null; project: string | null;
  every_months: number; next_date: string | null; last_done: string | null; start_date: string | null; end_date: string | null;
  amount: string; currency: string; scope: string | null; active: boolean; notes: string | null; dias_para_la_proxima: number | null;
};

const KIND: Record<string, string> = { mantenimiento: "Mantenimiento preventivo", soporte: "Soporte recurrente", renting: "Renting", licencia: "Licencia" };
const blank = { customer_id: "", customer_name: "", name: "", kind: "mantenimiento", every_months: 6, next_date: "", start_date: "", end_date: "", amount: "0", scope: "", active: true, notes: "" };

export default function Contracts() {
  const { toast, allows } = useSession();
  const [rows, setRows] = useState<Contract[]>([]);
  const [form, setForm] = useState<(typeof blank & { id?: number }) | null>(null);
  const verCostos = allows("catalog.costos");

  const load = useCallback(() => api<Contract[]>("/contracts").then(setRows), []);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!form) return;
    const body = {
      customer_id: Number(form.customer_id), name: form.name, kind: form.kind, every_months: Number(form.every_months),
      next_date: form.next_date || null, start_date: form.start_date || null, end_date: form.end_date || null,
      amount: Number(form.amount || 0), currency: "CRC", scope: form.scope || null, active: form.active, notes: form.notes || null,
    };
    try { await api(form.id ? `/contracts/${form.id}` : "/contracts", { method: form.id ? "PUT" : "POST", json: body }); toast("Contrato guardado"); setForm(null); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const hacerAhora = async (c: Contract) => {
    try { const t = await api<{ number: string }>(`/contracts/${c.id}/ticket`, { method: "POST" }); toast(`Ticket ${t.number} abierto; la próxima visita quedó reprogramada`); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const cuando = (c: Contract) => {
    if (!c.next_date) return <span className="muted">—</span>;
    const d = c.dias_para_la_proxima ?? 0;
    if (d < 0) return <span className="badge badge--bad">Atrasado {Math.abs(d)} d</span>;
    if (d <= 15) return <span className="badge badge--warn">En {d} d</span>;
    return <span>{fmtDate(c.next_date)}</span>;
  };

  return (
    <>
      <div className="page-head">
        <div><div className="meta">13 · Soporte</div><h1 className="h1">Mantenimientos</h1></div>
        <div className="page-head__actions">
          {allows("support_desk.crear") && <button className="btn btn--crimson" onClick={() => setForm({ ...blank, next_date: new Date().toISOString().slice(0, 10) })}><Icon d={I.plus} />Nuevo contrato</button>}
        </div>
      </div>

      <div className="eco">
        <div className="eco__box"><span className="meta">Contratos activos</span><b>{rows.filter((c) => c.active).length}</b></div>
        <div className={`eco__box ${rows.some((c) => (c.dias_para_la_proxima ?? 99) < 0) ? "is-bad" : ""}`}><span className="meta">Atrasados</span><b>{rows.filter((c) => (c.dias_para_la_proxima ?? 99) < 0).length}</b></div>
        <div className="eco__box"><span className="meta">Este mes toca</span><b>{rows.filter((c) => (c.dias_para_la_proxima ?? 99) >= 0 && (c.dias_para_la_proxima ?? 99) <= 30).length}</b></div>
        {verCostos && <div className="eco__box"><span className="meta">Ingreso por vuelta</span><b className="money">{fmtMoney(rows.filter((c) => c.active).reduce((s, c) => s + Number(c.amount || 0), 0))}</b></div>}
      </div>

      <Card flush>
        {rows.length === 0 ? <Empty title="Sin contratos" hint="Un mantenimiento cada seis meses es trabajo que vuelve solo; el sistema abre el ticket cuando toca." /> : (
          <table className="table">
            <thead><tr><th>Número</th><th>Contrato</th><th>Cliente</th><th>Cada</th><th>Última</th><th>Próxima</th>{verCostos && <th className="num">Monto</th>}<th /></tr></thead>
            <tbody>{rows.map((c) => (
              <tr key={c.id} style={{ opacity: c.active ? 1 : 0.5 }}>
                <td className="mono muted">{c.number}</td>
                <td style={{ fontWeight: 600 }}>{c.name}<div className="meta">{KIND[c.kind] || c.kind}</div></td>
                <td className="muted">{c.customer || "—"}</td>
                <td className="mono muted">{c.every_months} meses</td>
                <td className="muted">{fmtDate(c.last_done)}</td>
                <td>{cuando(c)}</td>
                {verCostos && <td className="num money">{fmtMoney(c.amount, c.currency)}</td>}
                <td className="num" style={{ whiteSpace: "nowrap" }}>
                  {allows("support_desk.crear") && c.active && <button className="btn btn--soft btn--sm" onClick={() => hacerAhora(c)} title="Abre el ticket y reprograma la próxima">Programar</button>}
                  {allows("support_desk.editar") && <button className="btn btn--ghost btn--sm" onClick={() => setForm({ id: c.id, customer_id: String(c.customer_id), customer_name: c.customer || "", name: c.name, kind: c.kind, every_months: c.every_months, next_date: c.next_date || "", start_date: c.start_date || "", end_date: c.end_date || "", amount: String(c.amount), scope: c.scope || "", active: c.active, notes: c.notes || "" })}>Editar</button>}
                </td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>

      {form && (
        <Modal title={form.id ? "Editar contrato" : "Nuevo contrato de mantenimiento"} onClose={() => setForm(null)} wide foot={<>
          <button className="btn btn--ghost" onClick={() => setForm(null)}>Cancelar</button>
          <button className="btn btn--crimson" onClick={save} disabled={!form.customer_id || form.name.trim().length < 2}>Guardar</button>
        </>}>
          <div className="grid-3">
            <Field label="Cliente"><Lookup value={form.customer_name} placeholder="Buscar cliente…" fetcher={searchCustomers} onSelect={(it, text) => setForm({ ...form, customer_id: it ? String(it.id) : "", customer_name: text })} /></Field>
            <Field label="Nombre del contrato"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="CCTV condominio · preventivo" /></Field>
            <Field label="Tipo"><select className="select" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{Object.entries(KIND).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
            <Field label="Cada cuántos meses"><input className="input input--mono" type="number" min={1} max={60} value={form.every_months} onChange={(e) => setForm({ ...form, every_months: Number(e.target.value) })} /></Field>
            <Field label="Próxima visita" hint="El sistema abre el ticket ese día."><input className="input" type="date" value={form.next_date} onChange={(e) => setForm({ ...form, next_date: e.target.value })} /></Field>
            <Field label="Monto por visita"><input className="input input--mono" inputMode="decimal" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></Field>
            <Field label="Inicio"><input className="input" type="date" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} /></Field>
            <Field label="Vence" hint="Vacío = sin fecha de fin."><input className="input" type="date" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} /></Field>
            <Field label="Activo"><select className="select" value={form.active ? "1" : "0"} onChange={(e) => setForm({ ...form, active: e.target.value === "1" })}><option value="1">Sí</option><option value="0">No</option></select></Field>
          </div>
          <Field label="Qué incluye" hint="Se copia al ticket para que el técnico sepa qué hacer."><textarea className="textarea" value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })} placeholder="Limpieza de lentes, revisión de grabación, respaldo de configuración y prueba de respaldo eléctrico." /></Field>
        </Modal>
      )}
    </>
  );
}
