/* Bancos: tarjetas por cuenta con el saldo segun el extracto, editar cuenta (apertura), chequeras y tarjetas de debito
   (SOLO los ultimos 4 digitos) y el boton para conciliar. Contabilidad administra todo esto sin entrar a Ajustes. */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Empty, Field, I, Icon, Loading, Modal } from "../../../ui/components";

type Instrument = { id: number; bank_account_id: number; kind: "chequera" | "tarjeta_debito"; name: string; last4: string | null; holder: string | null; check_from: number | null; check_to: number | null; next_check: number | null; active: boolean };
type Account = { id: number; name: string; bank: string | null; currency: string; number: string | null; active: boolean; pending: number; reconciled: number; opening_balance: string; opening_date: string | null; balance: string; balance_date: string | null; instruments: Instrument[] };
type AccEdit = { id?: number; name: string; bank: string; currency: string; number: string; active: boolean; opening_balance: string; opening_date: string };
type InsEdit = { id?: number; account: number; kind: "chequera" | "tarjeta_debito"; name: string; last4: string; holder: string; check_from: string; check_to: string; next_check: string; active: boolean };
const mask = (n: string | null) => (!n ? "—" : n.length <= 4 ? n : `••••${n.slice(-4)}`);

export default function Banks() {
  const { toast, allows } = useSession();
  const nav = useNavigate();
  const [rows, setRows] = useState<Account[] | null>(null);
  const [acc, setAcc] = useState<AccEdit | null>(null);
  const [ins, setIns] = useState<InsEdit | null>(null);
  const canEdit = allows("accounting.editar");
  const load = () => api<Account[]>("/banking/accounts?all=1").then(setRows).catch((e) => toast(e.message, "bad"));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const saveAcc = async () => {
    if (!acc) return;
    const { id, ...b } = acc;
    try { await api(id ? `/banking/accounts/${id}` : "/banking/accounts", { method: id ? "PUT" : "POST", json: { ...b, bank: b.bank || null, number: b.number || null, opening_balance: Number(b.opening_balance || 0), opening_date: b.opening_date || null } }); toast("Cuenta guardada"); setAcc(null); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const saveIns = async () => {
    if (!ins) return;
    const { id, account, ...b } = ins;
    const body = { ...b, last4: b.kind === "tarjeta_debito" ? b.last4 : null, holder: b.holder || null, check_from: b.check_from ? Number(b.check_from) : null, check_to: b.check_to ? Number(b.check_to) : null, next_check: b.next_check ? Number(b.next_check) : null };
    try { await api(id ? `/banking/instruments/${id}` : `/banking/accounts/${account}/instruments`, { method: id ? "PUT" : "POST", json: body }); toast("Guardado"); setIns(null); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  return (
    <>
      {canEdit && <div style={{ display: "flex", justifyContent: "flex-end" }}><button className="btn btn--crimson btn--sm" onClick={() => setAcc({ name: "", bank: "", currency: "CRC", number: "", active: true, opening_balance: "0", opening_date: "" })}><Icon d={I.plus} />Cuenta</button></div>}
      {rows === null ? <Loading /> : rows.length === 0 ? <Empty title="Sin cuentas" hint="Agregá las cuentas en colones y en dólares; el banco no se conecta: el saldo sale del extracto que se importa al conciliar." /> : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(300px,1fr))", gap: 14 }}>
          {rows.map((a) => (
            <div key={a.id} className="card" style={{ opacity: a.active ? 1 : 0.6 }}><div className="card__body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}><b>{a.name}</b><span className="badge badge--muted">{a.currency}</span></div>
              <div className="muted" style={{ fontSize: 13 }}>{a.bank || "—"} · <span className="mono">{mask(a.number)}</span></div>
              <div><div className="meta">Saldo según extracto{a.balance_date ? ` al ${fmtDate(a.balance_date)}` : ""}</div><div style={{ fontWeight: 700, fontSize: 20 }}>{fmtMoney(a.balance, a.currency)}</div></div>
              <div className="muted" style={{ fontSize: 12 }}>{a.pending} pendientes · {a.reconciled} conciliados{a.opening_date ? ` · apertura ${fmtMoney(a.opening_balance, a.currency)} el ${fmtDate(a.opening_date)}` : ""}</div>
              {a.instruments.length > 0 && (
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                  {a.instruments.map((i) => <li key={i.id}>{i.kind === "chequera" ? `Chequera ${i.name} · ${i.check_from}–${i.check_to} · siguiente ${i.next_check ?? "—"}` : `Tarjeta ${i.name} ····${i.last4}${i.holder ? ` · ${i.holder}` : ""}`}{!i.active && " (inactiva)"} {canEdit && <button className="btn btn--ghost btn--sm" onClick={() => setIns({ id: i.id, account: a.id, kind: i.kind, name: i.name, last4: i.last4 || "", holder: i.holder || "", check_from: String(i.check_from ?? ""), check_to: String(i.check_to ?? ""), next_check: String(i.next_check ?? ""), active: i.active })}>Editar</button>}</li>)}
                </ul>
              )}
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
                <button className="btn btn--crimson btn--sm" onClick={() => nav(`/contabilidad/contable/bancos/${a.id}/conciliacion`)}>Conciliar</button>
                {canEdit && <button className="btn btn--ghost btn--sm" onClick={() => setAcc({ id: a.id, name: a.name, bank: a.bank || "", currency: a.currency, number: a.number || "", active: a.active, opening_balance: String(Number(a.opening_balance)), opening_date: a.opening_date || "" })}>Editar cuenta</button>}
                {canEdit && <button className="btn btn--ghost btn--sm" onClick={() => setIns({ account: a.id, kind: "chequera", name: "", last4: "", holder: "", check_from: "", check_to: "", next_check: "", active: true })}>Chequeras y tarjetas</button>}
              </div>
            </div></div>
          ))}
        </div>
      )}
      {acc && (
        <Modal title={acc.id ? "Cuenta bancaria" : "Nueva cuenta"} onClose={() => setAcc(null)} foot={<><button className="btn btn--ghost" onClick={() => setAcc(null)}>Cancelar</button><button className="btn btn--crimson" onClick={saveAcc}>Guardar</button></>}>
          <div className="grid-2">
            <Field label="Nombre"><input className="input" value={acc.name} onChange={(e) => setAcc({ ...acc, name: e.target.value })} /></Field>
            <Field label="Banco"><input className="input" value={acc.bank} onChange={(e) => setAcc({ ...acc, bank: e.target.value })} /></Field>
            <Field label="Divisa"><select className="select" value={acc.currency} onChange={(e) => setAcc({ ...acc, currency: e.target.value })}><option>CRC</option><option>USD</option></select></Field>
            <Field label="Número / IBAN"><input className="input input--mono" style={{ textAlign: "left" }} value={acc.number} onChange={(e) => setAcc({ ...acc, number: e.target.value })} /></Field>
            <Field label="Saldo de apertura"><input className="input input--mono" inputMode="decimal" value={acc.opening_balance} onChange={(e) => setAcc({ ...acc, opening_balance: e.target.value })} /></Field>
            <Field label="Fecha de apertura" hint="Los movimientos anteriores no suman al saldo."><input className="input" type="date" value={acc.opening_date} onChange={(e) => setAcc({ ...acc, opening_date: e.target.value })} /></Field>
          </div>
          <label className="acc-check"><input type="checkbox" checked={acc.active} onChange={(e) => setAcc({ ...acc, active: e.target.checked })} />Activa</label>
        </Modal>
      )}
      {ins && (
        <Modal title={ins.id ? "Chequera o tarjeta" : "Agregar chequera o tarjeta"} onClose={() => setIns(null)} foot={<><button className="btn btn--ghost" onClick={() => setIns(null)}>Cancelar</button><button className="btn btn--crimson" onClick={saveIns}>Guardar</button></>}>
          <div className="grid-2">
            <Field label="Tipo"><select className="select" value={ins.kind} disabled={!!ins.id} onChange={(e) => setIns({ ...ins, kind: e.target.value as InsEdit["kind"] })}><option value="chequera">Chequera</option><option value="tarjeta_debito">Tarjeta de débito</option></select></Field>
            <Field label="Nombre"><input className="input" value={ins.name} onChange={(e) => setIns({ ...ins, name: e.target.value })} placeholder={ins.kind === "chequera" ? "Chequera 1" : "Débito gerencia"} /></Field>
            {ins.kind === "tarjeta_debito" ? <>
              <Field label="Últimos 4 dígitos" hint="Nunca el número completo."><input className="input input--mono" inputMode="numeric" maxLength={4} value={ins.last4} onChange={(e) => setIns({ ...ins, last4: e.target.value.replace(/\D/g, "").slice(0, 4) })} /></Field>
              <Field label="Titular"><input className="input" value={ins.holder} onChange={(e) => setIns({ ...ins, holder: e.target.value })} /></Field>
            </> : <>
              <Field label="Desde el cheque"><input className="input input--mono" inputMode="numeric" value={ins.check_from} onChange={(e) => setIns({ ...ins, check_from: e.target.value })} /></Field>
              <Field label="Hasta el cheque"><input className="input input--mono" inputMode="numeric" value={ins.check_to} onChange={(e) => setIns({ ...ins, check_to: e.target.value })} /></Field>
              <Field label="Siguiente número" hint="Vacío = el primero del rango."><input className="input input--mono" inputMode="numeric" value={ins.next_check} onChange={(e) => setIns({ ...ins, next_check: e.target.value })} /></Field>
            </>}
          </div>
          <label className="acc-check"><input type="checkbox" checked={ins.active} onChange={(e) => setIns({ ...ins, active: e.target.checked })} />Activa</label>
        </Modal>
      )}
    </>
  );
}
