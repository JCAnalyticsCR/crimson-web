/* «Marcar pagada» y «Desmarcar pago» (reunión con la contadora 10/11): fecha real del pago, medio, cuenta o tarjeta de
   la que salió y número de referencia, que luego tiene que coincidir con el estado de cuenta del banco. Lo usan Gastos
   y Cuentas por pagar. Una o varias facturas de la misma moneda con el mismo pago; todo o nada. */
import { useState } from "react";
import { api, fmtDate, fmtMoney } from "../../../lib/api";
import { hoyCR } from "../../../lib/fechaCR";
import { Field, Modal } from "../../../ui/components";

export type PayInstrument = { id: number; kind: "chequera" | "tarjeta_debito"; name: string; last4: string | null; next_check: number | null; active: boolean };
export type PayAccount = { id: number; name: string; currency: string; active?: boolean; instruments?: PayInstrument[] };
export type PayItem = { id: number; description: string; supplier_name?: string | null; date: string; currency: string; amount: number | string };

export const PAY_METHODS: [string, string][] = [["transferencia", "Transferencia"], ["sinpe", "SINPE"], ["efectivo", "Efectivo"], ["tarjeta", "Tarjeta"], ["cheque", "Cheque"]];
export const PAY_STATE: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "info" | "muted" }> = {
  pendiente: { label: "Pendiente de pago", tone: "warn" },
  pagado: { label: "Pagada", tone: "ok" },
  anterior: { label: "Antes del corte", tone: "muted" },
  anulado: { label: "Anulado", tone: "muted" },
};

type Toast = (msg: string, tone?: "ok" | "bad") => void;

