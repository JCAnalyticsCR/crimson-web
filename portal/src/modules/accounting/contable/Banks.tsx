/* Bancos (reunion con la contadora, 10/11/2026): cada cuenta con su saldo segun Crimson (saldo inicial a la fecha de
   corte + cobros, gastos pagados, pagos a proveedor, planilla, comisiones y movimientos) y su saldo segun el banco
   (estado de cuenta importado). Abrir una cuenta muestra el libro, la conciliacion y los archivos importados
   (?cuenta=ID&vista=...). Chequeras y tarjetas de debito: SOLO los ultimos 4 digitos. */
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Empty, Field, I, Icon, Loading, Modal } from "../../../ui/components";
import BankBook from "./BankBook";
import { DEFAULT_CUTOFF, errMsg, num, type BookAccount } from "./bankTypes";

type Instrument = { id: number; bank_account_id: number; kind: "chequera" | "tarjeta_debito"; name: string; last4: string | null; holder: string | null; check_from: number | null; check_to: number | null; next_check: number | null; active: boolean };
type LegacyAccount = { id: number; instruments: Instrument[] };
type AccEdit = { id?: number; name: string; bank: string; currency: string; number: string; active: boolean; opening_balance: string; opening_date: string };
type OpenEdit = { id: number; name: string; currency: string; opening_balance: string; opening_date: string; kind: "banco" | "caja" };
type InsEdit = { id?: number; account: number; kind: "chequera" | "tarjeta_debito"; name: string; last4: string; holder: string; check_from: string; check_to: string; next_check: string; active: boolean };
type View = "libro" | "conciliacion" | "estados";
const mask = (n: string | null) => (!n ? "—" : n.length <= 4 ? n : `••••${n.slice(-4)}`);

