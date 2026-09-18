/* Planillas: colaboradores, corridas de planilla y tasas CCSS/impuesto. */
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Card, Empty, Field, I, Icon, Modal } from "../../ui/components";
import AuthLink from "../../ui/AuthLink";

/* ---------- tipos ---------- */
type Employee = {
  id: number; name: string; id_number: string; email: string | null; phone: string | null;
  position: string | null; salary: string; frequency: "mensual" | "quincenal";
  start_date: string; iban: string | null; children: number; spouse_credit: boolean; active: boolean;
};
type RunListItem = {
  id: number; period_start: string; period_end: string; frequency: string;
  status: "borrador" | "aprobada" | "pagada";
  gross: string; ccss_worker: string; income_tax: string; other_deductions: string; net: string;
  ccss_employer: string; provisions: string; employer_cost: string; expense_id: number | null; people: number;
};
type RunLine = {
  id: number; employee_id: number; employee_name: string;
  base: string; overtime: string; bonus: string; other_deductions: string;
  gross: string; ccss_worker: string; income_tax: string; net: string;
  ccss_employer: string; provisions: string;
};
type RunDetail = RunListItem & { rates: Record<string, number>; lines: RunLine[] };
type Bracket = [number | null, number];
type PayrollSettings = {
  ccss_worker: number; ccss_employer: number;
  brackets: Bracket[]; child_credit: number; spouse_credit: number;
  aguinaldo: number; vacaciones: number; year: number;
  defaults: { ccss_worker: number; ccss_employer: number; brackets: Bracket[]; child_credit: number; spouse_credit: number; aguinaldo: number; vacaciones: number; year: number };
};
type BankAccount = { id: number; name: string; bank: string; currency: string };

const TABS = [["planillas", "Planillas"], ["colaboradores", "Colaboradores"], ["tasas", "Tasas"]];

const blankEmp: Omit<Employee, "id"> = {
  name: "", id_number: "", email: "", phone: "", position: "", salary: "",
  frequency: "mensual", start_date: new Date().toISOString().slice(0, 10),
  iban: "", children: 0, spouse_credit: false, active: true,
};

function todayRange(freq: "mensual" | "quincenal"): { start: string; end: string } {
  const now = new Date();
  const y = now.getFullYear(), m = now.getMonth();
  if (freq === "mensual") {
    return {
      start: new Date(y, m, 1).toISOString().slice(0, 10),
      end: new Date(y, m + 1, 0).toISOString().slice(0, 10),
    };
  }
  const d = now.getDate();
  if (d <= 15) {
    return { start: new Date(y, m, 1).toISOString().slice(0, 10), end: new Date(y, m, 15).toISOString().slice(0, 10) };
  }
  return { start: new Date(y, m, 16).toISOString().slice(0, 10), end: new Date(y, m + 1, 0).toISOString().slice(0, 10) };
}

/* ========================= componente principal ========================= */
export default function Payroll() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "planillas";

  return (
    <>
      <div className="page-head">
        <div><div className="meta">10 · Planillas</div><h1 className="h1">Planillas</h1></div>
      </div>
      <div className="tabs" style={{ alignSelf: "flex-start" }}>
        {TABS.map(([k, l]) => (
          <button key={k} className={tab === k ? "is-active" : ""} onClick={() => setParams({ tab: k })}>{l}</button>
        ))}
      </div>
      {tab === "planillas" && <TabRuns />}
      {tab === "colaboradores" && <TabEmployees />}
      {tab === "tasas" && <TabRates />}
    </>
  );
}

