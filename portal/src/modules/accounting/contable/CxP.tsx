/* Cuentas por pagar: resumen ejecutivo, antigüedad por proveedor, estado de cuenta y registro de pagos.
   Tablet 768 px: la tabla de antigüedad usa table--stack para pasar a tarjetas. */
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtMoney, STATUS } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Card, Empty, Field, I, Icon, Loading, Modal } from "../../../ui/components";

/* mini-badge sin usar el componente Badge (que no acepta children) */
function Chip({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, tone: "muted" as const };
  return <span className={`badge badge--${s.tone}`}>{s.label}</span>;
}

/* ---------- tipos ---------- */
type Sup = { id: number; name: string; tax_id: string | null };
type Account = { id: number; name: string; currency: string };
type AgingRow = {
  supplier_id: number | null; supplier_name: string; currency: string;
  corriente: string; d1_30: string; d31_60: string; d61_90: string; d90_mas: string;
  vencido: string; total: string;
};
type AgingRes = { rows: AgingRow[]; totals: { total: number; vencido: number; corriente: number; d1_30: number; d31_60: number; d61_90: number; d90_mas: number } };
type Summary = { total_crc: number; vencido_crc: number; semana_crc: number; count: number };
type OpenExp = { expense_id: number; date: string; due_date: string; description: string; currency: string; total: number; balance: number };
type StatExp = OpenExp & { applied: number; status: string; overdue: boolean };
type StatPay = { id: number; date: string; amount: number; currency: string; method: string; status: string; reference: string | null };
type Statement = {
  supplier: Sup;
  expenses: StatExp[];
  payments: StatPay[];
  total_balance: number;
  total_vencido: number;
};
type AppLine = { expense_id: number; description: string; currency: string; balance: number; amount: string };