export default function Banks() {
  const { toast, allows } = useSession();
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<BookAccount[] | null>(null);
  const [inst, setInst] = useState<Record<number, Instrument[]>>({});
  const [acc, setAcc] = useState<AccEdit | null>(null);
  const [open, setOpen] = useState<OpenEdit | null>(null);
  const [ins, setIns] = useState<InsEdit | null>(null);
  const canEdit = allows("accounting.editar");
  const selId = Number(params.get("cuenta")) || null;
  const view = (["libro", "conciliacion", "estados"].includes(params.get("vista") || "") ? params.get("vista") : "libro") as View;

  const load = () => {
    api<BookAccount[]>("/bank-books/accounts?all=1").then(setRows).catch((e) => toast(errMsg(e), "bad"));
    api<LegacyAccount[]>("/banking/accounts?all=1").then((r) => setInst(Object.fromEntries(r.map((a) => [a.id, a.instruments])))).catch(() => setInst({}));
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const go = (id: number | null, v: View = "libro") => setParams(id ? { cuenta: String(id), vista: v } : {});

  const saveAcc = async () => {
    if (!acc) return;
    const { id, ...b } = acc;
    try {
      await api(id ? `/banking/accounts/${id}` : "/banking/accounts", { method: id ? "PUT" : "POST", json: { ...b, bank: b.bank || null, number: b.number || null, opening_balance: b.opening_balance || "0", opening_date: b.opening_date || null } });
      toast("Cuenta guardada"); setAcc(null); load();
    } catch (e) { toast(errMsg(e), "bad"); }
  };
  const saveOpen = async () => {
    if (!open) return;
    if (!open.opening_date) { toast("Elegí la fecha de corte", "bad"); return; }
    try {
      await api(`/bank-books/accounts/${open.id}/opening`, { method: "PUT", json: { opening_balance: open.opening_balance || "0", opening_date: open.opening_date, kind: open.kind } });
      toast("Saldo inicial guardado"); setOpen(null); load();
    } catch (e) { toast(errMsg(e), "bad"); }
  };
  const saveIns = async () => {
    if (!ins) return;
    const { id, account, ...b } = ins;
    const body = { ...b, last4: b.kind === "tarjeta_debito" ? b.last4 : null, holder: b.holder || null, check_from: b.check_from ? Number(b.check_from) : null, check_to: b.check_to ? Number(b.check_to) : null, next_check: b.next_check ? Number(b.next_check) : null };
    try { await api(id ? `/banking/instruments/${id}` : `/banking/accounts/${account}/instruments`, { method: id ? "PUT" : "POST", json: body }); toast("Guardado"); setIns(null); load(); }
    catch (e) { toast(errMsg(e), "bad"); }
  };
  const editOpening = (a: BookAccount) => setOpen({ id: a.id, name: a.name, currency: a.currency, opening_balance: String(num(a.opening_balance)), opening_date: a.opening_date || DEFAULT_CUTOFF, kind: a.kind });

  const sel = rows?.find((a) => a.id === selId) ?? null;

  return (
    <>
      {rows === null ? <Loading /> : sel ? (
        <BankBook account={sel} accounts={rows} view={view} setView={(v) => go(sel.id, v)} onBack={() => go(null)} onChanged={load} />
      ) : <>
        {canEdit && <div style={{ display: "flex", justifyContent: "flex-end" }}><button className="btn btn--crimson btn--sm" onClick={() => setAcc({ name: "", bank: "", currency: "CRC", number: "", active: true, opening_balance: "0", opening_date: DEFAULT_CUTOFF })}><Icon d={I.plus} />Cuenta</button></div>}
        {rows.length === 0 ? <Empty title="Sin cuentas" hint="Agregá las cuentas en colones, en dólares y la caja, cada una con su saldo inicial a la fecha de corte. El banco no se conecta: el saldo del banco sale del estado de cuenta que se importa." /> : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(min(300px,100%),1fr))", gap: 14 }}>
            {rows.map((a) => {
              const diff = a.difference !== null ? num(a.difference) : null;
              const instruments = inst[a.id] || [];
              return (
                <div key={a.id} className="card" style={{ opacity: a.active ? 1 : 0.6 }}><div className="card__body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                    <b style={{ minWidth: 0, overflowWrap: "anywhere" }}>{a.name}</b>
                    <span style={{ display: "flex", gap: 4 }}>{a.kind === "caja" && <span className="badge badge--info">Caja</span>}<span className="badge badge--muted">{a.currency}</span></span>
                  </div>
                  <div className="muted" style={{ fontSize: 13 }}>{a.bank || "—"} · <span className="mono">{mask(a.number)}</span></div>
                  <div>
                    <div className="meta">Saldo según Crimson</div>
                    <div className="mono" style={{ fontWeight: 700, fontSize: 20 }}>{fmtMoney(a.book_balance, a.currency)}</div>
                  </div>
                  <div style={{ fontSize: 13 }}>
                    {a.bank_balance !== null && a.last_statement_date ? <>
                      Banco al {fmtDate(a.last_statement_date)}: <b className="mono">{fmtMoney(a.bank_balance, a.currency)}</b>{" "}
                      {diff !== null && (diff === 0 ? <span className="badge badge--ok">Calza</span> : <span className="badge badge--warn">Dif. {fmtMoney(diff, a.currency)}</span>)}
                    </> : <span className="muted">Sin estado de cuenta importado</span>}
                  </div>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {a.opening_date ? `Saldo inicial ${fmtMoney(a.opening_balance, a.currency)} al ${fmtDate(a.opening_date)}` : <span style={{ color: "var(--warn)" }}>Falta el saldo inicial y la fecha de corte</span>}
                    {" · "}{a.pending_lines} del banco y {a.unmatched_entries} de Crimson sin conciliar
                    {a.cash_default && " · recibe el efectivo sin cuenta"}
                  </div>
                  {instruments.length > 0 && (
                    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                      {instruments.map((i) => <li key={i.id}>{i.kind === "chequera" ? `Chequera ${i.name} · ${i.check_from}–${i.check_to} · siguiente ${i.next_check ?? "—"}` : `Tarjeta ${i.name} ····${i.last4}${i.holder ? ` · ${i.holder}` : ""}`}{!i.active && " (inactiva)"} {canEdit && <button className="btn btn--ghost btn--sm" onClick={() => setIns({ id: i.id, account: a.id, kind: i.kind, name: i.name, last4: i.last4 || "", holder: i.holder || "", check_from: String(i.check_from ?? ""), check_to: String(i.check_to ?? ""), next_check: String(i.next_check ?? ""), active: i.active })}>Editar</button>}</li>)}
                    </ul>
                  )}
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
                    <button className="btn btn--crimson btn--sm" onClick={() => go(a.id, "libro")}>Libro</button>
                    <button className="btn btn--soft btn--sm" onClick={() => go(a.id, "conciliacion")}>Conciliar</button>
                    {canEdit && <button className="btn btn--ghost btn--sm" onClick={() => editOpening(a)}>Saldo inicial</button>}
                    {canEdit && <button className="btn btn--ghost btn--sm" onClick={() => setAcc({ id: a.id, name: a.name, bank: a.bank || "", currency: a.currency, number: a.number || "", active: a.active, opening_balance: String(num(a.opening_balance)), opening_date: a.opening_date || "" })}>Editar cuenta</button>}
                    {canEdit && <button className="btn btn--ghost btn--sm" onClick={() => setIns({ account: a.id, kind: "chequera", name: "", last4: "", holder: "", check_from: "", check_to: "", next_check: "", active: true })}>Chequeras y tarjetas</button>}
                  </div>
                </div></div>
              );
            })}
          </div>
        )}
      </>}

      {open && (
        <Modal title={`Saldo inicial · ${open.name}`} onClose={() => setOpen(null)} foot={<><button className="btn btn--ghost" onClick={() => setOpen(null)}>Cancelar</button><button className="btn btn--crimson" onClick={saveOpen}>Guardar</button></>}>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>El saldo que tenía la cuenta según el banco al final del día anterior a la fecha de corte. Desde esa fecha el saldo se mueve con lo registrado en Crimson; lo anterior ya está en este saldo.</p>
          <div className="grid-2">
            <Field label={`Saldo inicial (${open.currency})`}><input className="input input--mono" inputMode="decimal" value={open.opening_balance} onChange={(e) => setOpen({ ...open, opening_balance: e.target.value })} /></Field>
            <Field label="Fecha de corte" hint="La empresa arrancó en Crimson el 1 de noviembre de 2026."><input className="input" type="date" value={open.opening_date} onChange={(e) => setOpen({ ...open, opening_date: e.target.value })} /></Field>
            <Field label="Tipo" hint="La única caja de cada divisa recibe el efectivo cobrado o pagado sin cuenta."><select className="select" value={open.kind} onChange={(e) => setOpen({ ...open, kind: e.target.value as OpenEdit["kind"] })}><option value="banco">Cuenta bancaria</option><option value="caja">Caja (efectivo)</option></select></Field>
          </div>
        </Modal>
      )}
      {acc && (
        <Modal title={acc.id ? "Cuenta bancaria" : "Nueva cuenta"} onClose={() => setAcc(null)} foot={<><button className="btn btn--ghost" onClick={() => setAcc(null)}>Cancelar</button><button className="btn btn--crimson" onClick={saveAcc}>Guardar</button></>}>
          <div className="grid-2">
            <Field label="Nombre"><input className="input" value={acc.name} onChange={(e) => setAcc({ ...acc, name: e.target.value })} /></Field>
            <Field label="Banco"><input className="input" value={acc.bank} onChange={(e) => setAcc({ ...acc, bank: e.target.value })} /></Field>
            <Field label="Divisa"><select className="select" value={acc.currency} onChange={(e) => setAcc({ ...acc, currency: e.target.value })}><option>CRC</option><option>USD</option></select></Field>
            <Field label="Número / IBAN"><input className="input input--mono" style={{ textAlign: "left" }} value={acc.number} onChange={(e) => setAcc({ ...acc, number: e.target.value })} /></Field>
            <Field label="Saldo inicial"><input className="input input--mono" inputMode="decimal" value={acc.opening_balance} onChange={(e) => setAcc({ ...acc, opening_balance: e.target.value })} /></Field>
            <Field label="Fecha de corte" hint="Los movimientos anteriores no suman al saldo."><input className="input" type="date" value={acc.opening_date} onChange={(e) => setAcc({ ...acc, opening_date: e.target.value })} /></Field>
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
