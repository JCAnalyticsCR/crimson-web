/* Contratos de mantenimiento: "este cliente requiere mantenimiento cada 6 meses".
   El valor no esta en la lista sino en que el sistema abre el ticket solo cuando toca. */
import { useCallback, useEffect, useState } from "react";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal, Loading } from "../../ui/components";
import { Lookup, searchCustomers, type LookupItem } from "../../ui/Lookup";
import { SlaTable, type Sla } from "./SlaTable";
import { TrashButton } from "../../ui/TrashButton";
import { useRecordAutosave } from "../../lib/useRecordAutosave";

type Contract = {
  id: number; number: string; name: string; kind: string; customer_id: number; customer: string | null; project: string | null;
  every_months: number; next_date: string | null; last_done: string | null; start_date: string | null; end_date: string | null;
  amount: string | null; currency: string | null; scope: string | null; active: boolean; notes: string | null; dias_para_la_proxima: number | null;
  sla: Sla | null;
  /** lo ejecuta una empresa aliada: sus tickets de mantenimiento nacen asignados a ella */
  ally_company_id?: number | null; ally_company?: string | null;
};

const KIND: Record<string, string> = { mantenimiento: "Mantenimiento preventivo", soporte: "Soporte recurrente", renting: "Renting", licencia: "Licencia" };
const blank = { customer_id: "", customer_name: "", name: "", kind: "mantenimiento", every_months: 6, next_date: "", start_date: "", end_date: "", amount: "0", scope: "", active: true, notes: "", sla: null as Sla | null, ally_company_id: "", ally_company: "" };
const searchAllies = async (q: string): Promise<LookupItem[]> => {
  const r = await api<{ id: number; name: string; tax_id: string | null }[]>(`/allies/options?q=${encodeURIComponent(q)}`);
  return r.map((a) => ({ id: a.id, label: a.name, hint: a.tax_id || undefined }));
};
type Form = typeof blank & { id?: number };
const ctrBody = (f: Form) => ({
  customer_id: Number(f.customer_id), name: f.name, kind: f.kind, every_months: Number(f.every_months),
  next_date: f.next_date || null, start_date: f.start_date || null, end_date: f.end_date || null,
  amount: Number(f.amount || 0), currency: "CRC", scope: f.scope || null, active: f.active, notes: f.notes || null, sla: f.sla,
  ally_company_id: f.ally_company_id ? Number(f.ally_company_id) : null,
});