const METHOD_LABELS: Record<string, string> = {
  transferencia: "Transferencia", sinpe: "SINPE", cheque: "Cheque",
  tarjeta: "Tarjeta", efectivo: "Efectivo",
};
const fmt = (n: number | string, cur = "CRC") => fmtMoney(Number(n), cur);
const fmtCRC = (n: number) => `₡${n.toLocaleString("es-CR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/* ---------- PaymentModal ---------- */
type PayModal = {
  supplier_id: number;
  supplier_name: string;
  open_expenses: OpenExp[];
  accounts: Account[];
  sups: Sup[];
  onClose: () => void;
  onSaved: () => void;
  toast: (msg: string, tone?: "ok" | "bad") => void;
};
function PaymentModal({ supplier_id, supplier_name, open_expenses, accounts, onClose, onSaved, toast }: PayModal) {
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [method, setMethod] = useState("transferencia");
  const [bankId, setBankId] = useState<string>("");
  const [ref, setRef] = useState("");
  const [note, setNote] = useState("");
  const [apps, setApps] = useState<AppLine[]>(
    open_expenses.map((e) => ({ expense_id: e.expense_id, description: e.description, currency: e.currency, balance: e.balance, amount: "" }))
  );
  const [saving, setSaving] = useState(false);

  const totalAmt = apps.reduce((s, a) => s + (parseFloat(a.amount) || 0), 0);

  const save = async () => {
    const validApps = apps.filter((a) => parseFloat(a.amount) > 0);
    if (!validApps.length) { toast("Indique al menos un monto a aplicar", "bad"); return; }
    setSaving(true);
    try {
      await api("/cxp/payments", {
        method: "POST",
        json: {
          supplier_id,
          date,
          amount: totalAmt,
          currency: validApps[0].currency,
          method,
          bank_account_id: bankId ? Number(bankId) : null,
          reference: ref || null,
          note: note || null,
          applications: validApps.map((a) => ({ expense_id: a.expense_id, amount: parseFloat(a.amount) })),
        },
      });
      toast("Pago registrado");
      onSaved();
      onClose();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Error al guardar", "bad");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={`Registrar pago — ${supplier_name}`} onClose={onClose}
      foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" onClick={save} disabled={saving}>{saving ? "Guardando…" : "Registrar pago"}</button></>}>
      <div style={{ display: "grid", gap: 16 }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <Field label="Fecha"><input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
          <Field label="Método">
            <select className="input" value={method} onChange={(e) => setMethod(e.target.value)}>
              {Object.entries(METHOD_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </Field>
          <Field label="Cuenta bancaria (opcional)">
            <select className="input" value={bankId} onChange={(e) => setBankId(e.target.value)}>
              <option value="">—</option>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.currency})</option>)}
            </select>
          </Field>
          <Field label="Referencia"><input className="input input--mono" value={ref} onChange={(e) => setRef(e.target.value)} placeholder="Nro. de transferencia, cheque…" /></Field>
        </div>
        <Field label="Facturas abiertas — ingrese el monto a aplicar en cada una">
          {open_expenses.length === 0 ? (
            <p style={{ color: "var(--text-3)", fontSize: 13 }}>Este proveedor no tiene facturas de crédito pendientes.</p>
          ) : (
            <table className="table table--sm" style={{ width: "100%" }}>
              <thead><tr><th>Descripción</th><th>Vence</th><th>Moneda</th><th style={{ textAlign: "right" }}>Saldo</th><th style={{ textAlign: "right" }}>Aplicar</th></tr></thead>
              <tbody>
                {apps.map((a, i) => {
                  const oe = open_expenses[i];
                  return (
                    <tr key={a.expense_id}>
                      <td>{a.description}</td>
                      <td style={{ whiteSpace: "nowrap" }}>{fmtDate(oe.due_date)}</td>
                      <td>{a.currency}</td>
                      <td style={{ textAlign: "right", fontFamily: "var(--mono)" }}>{fmt(a.balance, a.currency)}</td>
                      <td style={{ textAlign: "right" }}>
                        <input
                          className="input input--mono"
                          style={{ width: 110, textAlign: "right" }}
                          type="number" min="0" step="0.01"
                          max={a.balance}
                          value={a.amount}
                          onChange={(ev) => setApps(apps.map((x, j) => j === i ? { ...x, amount: ev.target.value } : x))}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={3}><strong>Total del pago</strong></td>
                  <td />
                  <td style={{ textAlign: "right", fontFamily: "var(--mono)" }}><strong>{totalAmt.toLocaleString("es-CR", { minimumFractionDigits: 2 })}</strong></td>
                </tr>
              </tfoot>
            </table>
          )}
        </Field>
        <Field label="Nota (opcional)"><textarea className="input" rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
      </div>
    </Modal>
  );
}

/* ---------- VoidModal ---------- */
function VoidModal({ payId, onClose, onDone, toast }: { payId: number; onClose: () => void; onDone: () => void; toast: (m: string, tone?: "ok" | "bad") => void }) {
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const save = async () => {
    if (!reason.trim()) { toast("Escriba el motivo de anulación", "bad"); return; }
    setSaving(true);
    try { await api(`/cxp/payments/${payId}/anular`, { method: "POST", json: { reason } }); toast("Pago anulado"); onDone(); onClose(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setSaving(false); }
  };
  return (
    <Modal title="Anular pago" onClose={onClose}
      foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--danger" onClick={save} disabled={saving}>{saving ? "Anulando…" : "Anular"}</button></>}>
      <Field label="Motivo de anulación"><textarea className="input" rows={3} autoFocus value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
    </Modal>
  );
}

/* ---------- SupplierStatement ---------- */
function SupplierStatement({ sid, accounts, sups, onBack, toast, allows }: { sid: number; accounts: Account[]; sups: Sup[]; onBack: () => void; toast: (m: string, tone?: "ok" | "bad") => void; allows: (p: string) => boolean }) {
  const [stmt, setStmt] = useState<Statement | null>(null);
  const [openExps, setOpenExps] = useState<OpenExp[]>([]);
  const [showPay, setShowPay] = useState(false);
  const [voidId, setVoidId] = useState<number | null>(null);

  const load = () => {
    api<Statement>(`/cxp/suppliers/${sid}/statement`).then(setStmt).catch(() => setStmt(null));
    api<OpenExp[]>(`/cxp/suppliers/${sid}/open-expenses`).then(setOpenExps).catch(() => setOpenExps([]));
  };
  useEffect(load, [sid]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!stmt) return <Loading />;
  const { supplier, expenses, payments, total_balance, total_vencido } = stmt;

  return (
    <div style={{ display: "grid", gap: 20 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <button className="btn btn--ghost btn--sm" onClick={onBack}><span style={{ display: "inline-flex", transform: "rotate(180deg)" }}><Icon d={I.arrow} /></span>Volver</button>
        <h2 style={{ margin: 0, fontSize: 18 }}>{supplier.name}</h2>
        {supplier.tax_id && <span style={{ color: "var(--text-3)", fontSize: 13 }}>{supplier.tax_id}</span>}
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, flexWrap: "wrap" }}>
          {allows("accounting.crear") && <button className="btn btn--crimson btn--sm" onClick={() => setShowPay(true)}><Icon d={I.plus} />Registrar pago</button>}
        </div>
      </div>

      {/* resumen */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(160px,1fr))", gap: 12 }}>
        <Card title="Saldo total"><span style={{ fontSize: 22, fontFamily: "var(--mono)" }}>{fmtCRC(total_balance)}</span></Card>
        <Card title="Vencido"><span style={{ fontSize: 22, fontFamily: "var(--mono)", color: total_vencido > 0 ? "var(--danger)" : undefined }}>{fmtCRC(total_vencido)}</span></Card>
      </div>

      {/* facturas */}
      <Card title="Facturas de crédito" flush>
        {expenses.length === 0 ? <Empty title="Sin facturas de crédito" hint="Aún no hay gastos de crédito para este proveedor." /> : (
          <div style={{ overflowX: "auto" }}>
            <table className="table table--sm table--stack">
              <thead><tr><th>Fecha</th><th>Vence</th><th>Descripción</th><th>Moneda</th><th style={{ textAlign: "right" }}>Total</th><th style={{ textAlign: "right" }}>Aplicado</th><th style={{ textAlign: "right" }}>Saldo</th><th>Estado</th></tr></thead>
              <tbody>
                {expenses.map((e) => (
                  <tr key={e.expense_id}>
                    <td data-label="Fecha">{fmtDate(e.date)}</td>
                    <td data-label="Vence" style={{ color: e.overdue ? "var(--danger)" : undefined }}>{fmtDate(e.due_date)}{e.overdue && " ⚠"}</td>
                    <td data-label="Descripción" className="stack-full">{e.description}</td>
                    <td data-label="Moneda">{e.currency}</td>
                    <td data-label="Total" style={{ textAlign: "right", fontFamily: "var(--mono)" }}>{fmt(e.total, e.currency)}</td>
                    <td data-label="Aplicado" style={{ textAlign: "right", fontFamily: "var(--mono)" }}>{fmt(e.applied, e.currency)}</td>
                    <td data-label="Saldo" style={{ textAlign: "right", fontFamily: "var(--mono)", fontWeight: e.balance > 0 ? 600 : undefined }}>{fmt(e.balance, e.currency)}</td>
                    <td data-label="Estado"><Chip status={e.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* pagos */}
      <Card title="Pagos registrados" flush>
        {payments.length === 0 ? <Empty title="Sin pagos" hint="Aún no se han registrado pagos a este proveedor." /> : (
          <div style={{ overflowX: "auto" }}>
            <table className="table table--sm table--stack">
              <thead><tr><th>Fecha</th><th>Método</th><th>Referencia</th><th>Moneda</th><th style={{ textAlign: "right" }}>Monto</th><th>Estado</th><th /></tr></thead>
              <tbody>
                {payments.map((pay) => (
                  <tr key={pay.id}>
                    <td data-label="Fecha">{fmtDate(pay.date)}</td>
                    <td data-label="Método">{METHOD_LABELS[pay.method] ?? pay.method}</td>
                    <td data-label="Referencia">{pay.reference ?? "—"}</td>
                    <td data-label="Moneda">{pay.currency}</td>
                    <td data-label="Monto" style={{ textAlign: "right", fontFamily: "var(--mono)" }}>{fmt(pay.amount, pay.currency)}</td>
                    <td data-label="Estado"><Chip status={pay.status} /></td>
                    <td data-label=" " className="stack-actions">
                      {pay.status === "registrado" && allows("accounting.editar") && (
                        <button className="btn btn--ghost btn--sm btn--danger" onClick={() => setVoidId(pay.id)}>Anular</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {showPay && (
        <PaymentModal
          supplier_id={sid}
          supplier_name={supplier.name}
          open_expenses={openExps}
          accounts={accounts}
          sups={sups}
          onClose={() => setShowPay(false)}
          onSaved={load}
          toast={toast}
        />
      )}
      {voidId !== null && (
        <VoidModal payId={voidId} onClose={() => setVoidId(null)} onDone={load} toast={toast} />
      )}
    </div>
  );
}

/* ---------- Pantalla principal ---------- */
export default function CxP() {
  const { toast, allows } = useSession();
  const [params, setParams] = useSearchParams();
  const [aging, setAging] = useState<AgingRes | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [sups, setSups] = useState<Sup[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [payModal, setPayModal] = useState<{ sid: number; name: string; openExps: OpenExp[] } | null>(null);

  const selectedSid = params.get("supplier") ? Number(params.get("supplier")) : null;

  const load = () => {
    api<AgingRes>("/cxp/aging").then(setAging).catch(() => setAging({ rows: [], totals: { total: 0, vencido: 0, corriente: 0, d1_30: 0, d31_60: 0, d61_90: 0, d90_mas: 0 } }));
    api<Summary>("/cxp/summary").then(setSummary).catch(() => setSummary(null));
    api<Sup[]>("/suppliers").then(setSups).catch(() => setSups([]));
    api<Account[]>("/banking/accounts").then(setAccounts).catch(() => setAccounts([]));
  };
  useEffect(load, []);

  const openPayModal = async (sid: number, name: string) => {
    try {
      const openExps = await api<OpenExp[]>(`/cxp/suppliers/${sid}/open-expenses`);
      setPayModal({ sid, name, openExps });
    } catch (e) {
      toast(e instanceof Error ? e.message : "Error", "bad");
    }
  };

  if (selectedSid !== null) {
    return (
      <SupplierStatement
        sid={selectedSid}
        accounts={accounts}
        sups={sups}
        onBack={() => { setParams({}); load(); }}
        toast={toast}
        allows={allows}
      />
    );
  }

  return (
    <div style={{ display: "grid", gap: 20 }}>
      {/* resumen ejecutivo */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: 12 }}>
        <Card title="Total por pagar">
          {summary ? <span style={{ fontSize: 22, fontFamily: "var(--mono)" }}>{fmtCRC(summary.total_crc)}</span> : <Loading />}
          {summary && <div style={{ fontSize: 12, color: "var(--text-3)", marginTop: 4 }}>{summary.count} factura{summary.count !== 1 ? "s" : ""} abiertas</div>}
        </Card>
        <Card title="Vencido">
          {summary ? <span style={{ fontSize: 22, fontFamily: "var(--mono)", color: (summary.vencido_crc > 0) ? "var(--danger)" : undefined }}>{fmtCRC(summary.vencido_crc)}</span> : <Loading />}
        </Card>
        <Card title="Vence esta semana">
          {summary ? <span style={{ fontSize: 22, fontFamily: "var(--mono)", color: (summary.semana_crc > 0) ? "var(--warn)" : undefined }}>{fmtCRC(summary.semana_crc)}</span> : <Loading />}
        </Card>
      </div>

      {/* antigüedad por proveedor */}
      <Card title="Antigüedad de saldos" flush extra={
        <span style={{ fontSize: 12, color: "var(--text-3)" }}>Haga clic en un proveedor para ver su estado de cuenta</span>
      }>
        {aging === null ? <Loading /> : aging.rows.length === 0 ? (
          <Empty title="Sin saldos pendientes" hint="No hay facturas de crédito abiertas con saldo." />
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="table table--sm table--stack">
              <thead>
                <tr>
                  <th>Proveedor</th>
                  <th>Moneda</th>
                  <th style={{ textAlign: "right" }}>Corriente</th>
                  <th style={{ textAlign: "right" }}>1-30 d</th>
                  <th style={{ textAlign: "right" }}>31-60 d</th>
                  <th style={{ textAlign: "right" }}>61-90 d</th>
                  <th style={{ textAlign: "right" }}>&gt;90 d</th>
                  <th style={{ textAlign: "right" }}>Vencido</th>
                  <th style={{ textAlign: "right" }}>Total</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {aging.rows.map((r, i) => (
                  <tr key={i} style={{ cursor: r.supplier_id ? "pointer" : undefined }}
                    onClick={() => r.supplier_id && setParams({ supplier: String(r.supplier_id) })}>
                    <td data-label="Proveedor">{r.supplier_name}</td>
                    <td data-label="Moneda">{r.currency}</td>
                    <td data-label="Corriente" style={{ textAlign: "right", fontFamily: "var(--mono)" }}>{fmt(r.corriente, r.currency)}</td>
                    <td data-label="1-30 d" style={{ textAlign: "right", fontFamily: "var(--mono)", color: Number(r.d1_30) > 0 ? "var(--warn)" : undefined }}>{fmt(r.d1_30, r.currency)}</td>
                    <td data-label="31-60 d" style={{ textAlign: "right", fontFamily: "var(--mono)", color: Number(r.d31_60) > 0 ? "var(--warn)" : undefined }}>{fmt(r.d31_60, r.currency)}</td>
                    <td data-label="61-90 d" style={{ textAlign: "right", fontFamily: "var(--mono)", color: Number(r.d61_90) > 0 ? "var(--danger)" : undefined }}>{fmt(r.d61_90, r.currency)}</td>
                    <td data-label=">90 d" style={{ textAlign: "right", fontFamily: "var(--mono)", color: Number(r.d90_mas) > 0 ? "var(--danger)" : undefined }}>{fmt(r.d90_mas, r.currency)}</td>
                    <td data-label="Vencido" style={{ textAlign: "right", fontFamily: "var(--mono)", color: Number(r.vencido) > 0 ? "var(--danger)" : undefined }}><strong>{fmt(r.vencido, r.currency)}</strong></td>
                    <td data-label="Total" style={{ textAlign: "right", fontFamily: "var(--mono)" }}><strong>{fmt(r.total, r.currency)}</strong></td>
                    <td className="stack-actions">
                      {r.supplier_id && allows("accounting.crear") && (
                        <button className="btn btn--ghost btn--sm" onClick={(ev) => { ev.stopPropagation(); openPayModal(r.supplier_id!, r.supplier_name); }}>
                          <Icon d={I.plus} />Pagar
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
              {aging.rows.length > 1 && (
                <tfoot>
                  <tr>
                    <td colSpan={2}><strong>Totales</strong></td>
                    <td style={{ textAlign: "right", fontFamily: "var(--mono)" }}><strong>{fmtCRC(aging.totals.corriente)}</strong></td>
                    <td style={{ textAlign: "right", fontFamily: "var(--mono)" }}><strong>{fmtCRC(aging.totals.d1_30)}</strong></td>
                    <td style={{ textAlign: "right", fontFamily: "var(--mono)" }}><strong>{fmtCRC(aging.totals.d31_60)}</strong></td>
                    <td style={{ textAlign: "right", fontFamily: "var(--mono)" }}><strong>{fmtCRC(aging.totals.d61_90)}</strong></td>
                    <td style={{ textAlign: "right", fontFamily: "var(--mono)" }}><strong>{fmtCRC(aging.totals.d90_mas)}</strong></td>
                    <td style={{ textAlign: "right", fontFamily: "var(--mono)" }}><strong>{fmtCRC(aging.totals.vencido)}</strong></td>
                    <td style={{ textAlign: "right", fontFamily: "var(--mono)" }}><strong>{fmtCRC(aging.totals.total)}</strong></td>
                    <td />
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        )}
      </Card>

      {payModal && (
        <PaymentModal
          supplier_id={payModal.sid}
          supplier_name={payModal.name}
          open_expenses={payModal.openExps}
          accounts={accounts}
          sups={sups}
          onClose={() => setPayModal(null)}
          onSaved={load}
          toast={toast}
        />
      )}
    </div>
  );
}