export function MarkPaidModal({ items, accounts, onClose, onDone, toast }: { items: PayItem[]; accounts: PayAccount[]; onClose: () => void; onDone: () => void; toast: Toast }) {
  const currencies = [...new Set(items.map((i) => i.currency))];
  const currency = currencies[0] || "CRC";
  const [f, setF] = useState({ paid_date: hoyCR(), method: "transferencia", bank_account_id: "", bank_instrument_id: "", check_number: "", reference: "", note: "", before_invoice_ok: false });
  const [saving, setSaving] = useState(false);
  const cuentas = accounts.filter((a) => a.active !== false && a.currency === currency);
  const acct = cuentas.find((a) => String(a.id) === f.bank_account_id);
  const kind = f.method === "cheque" ? "chequera" : f.method === "tarjeta" ? "tarjeta_debito" : null;
  const instrumentos = (acct?.instruments || []).filter((i) => i.active && i.kind === kind);
  const ins = instrumentos.find((i) => String(i.id) === f.bank_instrument_id);
  const total = items.reduce((s, i) => s + Number(i.amount || 0), 0);
  const antes = items.filter((i) => f.paid_date && f.paid_date < i.date);
  const pideRef = f.method === "transferencia" || f.method === "sinpe";
  const faltan = [
    currencies.length > 1 && "facturas de una sola moneda",
    !f.paid_date && "la fecha de pago",
    f.paid_date > hoyCR() && "una fecha que no sea futura",
    f.method !== "efectivo" && !f.bank_account_id && "la cuenta",
    pideRef && !f.reference.trim() && "la referencia",
    f.method === "cheque" && !f.check_number.trim() && !f.reference.trim() && "el número de cheque",
    antes.length > 0 && !f.before_invoice_ok && "confirmar el adelanto",
  ].filter(Boolean) as string[];

  const save = async () => {
    if (faltan.length) { toast(`Falta: ${faltan.join(", ")}`, "bad"); return; }
    setSaving(true);
    try {
      const r = await api<{ paid: unknown[]; warnings: string[] }>("/cxp/mark-paid", {
        method: "POST",
        json: {
          expense_ids: items.map((i) => i.id), paid_date: f.paid_date, method: f.method,
          bank_account_id: f.bank_account_id ? Number(f.bank_account_id) : null, bank_instrument_id: f.bank_instrument_id ? Number(f.bank_instrument_id) : null,
          check_number: f.method === "cheque" ? f.check_number.trim() || null : null, reference: f.reference.trim() || null, note: f.note.trim() || null,
          before_invoice_ok: f.before_invoice_ok,
        },
      });
      toast(r.warnings?.length ? r.warnings[0] : r.paid.length === 1 ? "Factura marcada como pagada" : `${r.paid.length} facturas marcadas como pagadas`);
      onDone(); onClose();
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo marcar", "bad"); }
    finally { setSaving(false); }
  };

  return (
    <Modal title={items.length === 1 ? "Marcar pagada" : `Marcar pagadas (${items.length})`} onClose={onClose}
      foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" disabled={saving || currencies.length > 1} onClick={save}>{saving ? "Guardando…" : "Marcar pagada"}</button></>}>
      <div style={{ display: "grid", gap: 14 }}>
        <div style={{ fontSize: 13, display: "grid", gap: 4 }}>
          {items.slice(0, 6).map((i) => (
            <div key={i.id} style={{ display: "flex", gap: 8, justifyContent: "space-between", flexWrap: "wrap" }}>
              <span style={{ minWidth: 0 }}><b>{i.supplier_name || "Sin proveedor"}</b> · {i.description} <span className="muted">({fmtDate(i.date)})</span></span>
              <span className="money">{fmtMoney(i.amount, i.currency)}</span>
            </div>
          ))}
          {items.length > 6 && <div className="muted">y {items.length - 6} más…</div>}
          {items.length > 1 && <div style={{ display: "flex", justifyContent: "space-between", borderTop: "1px solid var(--hair)", paddingTop: 6 }}><b>Total</b><b className="money">{fmtMoney(total, currency)}</b></div>}
        </div>
        {currencies.length > 1 && <div className="acc-warn">Elegiste facturas en {currencies.join(" y ")}: marcalas por moneda, cada pago sale de una cuenta en la moneda de la factura.</div>}
        <div className="grid-2">
          <Field label="Fecha de pago" hint="La del estado de cuenta del banco."><input className="input" type="date" max={hoyCR()} value={f.paid_date} onChange={(e) => setF({ ...f, paid_date: e.target.value, before_invoice_ok: false })} /></Field>
          <Field label="Medio"><select className="select" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value, bank_instrument_id: "" })}>{PAY_METHODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
          <Field label={f.method === "efectivo" ? "Cuenta (opcional)" : "Cuenta de la que salió"} hint={cuentas.length ? undefined : `No hay cuentas activas en ${currency}: agregala en Bancos.`}>
            <select className="select" value={f.bank_account_id} onChange={(e) => setF({ ...f, bank_account_id: e.target.value, bank_instrument_id: "" })}>
              <option value="">{f.method === "efectivo" ? "Caja (efectivo)" : "—"}</option>
              {cuentas.map((a) => <option key={a.id} value={a.id}>{a.name} · {a.currency}</option>)}
            </select>
          </Field>
          {kind && instrumentos.length > 0 && (
            <Field label={kind === "chequera" ? "Chequera" : "Tarjeta"}>
              <select className="select" value={f.bank_instrument_id} onChange={(e) => setF({ ...f, bank_instrument_id: e.target.value })}>
                <option value="">—</option>
                {instrumentos.map((i) => <option key={i.id} value={i.id}>{i.kind === "chequera" ? `Chequera ${i.name}` : `Tarjeta ${i.name} ····${i.last4 || ""}`}</option>)}
              </select>
            </Field>
          )}
          {f.method === "cheque" && <Field label="Número de cheque" hint={ins?.next_check ? `Siguiente: ${ins.next_check}` : undefined}><input className="input input--mono" value={f.check_number} onChange={(e) => setF({ ...f, check_number: e.target.value })} /></Field>}
          <Field label={pideRef ? "Número de referencia" : "Referencia (opcional)"} hint="Tal como sale en el estado de cuenta."><input className="input input--mono" value={f.reference} placeholder={f.method === "sinpe" ? "Comprobante SINPE" : f.method === "tarjeta" ? "Voucher o autorización" : "Número de transferencia"} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
        </div>
        {antes.length > 0 && (
          <label className="acc-warn" style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
            <input type="checkbox" checked={f.before_invoice_ok} onChange={(e) => setF({ ...f, before_invoice_ok: e.target.checked })} />
            <span>La fecha de pago es anterior a la factura{antes.length === 1 ? ` (${fmtDate(antes[0].date)})` : "s elegidas"}. Marcá si fue un adelanto para registrarla igual.</span>
          </label>
        )}
        <Field label="Nota (opcional)"><textarea className="textarea" rows={2} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
      </div>
    </Modal>
  );
}

export function UnmarkModal({ expenseId, label, onClose, onDone, toast }: { expenseId: number; label: string; onClose: () => void; onDone: () => void; toast: Toast }) {
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const save = async () => {
    if (reason.trim().length < 3) { toast("Escribí el motivo (queda en la bitácora)", "bad"); return; }
    setSaving(true);
    try { await api(`/cxp/expenses/${expenseId}/unmark`, { method: "POST", json: { reason: reason.trim() } }); toast("Pago desmarcado: la factura vuelve a pendiente"); onDone(); onClose(); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo desmarcar", "bad"); }
    finally { setSaving(false); }
  };
  return (
    <Modal title="Desmarcar pago" onClose={onClose}
      foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--danger" disabled={saving} onClick={save}>{saving ? "Desmarcando…" : "Desmarcar"}</button></>}>
      <p className="muted" style={{ marginTop: 0 }}>{label}: el pago se anula y la factura vuelve a pendiente. Si el mes ya se exportó, el asiento sale como ajuste.</p>
      <Field label="Motivo"><textarea className="textarea" rows={3} autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Ej.: se marcó la factura equivocada" /></Field>
    </Modal>
  );
}