/* ========================= TAB: Planillas ========================= */
function TabRuns() {
  const { toast } = useSession();
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [newModal, setNewModal] = useState(false);
  const [newForm, setNewForm] = useState<{ period_start: string; period_end: string; frequency: "mensual" | "quincenal"; notes: string }>(() => {
    const r = todayRange("mensual");
    return { period_start: r.start, period_end: r.end, frequency: "mensual", notes: "" };
  });
  const [payModal, setPayModal] = useState<{ run: RunListItem } | null>(null);
  const [payForm, setPayForm] = useState({ date: new Date().toISOString().slice(0, 10), bank_account_id: "" });
  const [banks, setBanks] = useState<BankAccount[]>([]);
  const [busy, setBusy] = useState(false);

  const load = () => api<RunListItem[]>("/payroll/runs").then(setRuns).catch((e) => toast(e.message, "bad"));
  const loadDetail = (id: number) => api<RunDetail>(`/payroll/runs/${id}`).then(setDetail).catch((e) => toast(e.message, "bad"));
  useEffect(() => { load(); api<BankAccount[]>("/settings/bank-accounts").then(setBanks); }, []);

  const createRun = async () => {
    setBusy(true);
    try {
      const r = await api<RunDetail>("/payroll/runs", { method: "POST", json: { period_start: newForm.period_start, period_end: newForm.period_end, frequency: newForm.frequency, notes: newForm.notes || undefined } });
      toast("Planilla creada"); setNewModal(false); load(); setDetail(r);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  const approve = async (id: number) => {
    try { await api(`/payroll/runs/${id}/approve`, { method: "POST" }); toast("Planilla aprobada"); load(); loadDetail(id); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const remove = async (id: number) => {
    if (!confirm("¿Eliminar esta planilla borrador?")) return;
    try { await api(`/payroll/runs/${id}`, { method: "DELETE" }); toast("Planilla eliminada"); load(); setDetail(null); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const markPaid = async () => {
    if (!payModal) return;
    try {
      await api(`/payroll/runs/${payModal.run.id}/pay`, { method: "POST", json: { date: payForm.date, bank_account_id: payForm.bank_account_id ? Number(payForm.bank_account_id) : undefined } });
      toast("Planilla marcada como pagada"); setPayModal(null); load(); loadDetail(payModal.run.id);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const updateLine = async (runId: number, lid: number, patch: { overtime: string; bonus: string; other_deductions: string }) => {
    try {
      const updated = await api<RunDetail>(`/payroll/runs/${runId}/lines/${lid}`, { method: "PUT", json: { overtime: Number(patch.overtime), bonus: Number(patch.bonus), other_deductions: Number(patch.other_deductions) } });
      setDetail(updated);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const statusTone = (s: string) => s === "pagada" ? "pagada" : s === "aprobada" ? "confirmado" : "creado";

  return (
    <>
      <Card title="Corridas de planilla" flush extra={
        <button className="btn btn--crimson btn--sm" onClick={() => setNewModal(true)}><Icon d={I.plus} />Nueva planilla</button>
      }>
        {runs.length === 0
          ? <Empty hint="Generá la primera planilla para calcular salarios, deducciones CCSS e impuesto al salario." />
          : (
            <table className="table">
              <thead><tr><th>Período</th><th>Frecuencia</th><th>Personas</th><th className="num">Bruto</th><th className="num">Deducciones</th><th className="num">Neto</th><th className="num">Costo patronal</th><th>Estado</th><th /></tr></thead>
              <tbody>{runs.map((r) => (
                <tr key={r.id}>
                  <td style={{ fontWeight: 600 }}>{fmtDate(r.period_start)} – {fmtDate(r.period_end)}</td>
                  <td className="muted" style={{ textTransform: "capitalize" }}>{r.frequency}</td>
                  <td className="num muted">{r.people}</td>
                  <td className="num money">{fmtMoney(r.gross)}</td>
                  <td className="num money">{fmtMoney(Number(r.ccss_worker) + Number(r.income_tax) + Number(r.other_deductions))}</td>
                  <td className="num money" style={{ fontWeight: 700 }}>{fmtMoney(r.net)}</td>
                  <td className="num money">{fmtMoney(r.employer_cost)}</td>
                  <td><Badge status={statusTone(r.status)} /></td>
                  <td className="num" style={{ whiteSpace: "nowrap", display: "flex", gap: 4 }}>
                    <button className="btn btn--ghost btn--sm" onClick={() => loadDetail(r.id)}>Ver</button>
                    <AuthLink path={`/reports/planilla?from=${r.period_start}&to=${r.period_end}&format=xlsx`} download={`planilla-${r.period_start}.xlsx`} className="btn btn--ghost btn--sm">Excel</AuthLink>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          )}
      </Card>

      {newModal && (
        <Modal title="Nueva planilla" onClose={() => setNewModal(false)} foot={
          <><button className="btn btn--ghost" onClick={() => setNewModal(false)}>Cancelar</button>
            <button className="btn btn--crimson" onClick={createRun} disabled={busy}>{busy ? <span className="spinner" /> : "Crear"}</button></>
        }>
          <div className="grid-2">
            <Field label="Frecuencia">
              <select className="select" value={newForm.frequency} onChange={(e) => {
                const f = e.target.value as "mensual" | "quincenal";
                const r = todayRange(f);
                setNewForm({ ...newForm, frequency: f, period_start: r.start, period_end: r.end });
              }}>
                <option value="mensual">Mensual</option>
                <option value="quincenal">Quincenal</option>
              </select>
            </Field>
            <div />
            <Field label="Inicio del período"><input className="input" type="date" value={newForm.period_start} onChange={(e) => setNewForm({ ...newForm, period_start: e.target.value })} /></Field>
            <Field label="Fin del período"><input className="input" type="date" value={newForm.period_end} onChange={(e) => setNewForm({ ...newForm, period_end: e.target.value })} /></Field>
          </div>
          <Field label="Notas (opcional)"><input className="input" value={newForm.notes} onChange={(e) => setNewForm({ ...newForm, notes: e.target.value })} /></Field>
        </Modal>
      )}

      {payModal && (
        <Modal title="Marcar como pagada" onClose={() => setPayModal(null)} foot={
          <><button className="btn btn--ghost" onClick={() => setPayModal(null)}>Cancelar</button>
            <button className="btn btn--crimson" onClick={markPaid}>Confirmar pago</button></>
        }>
          <div className="grid-2">
            <Field label="Fecha de pago"><input className="input" type="date" value={payForm.date} onChange={(e) => setPayForm({ ...payForm, date: e.target.value })} /></Field>
            <Field label="Cuenta bancaria">
              <select className="select" value={payForm.bank_account_id} onChange={(e) => setPayForm({ ...payForm, bank_account_id: e.target.value })}>
                <option value="">— ninguna —</option>
                {banks.map((b) => <option key={b.id} value={b.id}>{b.name} · {b.bank}</option>)}
              </select>
            </Field>
          </div>
        </Modal>
      )}

      {detail && (
        <RunDetailPanel
          detail={detail}
          onClose={() => setDetail(null)}
          onApprove={() => approve(detail.id)}
          onDelete={() => remove(detail.id)}
          onPay={(run) => { setPayModal({ run }); setPayForm({ date: new Date().toISOString().slice(0, 10), bank_account_id: "" }); }}
          onLineUpdate={(lid, patch) => updateLine(detail.id, lid, patch)}
        />
      )}
    </>
  );
}

/* ---------- panel de detalle de planilla ---------- */
type EditableLine = RunLine & { _overtime: string; _bonus: string; _other: string };

function RunDetailPanel({ detail, onClose, onApprove, onDelete, onPay, onLineUpdate }: {
  detail: RunDetail;
  onClose: () => void;
  onApprove: () => void;
  onDelete: () => void;
  onPay: (run: RunListItem) => void;
  onLineUpdate: (lid: number, patch: { overtime: string; bonus: string; other_deductions: string }) => void;
}) {
  const [lines, setLines] = useState<EditableLine[]>(() =>
    detail.lines.map((l) => ({ ...l, _overtime: String(Number(l.overtime)), _bonus: String(Number(l.bonus)), _other: String(Number(l.other_deductions)) }))
  );
  useEffect(() => {
    setLines(detail.lines.map((l) => ({ ...l, _overtime: String(Number(l.overtime)), _bonus: String(Number(l.bonus)), _other: String(Number(l.other_deductions)) })));
  }, [detail]);

  const canEdit = detail.status === "borrador";
  const statusTone = (s: string) => s === "pagada" ? "pagada" : s === "aprobada" ? "confirmado" : "creado";

  const saveLineBlur = (l: EditableLine) => {
    onLineUpdate(l.id, { overtime: l._overtime, bonus: l._bonus, other_deductions: l._other });
  };

  return (
    <Modal title={`Planilla ${fmtDate(detail.period_start)} – ${fmtDate(detail.period_end)}`} onClose={onClose} wide foot={
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <Badge status={statusTone(detail.status)} />
        <span style={{ marginRight: "auto" }} />
        {detail.status === "borrador" && (
          <><button className="btn btn--danger btn--sm" onClick={onDelete}>Eliminar</button>
            <button className="btn btn--crimson" onClick={onApprove}>Aprobar</button></>
        )}
        {detail.status === "aprobada" && (
          <button className="btn btn--crimson" onClick={() => onPay(detail)}>Marcar pagada</button>
        )}
        {detail.status === "pagada" && detail.expense_id && (
          <span className="muted" style={{ fontSize: 13 }}>Gasto registrado en Contabilidad (#{detail.expense_id})</span>
        )}
      </div>
    }>
      {/* totales KPI */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(140px,1fr))", gap: 10, marginBottom: 16 }}>
        {[
          ["Bruto", detail.gross], ["CCSS trabajador", detail.ccss_worker],
          ["Impuesto salario", detail.income_tax], ["Otras deduc.", detail.other_deductions],
          ["Neto", detail.net], ["CCSS patronal", detail.ccss_employer],
          ["Provisiones", detail.provisions], ["Costo patronal", detail.employer_cost],
        ].map(([label, val]) => (
          <div key={label} style={{ background: "var(--bg-2)", borderRadius: 10, padding: "10px 14px" }}>
            <div className="meta">{label}</div>
            <div style={{ fontWeight: 700 }}>{fmtMoney(val)}</div>
          </div>
        ))}
      </div>

      {/* líneas */}
      <div style={{ overflowX: "auto" }}>
        <table className="table">
          <thead>
            <tr>
              <th>Colaborador</th>
              <th className="num">Base</th>
              <th className="num">HE</th>
              <th className="num">Bonif.</th>
              <th className="num">Otras ded.</th>
              <th className="num">Bruto</th>
              <th className="num">CCSS</th>
              <th className="num">Imp.</th>
              <th className="num">Neto</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {lines.map((l) => (
              <tr key={l.id}>
                <td style={{ fontWeight: 600 }}>{l.employee_name}</td>
                <td className="num money">{fmtMoney(l.base)}</td>
                <td className="num">
                  {canEdit
                    ? <input className="input input--mono" style={{ width: 90, height: 30, textAlign: "right" }} value={l._overtime}
                        onChange={(e) => setLines(lines.map((x) => x.id === l.id ? { ...x, _overtime: e.target.value } : x))}
                        onBlur={() => saveLineBlur(l)} />
                    : <span className="money">{fmtMoney(l.overtime)}</span>}
                </td>
                <td className="num">
                  {canEdit
                    ? <input className="input input--mono" style={{ width: 90, height: 30, textAlign: "right" }} value={l._bonus}
                        onChange={(e) => setLines(lines.map((x) => x.id === l.id ? { ...x, _bonus: e.target.value } : x))}
                        onBlur={() => saveLineBlur(l)} />
                    : <span className="money">{fmtMoney(l.bonus)}</span>}
                </td>
                <td className="num">
                  {canEdit
                    ? <input className="input input--mono" style={{ width: 90, height: 30, textAlign: "right" }} value={l._other}
                        onChange={(e) => setLines(lines.map((x) => x.id === l.id ? { ...x, _other: e.target.value } : x))}
                        onBlur={() => saveLineBlur(l)} />
                    : <span className="money">{fmtMoney(l.other_deductions)}</span>}
                </td>
                <td className="num money" style={{ fontWeight: 700 }}>{fmtMoney(l.gross)}</td>
                <td className="num money">{fmtMoney(l.ccss_worker)}</td>
                <td className="num money">{fmtMoney(l.income_tax)}</td>
                <td className="num money" style={{ fontWeight: 700 }}>{fmtMoney(l.net)}</td>
                <td className="num">
                  <AuthLink path={`/payroll/runs/${detail.id}/slip/${l.id}`} className="btn btn--ghost btn--sm">Colilla</AuthLink>
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr style={{ fontWeight: 700, borderTop: "2px solid var(--hair)" }}>
              <td>Total</td>
              <td />
              <td /><td /><td />
              <td className="num money">{fmtMoney(detail.gross)}</td>
              <td className="num money">{fmtMoney(detail.ccss_worker)}</td>
              <td className="num money">{fmtMoney(detail.income_tax)}</td>
              <td className="num money">{fmtMoney(detail.net)}</td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
    </Modal>
  );
}

/* ========================= TAB: Colaboradores ========================= */
function TabEmployees() {
  const { toast } = useSession();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [edit, setEdit] = useState<(Omit<Employee, "id"> & { id?: number }) | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => api<Employee[]>("/payroll/employees").then(setEmployees).catch((e) => toast(e.message, "bad"));
  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!edit) return;
    setBusy(true);
    try {
      const { id, ...body } = edit;
      await api(id ? `/payroll/employees/${id}` : "/payroll/employees", {
        method: id ? "PUT" : "POST",
        json: { ...body, salary: Number(body.salary), children: Number(body.children) },
      });
      toast("Colaborador guardado"); setEdit(null); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  return (
    <>
      <Card title="Colaboradores" flush extra={
        <button className="btn btn--crimson btn--sm" onClick={() => setEdit({ ...blankEmp })}><Icon d={I.plus} />Agregar colaborador</button>
      }>
        {employees.length === 0
          ? <Empty hint="Agregá los colaboradores para poder calcular planillas." />
          : (
            <table className="table">
              <thead><tr><th>Nombre</th><th>Cédula</th><th>Puesto</th><th>Frecuencia</th><th className="num">Salario</th><th>Inicio</th><th>Estado</th><th /></tr></thead>
              <tbody>{employees.map((e) => (
                <tr key={e.id}>
                  <td style={{ fontWeight: 600 }}>{e.name}<div className="meta" style={{ textTransform: "none" }}>{e.email}</div></td>
                  <td className="mono muted">{e.id_number}</td>
                  <td className="muted">{e.position || "—"}</td>
                  <td className="muted" style={{ textTransform: "capitalize" }}>{e.frequency}</td>
                  <td className="num money">{fmtMoney(e.salary)}</td>
                  <td className="muted">{fmtDate(e.start_date)}</td>
                  <td><Badge status={e.active ? "confirmado" : "anulada"} /></td>
                  <td className="num">
                    <button className="btn btn--ghost btn--sm" onClick={() => setEdit({
                      id: e.id, name: e.name, id_number: e.id_number, email: e.email || "",
                      phone: e.phone || "", position: e.position || "", salary: String(Number(e.salary)),
                      frequency: e.frequency, start_date: e.start_date, iban: e.iban || "",
                      children: e.children, spouse_credit: e.spouse_credit, active: e.active,
                    })}>Ver</button>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          )}
      </Card>

      {edit && (
        <Modal title={edit.id ? "Editar colaborador" : "Nuevo colaborador"} onClose={() => setEdit(null)} wide foot={
          <><button className="btn btn--ghost" onClick={() => setEdit(null)}>Cancelar</button>
            <button className="btn btn--crimson" onClick={save} disabled={busy}>{busy ? <span className="spinner" /> : "Guardar"}</button></>
        }>
          <div className="grid-2">
            <Field label="Nombre completo"><input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="Número de cédula"><input className="input input--mono" style={{ textAlign: "left" }} value={edit.id_number} onChange={(e) => setEdit({ ...edit, id_number: e.target.value })} /></Field>
            <Field label="Correo electrónico"><input className="input" type="email" value={edit.email ?? ""} onChange={(e) => setEdit({ ...edit, email: e.target.value })} /></Field>
            <Field label="Teléfono"><input className="input" value={edit.phone ?? ""} onChange={(e) => setEdit({ ...edit, phone: e.target.value })} /></Field>
            <Field label="Puesto"><input className="input" value={edit.position ?? ""} onChange={(e) => setEdit({ ...edit, position: e.target.value })} /></Field>
            <Field label="Fecha de ingreso"><input className="input" type="date" value={edit.start_date} onChange={(e) => setEdit({ ...edit, start_date: e.target.value })} /></Field>
            <Field label="Salario bruto"><input className="input input--mono" value={edit.salary} onChange={(e) => setEdit({ ...edit, salary: e.target.value })} /></Field>
            <Field label="Frecuencia de pago">
              <select className="select" value={edit.frequency} onChange={(e) => setEdit({ ...edit, frequency: e.target.value as "mensual" | "quincenal" })}>
                <option value="mensual">Mensual</option>
                <option value="quincenal">Quincenal</option>
              </select>
            </Field>
            <Field label="IBAN" hint="18 dígitos del BCCR"><input className="input input--mono" style={{ textAlign: "left" }} value={edit.iban ?? ""} onChange={(e) => setEdit({ ...edit, iban: e.target.value })} /></Field>
            <Field label="Hijos con crédito">
              <input className="input input--mono" type="number" min={0} value={edit.children} onChange={(e) => setEdit({ ...edit, children: Number(e.target.value) })} />
            </Field>
          </div>
          <div style={{ display: "flex", gap: 20, fontSize: 13, marginTop: 4 }}>
            <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input type="checkbox" checked={edit.spouse_credit} onChange={(e) => setEdit({ ...edit, spouse_credit: e.target.checked })} />
              Crédito cónyuge
            </label>
            <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input type="checkbox" checked={edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} />
              Activo
            </label>
          </div>
        </Modal>
      )}
    </>
  );
}

/* ========================= TAB: Tasas ========================= */
function TabRates() {
  const { toast } = useSession();
  const [settings, setSettings] = useState<PayrollSettings | null>(null);
  const [form, setForm] = useState<Omit<PayrollSettings, "defaults"> | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = () => api<PayrollSettings>("/payroll/settings").then((s) => { setSettings(s); setForm({ ccss_worker: s.ccss_worker, ccss_employer: s.ccss_employer, brackets: s.brackets, child_credit: s.child_credit, spouse_credit: s.spouse_credit, aguinaldo: s.aguinaldo, vacaciones: s.vacaciones, year: s.year }); }).catch((e) => toast(e.message, "bad"));
  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!form) return;
    setBusy(true);
    try {
      await api("/payroll/settings", { method: "PUT", json: form });
      toast("Tasas guardadas"); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  const restoreDefaults = () => {
    if (!settings) return;
    const d = settings.defaults;
    setForm({ ccss_worker: d.ccss_worker, ccss_employer: d.ccss_employer, brackets: d.brackets, child_credit: d.child_credit, spouse_credit: d.spouse_credit, aguinaldo: d.aguinaldo, vacaciones: d.vacaciones, year: d.year });
  };

  const setBracket = (i: number, field: "limit" | "pct", val: string) => {
    if (!form) return;
    const b: Bracket[] = form.brackets.map((br, k) => k === i ? (field === "limit" ? [val === "" ? null : Number(val), br[1]] : [br[0], Number(val)]) : br);
    setForm({ ...form, brackets: b });
  };

  const addBracket = () => { if (!form) return; setForm({ ...form, brackets: [...form.brackets, [null, 0]] }); };
  const removeBracket = (i: number) => { if (!form) return; setForm({ ...form, brackets: form.brackets.filter((_, k) => k !== i) }); };

  if (!form) return <span className="spinner" />;

  return (
    <div className="grid-2">
      <Card title="Cargas sociales y provisiones">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ padding: "10px 14px", background: "var(--warn-soft)", borderRadius: 10, fontSize: 13, color: "var(--text-2)", lineHeight: 1.5 }}>
            <b>Verificá los tramos del impuesto al salario y las cargas de la CCSS cada enero</b> (decreto de Hacienda y CCSS). Valores por defecto: base {settings?.defaults.year ?? "2025"}.
          </div>
          <div className="grid-2">
            <Field label="CCSS trabajador %" hint="Ej. 10.67"><input className="input input--mono" type="number" step="0.01" value={form.ccss_worker} onChange={(e) => setForm({ ...form, ccss_worker: Number(e.target.value) })} /></Field>
            <Field label="CCSS patronal %" hint="Ej. 26.33"><input className="input input--mono" type="number" step="0.01" value={form.ccss_employer} onChange={(e) => setForm({ ...form, ccss_employer: Number(e.target.value) })} /></Field>
            <Field label="Crédito por hijo (₡)" ><input className="input input--mono" type="number" value={form.child_credit} onChange={(e) => setForm({ ...form, child_credit: Number(e.target.value) })} /></Field>
            <Field label="Crédito cónyuge (₡)"><input className="input input--mono" type="number" value={form.spouse_credit} onChange={(e) => setForm({ ...form, spouse_credit: Number(e.target.value) })} /></Field>
            <Field label="Aguinaldo %" hint="1/12 = 8.33"><input className="input input--mono" type="number" step="0.01" value={form.aguinaldo} onChange={(e) => setForm({ ...form, aguinaldo: Number(e.target.value) })} /></Field>
            <Field label="Vacaciones %" hint="2 semanas = 3.85"><input className="input input--mono" type="number" step="0.01" value={form.vacaciones} onChange={(e) => setForm({ ...form, vacaciones: Number(e.target.value) })} /></Field>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--ghost btn--sm" onClick={restoreDefaults}>Restaurar valores por defecto</button>
          </div>
        </div>
      </Card>

      <Card title="Tramos impuesto al salario" extra={<button className="btn btn--ghost btn--sm" onClick={addBracket}><Icon d={I.plus} />Agregar tramo</button>}>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
          {form.brackets.map((br, i) => (
            <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8, alignItems: "center" }}>
              <Field label={i === 0 ? "Hasta (₡)" : "Hasta (₡ o vacío=sin límite)"}>
                <input className="input input--mono" type="number" placeholder="Sin límite" value={br[0] ?? ""} onChange={(e) => setBracket(i, "limit", e.target.value)} />
              </Field>
              <Field label="Tasa %">
                <input className="input input--mono" type="number" step="0.01" value={br[1]} onChange={(e) => setBracket(i, "pct", e.target.value)} />
              </Field>
              <button className="x" style={{ marginTop: 18 }} onClick={() => removeBracket(i)}><Icon d={I.x} size={14} /></button>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn--crimson" onClick={save} disabled={busy}>{busy ? <span className="spinner" /> : "Guardar tasas"}</button>
        </div>
      </Card>
      <input ref={fileRef} type="file" hidden />
    </div>
  );
}