export default function Contracts() {
  const { toast, allows } = useSession();
  const [rowsSt, setRows] = useState<Contract[] | null>(null); // null = cargando
  const rows = rowsSt ?? [];
  const [form, setForm] = useState<Form | null>(null);
  // el monto es lo que se le cobra al cliente (precio, no costo): lo ve quien ve precios; el tecnico no (13/10, 22:07)
  const verMonto = allows("catalog.precios");
  // crear, editar y programar contratos: administracion o supervisor (la API exige support_desk.ver_todo)
  const maneja = allows("support_desk.ver_todo");

  const load = useCallback(() => api<Contract[]>("/contracts").then(setRows), []);
  useEffect(() => { load(); }, [load]);

  /* Autoguardado (M1): un contrato existente se guarda al cerrar; uno nuevo (gasta el consecutivo CTR) queda de
     borrador en este dispositivo hasta Guardar. */
  const auto = useRecordAutosave<Form>({
    ns: "ctr", mode: "close", newRecords: "local", value: form, idOf: (f) => f.id, body: ctrBody,
    save: async (b, id, ctx) => { await api(`/contracts/${id}`, { method: "PUT", json: b, headers: ctx.headers, keepalive: ctx.keepalive }); load(); },
    enabled: !!form && maneja && (form.id ? allows("support_desk.editar") : allows("support_desk.crear")) && !!form.customer_id,
    label: (f) => (f.id ? f.name || "El contrato" : "El contrato nuevo"),
    apply: (d) => setForm((cur) => (cur ? { ...d, id: cur.id } : cur)),
  });
  const save = async () => {
    if (!form) return;
    try { await auto.exclusive(form, (id) => api(id ? `/contracts/${id}` : "/contracts", { method: id ? "PUT" : "POST", json: ctrBody(form) })); toast("Contrato guardado"); setForm(null); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const cancelar = () => { if (auto.isDirty() && !confirm("¿Descartar los cambios?")) return; auto.discard(); setForm(null); };

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
          {maneja && allows("support_desk.crear") && <button className="btn btn--crimson" onClick={() => setForm({ ...blank, next_date: new Date().toISOString().slice(0, 10) })}><Icon d={I.plus} />Nuevo contrato</button>}
        </div>
      </div>

      <div className="eco">
        <div className="eco__box"><span className="meta">Contratos activos</span><b>{rows.filter((c) => c.active).length}</b></div>
        <div className={`eco__box ${rows.some((c) => (c.dias_para_la_proxima ?? 99) < 0) ? "is-bad" : ""}`}><span className="meta">Atrasados</span><b>{rows.filter((c) => (c.dias_para_la_proxima ?? 99) < 0).length}</b></div>
        <div className="eco__box"><span className="meta">Este mes toca</span><b>{rows.filter((c) => (c.dias_para_la_proxima ?? 99) >= 0 && (c.dias_para_la_proxima ?? 99) <= 30).length}</b></div>
        {verMonto && <div className="eco__box"><span className="meta">Ingreso por vuelta</span><b className="money">{fmtMoney(rows.filter((c) => c.active).reduce((s, c) => s + Number(c.amount || 0), 0))}</b></div>}
      </div>

      <Card flush>
        {rowsSt === null ? <Loading /> : rows.length === 0 ? <Empty title="Sin contratos" hint="Un mantenimiento cada seis meses es trabajo que vuelve solo; el sistema abre el ticket cuando toca." /> : (
          <table className="table">
            <thead><tr><th>Número</th><th>Contrato</th><th>Cliente</th><th>Cada</th><th>Última</th><th>Próxima</th>{verMonto && <th className="num">Monto</th>}<th /></tr></thead>
            <tbody>{rows.map((c) => (
              <tr key={c.id} style={{ opacity: c.active ? 1 : 0.5 }}>
                <td className="mono muted">{c.number}</td>
                <td style={{ fontWeight: 600 }}>{c.name}<div className="meta">{KIND[c.kind] || c.kind}</div></td>
                <td className="muted">{c.customer || "—"}{c.ally_company && <div><span className="badge badge--info">Aliado: {c.ally_company}</span></div>}</td>
                <td className="mono muted">{c.every_months} meses</td>
                <td className="muted">{fmtDate(c.last_done)}</td>
                <td>{cuando(c)}</td>
                {verMonto && <td className="num money">{c.amount == null ? "—" : fmtMoney(c.amount, c.currency || undefined)}</td>}
                <td className="num row-actions" style={{ whiteSpace: "nowrap" }}>
                  {maneja && allows("support_desk.crear") && c.active && <button className="btn btn--soft btn--sm" onClick={() => hacerAhora(c)} title="Abre el ticket y reprograma la próxima">Programar</button>}
                  {maneja && allows("support_desk.editar") && <button className="btn btn--ghost btn--sm" onClick={() => setForm({ id: c.id, customer_id: String(c.customer_id), customer_name: c.customer || "", name: c.name, kind: c.kind, every_months: c.every_months, next_date: c.next_date || "", start_date: c.start_date || "", end_date: c.end_date || "", amount: String(c.amount ?? 0), scope: c.scope || "", active: c.active, notes: c.notes || "", sla: c.sla, ally_company_id: c.ally_company_id ? String(c.ally_company_id) : "", ally_company: c.ally_company || "" })}>Editar</button>}
                  <TrashButton kind="contract" id={c.id} number={c.number} onDone={load} />
                </td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>

      {form && (
        <Modal title={form.id ? "Editar contrato" : "Nuevo contrato de mantenimiento"} onClose={() => setForm(null)} wide foot={<>
          {auto.bar}
          <button className="btn btn--ghost" onClick={cancelar}>Cancelar</button>
          <button className="btn btn--crimson" onClick={save} disabled={!form.customer_id || form.name.trim().length < 2}>Guardar</button>
        </>}>
          <div className="grid-3">
            <Field label="Cliente"><Lookup value={form.customer_name} placeholder="Buscar cliente…" fetcher={searchCustomers} onSelect={(it, text) => setForm({ ...form, customer_id: it ? String(it.id) : "", customer_name: text })} /></Field>
            <Field label="Nombre del contrato"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="CCTV condominio · preventivo" /></Field>
            <Field label="Tipo"><select className="select" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{Object.entries(KIND).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
            <Field label="Cada cuántos meses"><input className="input input--mono" type="number" min={1} max={60} value={form.every_months} onChange={(e) => setForm({ ...form, every_months: Number(e.target.value) })} /></Field>
            <Field label="Próxima visita" hint="El sistema abre el ticket ese día."><input className="input" type="date" value={form.next_date} onChange={(e) => setForm({ ...form, next_date: e.target.value })} /></Field>
            {verMonto && <Field label="Monto por visita"><input className="input input--mono" inputMode="decimal" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></Field>}
            <Field label="Inicio"><input className="input" type="date" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} /></Field>
            <Field label="Vence" hint="Vacío = sin fecha de fin."><input className="input" type="date" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} /></Field>
            <Field label="Activo"><select className="select" value={form.active ? "1" : "0"} onChange={(e) => setForm({ ...form, active: e.target.value === "1" })}><option value="1">Sí</option><option value="0">No</option></select></Field>
            <Field label="Lo ejecuta: empresa aliada" hint="Opcional. Sus tickets de mantenimiento le llegan a su portal."><Lookup value={form.ally_company} placeholder="Crimson (sin aliado)" fetcher={searchAllies} onSelect={(it, text) => setForm({ ...form, ally_company_id: it ? String(it.id) : "", ally_company: text })} /></Field>
          </div>
          <Field label="Qué incluye" hint="Se copia al ticket para que el técnico sepa qué hacer."><textarea className="textarea" value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })} placeholder="Limpieza de lentes, revisión de grabación, respaldo de configuración y prueba de respaldo eléctrico." /></Field>
          {/* SLA propio: lo que se le vendio a este cliente. Manda sobre el SLA de la empresa mientras el contrato este activo. */}
          <Field label="SLA propio del contrato" hint="Si no se marca, los tickets de este cliente usan el SLA de la empresa (Ajustes → SLA).">
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 14 }}>
              <input type="checkbox" checked={!!form.sla} onChange={(e) => setForm({ ...form, sla: e.target.checked ? { critica: { respuesta: 2, resolucion: 8 }, alta: { respuesta: 4, resolucion: 24 }, media: { respuesta: 8, resolucion: 72 }, baja: { respuesta: 24, resolucion: 120 } } : null })} />
              Este contrato trae su propio SLA
            </label>
          </Field>
          {form.sla && <SlaTable value={form.sla} onChange={(sla) => setForm({ ...form, sla })} />}
        </Modal>
      )}
    </>
  );
}
