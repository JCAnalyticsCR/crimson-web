/* Una cuenta: libro de movimientos con saldo corrido, conciliacion contra el estado de cuenta del banco y archivos
   importados. Tablet 768 px: todas las tablas usan table--stack (pasan a tarjetas). */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Card, Empty, Field, I, Icon, Loading, Modal } from "../../../ui/components";
import BankImport, { Kpi } from "./BankImport";
import {
  MOVEMENT_KINDS, TYPE_LABEL, errMsg, num,
  type BookAccount, type BookEntry, type Candidate, type Ledger, type Reconciliation, type StatementImport, type StatementLine,
} from "./bankTypes";

type View = "libro" | "conciliacion" | "estados";
type LineFilter = "" | "pendiente" | "conciliado" | "ignorado";

export default function BankBook({ account, accounts, view, setView, onBack, onChanged }: {
  account: BookAccount; accounts: BookAccount[]; view: View; setView: (v: View) => void; onBack: () => void; onChanged: () => void;
}) {
  const { allows } = useSession();
  const [importing, setImporting] = useState(false);
  const [mov, setMov] = useState(false);
  const [trf, setTrf] = useState(false);
  const [tick, setTick] = useState(0);
  const cur = account.currency;
  const canCreate = allows("accounting.crear");
  const refresh = () => { setTick((t) => t + 1); onChanged(); };
  const diff = account.difference !== null ? num(account.difference) : null;

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <button className="btn btn--ghost btn--sm" onClick={onBack}>← Cuentas</button>
        <div style={{ flex: "1 1 200px", minWidth: 0 }}>
          <div className="h2" style={{ margin: 0 }}>{account.name} <span className="badge badge--muted">{cur}</span>{account.kind === "caja" && <span className="badge badge--info" style={{ marginLeft: 6 }}>Caja</span>}</div>
          <div className="muted" style={{ fontSize: 13 }}>{account.opening_date ? `Saldo inicial ${fmtMoney(account.opening_balance, cur)} al ${fmtDate(account.opening_date)}` : "Sin fecha de corte"}</div>
        </div>
        {canCreate && <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button className="btn btn--ghost btn--sm" onClick={() => setMov(true)}><Icon d={I.plus} />Movimiento</button>
          {accounts.length > 1 && <button className="btn btn--ghost btn--sm" onClick={() => setTrf(true)}>Transferir</button>}
          <button className="btn btn--crimson btn--sm" onClick={() => setImporting(true)}><Icon d={I.upload} />Importar estado de cuenta</button>
        </div>}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(160px,1fr))", gap: 10 }}>
        <Kpi label="Saldo según Crimson (hoy)" value={fmtMoney(account.book_balance, cur)} />
        <Kpi label={`Saldo según banco${account.last_statement_date ? ` al ${fmtDate(account.last_statement_date)}` : ""}`} value={account.bank_balance !== null ? fmtMoney(account.bank_balance, cur) : "Sin estado de cuenta"} />
        <Kpi label="Diferencia a esa fecha" value={diff === null ? "—" : fmtMoney(diff, cur)} tone={diff === null ? undefined : diff === 0 ? "ok" : "warn"} />
        <Kpi label="Por conciliar" value={`${account.pending_lines} banco · ${account.unmatched_entries} Crimson`} tone={account.pending_lines + account.unmatched_entries ? "warn" : "ok"} />
      </div>

      <div className="tabs" role="tablist">
        {([["libro", "Libro de movimientos"], ["conciliacion", "Conciliación"], ["estados", "Estados de cuenta"]] as [View, string][]).map(([k, l]) => (
          <button key={k} className={view === k ? "is-active" : ""} onClick={() => setView(k)}>{l}</button>
        ))}
      </div>

      {view === "libro" && <LedgerView account={account} tick={tick} />}
      {view === "conciliacion" && <ReconcileView account={account} tick={tick} onChanged={refresh} />}
      {view === "estados" && <StatementsView account={account} tick={tick} onChanged={refresh} />}

      {importing && <BankImport account={account} onClose={() => setImporting(false)} onDone={() => { setImporting(false); setView("conciliacion"); refresh(); }} />}
      {mov && <MovementModal account={account} onClose={() => setMov(false)} onDone={() => { setMov(false); refresh(); }} />}
      {trf && <TransferModal account={account} accounts={accounts} onClose={() => setTrf(false)} onDone={() => { setTrf(false); refresh(); }} />}
    </>
  );
}

/* ---------------------------------------------------------------- libro */

function LedgerView({ account, tick }: { account: BookAccount; tick: number }) {
  const { toast } = useSession();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [lg, setLg] = useState<Ledger | null>(null);
  const cur = account.currency;
  useEffect(() => {
    const q = new URLSearchParams();
    if (from) q.set("date_from", from);
    if (to) q.set("date_to", to);
    api<Ledger>(`/bank-books/accounts/${account.id}/ledger?${q}`).then(setLg).catch((e) => toast(errMsg(e), "bad"));
  }, [account.id, from, to, tick]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!lg) return <Loading />;
  return (
    <Card flush title="Libro de movimientos" extra={
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <input className="input" type="date" aria-label="Desde" value={from} onChange={(e) => setFrom(e.target.value)} style={{ width: 150 }} />
        <input className="input" type="date" aria-label="Hasta" value={to} onChange={(e) => setTo(e.target.value)} style={{ width: 150 }} />
      </div>
    }>
      {lg.warnings.map((w) => <div key={w} className="badge badge--warn" style={{ display: "block", whiteSpace: "normal", margin: "10px 16px 0", padding: "8px 10px" }}>{w}</div>)}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 8, padding: "12px 16px" }}>
        <Kpi label={from ? `Saldo al ${fmtDate(from)}` : "Saldo inicial"} value={fmtMoney(lg.period_start_balance, cur)} />
        <Kpi label="Entradas" value={fmtMoney(lg.inflows, cur)} tone="ok" />
        <Kpi label="Salidas" value={fmtMoney(lg.outflows, cur)} tone="bad" />
        <Kpi label={to ? `Saldo al ${fmtDate(to)}` : "Saldo final"} value={fmtMoney(lg.closing_balance, cur)} />
      </div>
      {lg.entries.length === 0 ? <Empty title="Sin movimientos" hint="Aquí salen los cobros, gastos pagados, pagos a proveedor, planillas y movimientos registrados en esta cuenta desde la fecha de corte." /> : (
        <table className="table table--sm table--stack">
          <thead><tr><th>Fecha</th><th>Movimiento</th><th>Documento</th><th>Contraparte</th><th>Referencia</th><th className="num">Entra</th><th className="num">Sale</th><th className="num">Saldo</th><th>Banco</th></tr></thead>
          <tbody>
            {lg.entries.map((e) => {
              const a = num(e.amount);
              return (
                <tr key={`${e.type}-${e.id}`}>
                  <td data-label="Fecha">{fmtDate(e.date)}</td>
                  <td data-label="Movimiento">{e.label}</td>
                  <td data-label="Documento" className="stack-full">{e.to ? <Link className="row-link" to={e.to}>{e.document || TYPE_LABEL[e.type]}</Link> : e.document || "—"}</td>
                  <td data-label="Contraparte">{e.counterparty || "—"}</td>
                  <td data-label="Referencia" className="mono">{e.reference || "—"}</td>
                  <td data-label="Entra" className="num mono" style={{ color: "var(--ok)" }}>{a > 0 ? fmtMoney(a, cur) : ""}</td>
                  <td data-label="Sale" className="num mono" style={{ color: "var(--bad)" }}>{a < 0 ? fmtMoney(-a, cur) : ""}</td>
                  <td data-label="Saldo" className="num mono" style={{ fontWeight: 600 }}>{fmtMoney(e.balance, cur)}</td>
                  <td data-label="Banco">{e.line_id ? <span className="badge badge--ok">Conciliado</span> : <span className="badge badge--muted">Pendiente</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Card>
  );
}

/* ---------------------------------------------------------------- conciliacion */

function ReconcileView({ account, tick, onChanged }: { account: BookAccount; tick: number; onChanged: () => void }) {
  const { toast, allows } = useSession();
  const [asOf, setAsOf] = useState("");
  const [rec, setRec] = useState<Reconciliation | null>(null);
  const [filter, setFilter] = useState<LineFilter>("");
  const [lines, setLines] = useState<StatementLine[] | null>(null);
  const [matchLine, setMatchLine] = useState<StatementLine | null>(null);
  const [movLine, setMovLine] = useState<StatementLine | null>(null);
  const [expLine, setExpLine] = useState<StatementLine | null>(null);
  const cur = account.currency;
  const canEdit = allows("accounting.editar");
  const canCreate = allows("accounting.crear");

  const load = () => {
    api<Reconciliation>(`/bank-books/accounts/${account.id}/reconciliation${asOf ? `?as_of=${asOf}` : ""}`).then(setRec).catch((e) => toast(errMsg(e), "bad"));
    api<StatementLine[]>(`/bank-books/accounts/${account.id}/lines${filter ? `?status=${filter}` : ""}`).then(setLines).catch((e) => toast(errMsg(e), "bad"));
  };
  useEffect(load, [account.id, asOf, filter, tick]); // eslint-disable-line react-hooks/exhaustive-deps
  const changed = () => { load(); onChanged(); };
  const post = async (path: string, ok: string, json?: unknown) => {
    try { await api(path, { method: "POST", json }); toast(ok); changed(); } catch (e) { toast(errMsg(e), "bad"); }
  };

  if (!rec) return <Loading />;
  const unexplained = num(rec.unexplained);
  return (
    <>
      <Card title="Resumen" extra={
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <input className="input" type="date" aria-label="Al" value={asOf || rec.as_of} onChange={(e) => setAsOf(e.target.value)} style={{ width: 150 }} />
          {canEdit && <button className="btn btn--soft btn--sm" onClick={() => post(`/bank-books/accounts/${account.id}/auto-match`, "Conciliación automática hecha")}><Icon d={I.check} />Conciliar automáticamente</button>}
        </div>
      }>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(160px,1fr))", gap: 8 }}>
          <Kpi label={`Saldo según banco al ${fmtDate(rec.as_of)}`} value={fmtMoney(rec.bank_balance, cur)} hint={rec.bank_reported_balance !== null ? `El banco reporta ${fmtMoney(rec.bank_reported_balance, cur)}` : undefined} />
          <Kpi label="Saldo según Crimson" value={fmtMoney(rec.book_balance, cur)} />
          <Kpi label="Diferencia" value={fmtMoney(rec.difference, cur)} tone={num(rec.difference) === 0 ? "ok" : "warn"} />
          <Kpi label="En el banco, no en Crimson" value={`${rec.pending_bank.count} · ${fmtMoney(rec.pending_bank.total, cur)}`} tone={rec.pending_bank.count ? "warn" : "ok"} />
          <Kpi label="En Crimson, no en el banco" value={`${rec.pending_book.count} · ${fmtMoney(rec.pending_book.total, cur)}`} tone={rec.pending_book.count ? "warn" : "ok"} />
          <Kpi label="Sin explicar" value={fmtMoney(unexplained, cur)} tone={unexplained === 0 ? "ok" : "bad"} hint={unexplained === 0 ? (rec.reconciled ? "Conciliado" : "La diferencia es lo pendiente") : "Casados con montos distintos"} />
        </div>
        {rec.warnings.map((w) => <div key={w} className="badge badge--warn" style={{ display: "block", whiteSpace: "normal", marginTop: 8, padding: "8px 10px" }}>{w}</div>)}
      </Card>

      <Card flush title={`En Crimson y no en el banco (${rec.pending_book.count})`}>
        {rec.pending_book.items.length === 0 ? <Empty title="Todo lo de Crimson aparece en el banco" /> : <EntryTable items={rec.pending_book.items} currency={cur} />}
      </Card>

      <Card flush title="Estado de cuenta del banco" extra={
        <div className="tabs">{([["", "Todas"], ["pendiente", "Pendientes"], ["conciliado", "Conciliadas"], ["ignorado", "Ignoradas"]] as [LineFilter, string][]).map(([k, l]) => (
          <button key={k || "all"} className={filter === k ? "is-active" : ""} onClick={() => setFilter(k)}>{l}</button>
        ))}</div>
      }>
        {lines === null ? <Loading /> : lines.length === 0 ? <Empty hint={filter === "pendiente" ? "Nada pendiente: todo lo del banco está conciliado." : "Importá un estado de cuenta para conciliar."} /> : (
          <table className="table table--sm table--stack">
            <thead><tr><th>Fecha</th><th>Descripción</th><th>Referencia</th><th className="num">Monto</th><th>Casado con</th><th /></tr></thead>
            <tbody>
              {lines.map((l) => {
                const a = num(l.amount);
                return (
                  <tr key={l.id} style={{ opacity: l.status === "ignorado" ? 0.55 : 1 }}>
                    <td data-label="Fecha">{fmtDate(l.date)}</td>
                    <td data-label="Descripción" className="stack-full">{l.description}</td>
                    <td data-label="Referencia" className="mono">{l.reference || "—"}</td>
                    <td data-label="Monto" className="num mono" style={{ fontWeight: 700, color: a >= 0 ? "var(--ok)" : "var(--bad)" }}>{fmtMoney(a, cur)}</td>
                    <td data-label="Casado con" className="stack-full" style={{ fontSize: 13 }}>
                      {l.status === "conciliado" && l.match ? <>
                        {l.match.to ? <Link className="row-link" to={l.match.to}>{l.match.label}</Link> : l.match.label}
                        <span className="meta" style={{ marginLeft: 4 }}>{fmtDate(l.match.date)} · {l.matched_by === "auto" ? "automático" : "a mano"}</span>
                        {l.diff && num(l.diff) !== 0 && <span className="badge badge--warn" style={{ marginLeft: 4 }}>dif. {fmtMoney(l.diff, cur)}</span>}
                      </> : l.status === "ignorado" ? <span className="muted">Ignorada</span> : <span className="badge badge--warn">Pendiente</span>}
                    </td>
                    <td data-label=" " className="stack-actions" style={{ whiteSpace: "nowrap" }}>
                      {l.status === "pendiente" && canEdit && <button className="btn btn--soft btn--sm" onClick={() => setMatchLine(l)}>Casar</button>}
                      {l.status === "pendiente" && canCreate && <button className="btn btn--ghost btn--sm" onClick={() => setMovLine(l)}>Registrar</button>}
                      {l.status === "pendiente" && canCreate && a < 0 && <button className="btn btn--ghost btn--sm" onClick={() => setExpLine(l)}>Crear gasto</button>}
                      {l.status !== "conciliado" && canEdit && <button className="btn btn--ghost btn--sm" onClick={() => post(`/bank-books/lines/${l.id}/ignore`, l.status === "ignorado" ? "Reactivada" : "Ignorada")}>{l.status === "ignorado" ? "Reactivar" : "Ignorar"}</button>}
                      {l.status === "conciliado" && canEdit && <button className="btn btn--ghost btn--sm" onClick={() => post(`/bank-books/lines/${l.id}/unmatch`, "Conciliación deshecha")}>Deshacer</button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      {matchLine && <MatchModal line={matchLine} currency={cur} onClose={() => setMatchLine(null)} onDone={() => { setMatchLine(null); changed(); }} />}
      {movLine && <LineMovementModal line={movLine} currency={cur} onClose={() => setMovLine(null)} onDone={() => { setMovLine(null); changed(); }} />}
      {expLine && <LineExpenseModal line={expLine} currency={cur} onClose={() => setExpLine(null)} onDone={() => { setExpLine(null); changed(); }} />}
    </>
  );
}

function EntryTable({ items, currency }: { items: BookEntry[]; currency: string }) {
  return (
    <table className="table table--sm table--stack">
      <thead><tr><th>Fecha</th><th>Movimiento</th><th>Documento</th><th>Contraparte</th><th>Referencia</th><th className="num">Monto</th></tr></thead>
      <tbody>
        {items.map((e) => (
          <tr key={`${e.type}-${e.id}`}>
            <td data-label="Fecha">{fmtDate(e.date)}</td>
            <td data-label="Movimiento">{e.label}</td>
            <td data-label="Documento" className="stack-full">{e.to ? <Link className="row-link" to={e.to}>{e.document || TYPE_LABEL[e.type]}</Link> : e.document || "—"}</td>
            <td data-label="Contraparte">{e.counterparty || "—"}</td>
            <td data-label="Referencia" className="mono">{e.reference || "—"}</td>
            <td data-label="Monto" className="num mono" style={{ color: num(e.amount) >= 0 ? "var(--ok)" : "var(--bad)" }}>{fmtMoney(e.amount, currency)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function MatchModal({ line, currency, onClose, onDone }: { line: StatementLine; currency: string; onClose: () => void; onDone: () => void }) {
  const { toast } = useSession();
  const [cands, setCands] = useState<Candidate[] | null>(null);
  useEffect(() => { api<Candidate[]>(`/bank-books/lines/${line.id}/candidates`).then(setCands).catch((e) => toast(errMsg(e), "bad")); }, [line.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = async (c: Candidate) => {
    try {
      const r = await api<{ assigned_account: boolean }>(`/bank-books/lines/${line.id}/match`, { method: "POST", json: { type: c.type, id: c.id } });
      toast(r.assigned_account ? "Conciliado; el movimiento quedó asignado a esta cuenta" : "Conciliado");
      onDone();
    } catch (e) { toast(errMsg(e), "bad"); }
  };
  return (
    <Modal wide title="Casar con un movimiento de Crimson" onClose={onClose}>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
        <b>{line.description}</b> · {fmtDate(line.date)}{line.reference ? ` · ref. ${line.reference}` : ""} · <b style={{ color: num(line.amount) >= 0 ? "var(--ok)" : "var(--bad)" }}>{fmtMoney(line.amount, currency)}</b>
      </p>
      {cands === null ? <Loading /> : cands.length === 0 ? <Empty title="Sin sugerencias" hint="Nada parecido en ±45 días. Si solo existe en el banco (intereses, cargos), usá «Registrar»." /> : (
        <table className="table table--sm table--stack">
          <thead><tr><th>Fecha</th><th>Movimiento</th><th>Contraparte</th><th>Referencia</th><th className="num">Monto</th><th>Coincide</th><th /></tr></thead>
          <tbody>
            {cands.map((c) => (
              <tr key={`${c.type}-${c.id}`}>
                <td data-label="Fecha">{fmtDate(c.date)}<span className="meta" style={{ marginLeft: 4 }}>{c.days ? `±${c.days} d` : "mismo día"}</span></td>
                <td data-label="Movimiento" className="stack-full">{c.label}{c.document ? ` · ${c.document}` : ""}{c.unassigned && <div className="meta" style={{ textTransform: "none" }}>Sin cuenta: al casarlo queda en esta</div>}</td>
                <td data-label="Contraparte">{c.counterparty || "—"}</td>
                <td data-label="Referencia" className="mono">{c.reference || "—"}</td>
                <td data-label="Monto" className="num mono">{fmtMoney(c.amount, currency)}</td>
                <td data-label="Coincide">
                  {c.exact ? <span className="badge badge--ok">Monto exacto</span> : <span className="badge badge--warn">dif. {fmtMoney(c.diff, currency)}</span>}
                  {c.ref && <span className="badge badge--info" style={{ marginLeft: 4 }}>Referencia</span>}
                </td>
                <td data-label=" " className="stack-actions"><button className="btn btn--crimson btn--sm" onClick={() => pick(c)}>Casar</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Modal>
  );
}

function LineMovementModal({ line, currency, onClose, onDone }: { line: StatementLine; currency: string; onClose: () => void; onDone: () => void }) {
  const { toast } = useSession();
  const [kind, setKind] = useState(num(line.amount) >= 0 ? "interes" : "cargo");
  const [desc, setDesc] = useState(line.description);
  const save = async () => {
    try { await api(`/bank-books/lines/${line.id}/movement`, { method: "POST", json: { kind, description: desc } }); toast("Registrado y conciliado"); onDone(); }
    catch (e) { toast(errMsg(e), "bad"); }
  };
  return (
    <Modal title="Registrar movimiento del banco" onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" onClick={save}>Registrar y conciliar</button></>}>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Para lo que solo existe en el banco (intereses, cargos, ajustes). Si es un gasto con factura, usá «Crear gasto»; si es un cobro o un pago, casalo.</p>
      <p style={{ fontSize: 13 }}>{fmtDate(line.date)} · <b>{fmtMoney(line.amount, currency)}</b></p>
      <div className="grid-2">
        <Field label="Tipo"><select className="select" value={kind} onChange={(e) => setKind(e.target.value)}>{MOVEMENT_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
        <Field label="Descripción"><input className="input" value={desc} maxLength={300} onChange={(e) => setDesc(e.target.value)} /></Field>
      </div>
    </Modal>
  );
}

function LineExpenseModal({ line, currency, onClose, onDone }: { line: StatementLine; currency: string; onClose: () => void; onDone: () => void }) {
  const { toast } = useSession();
  const [cats, setCats] = useState<{ id: number; name: string }[]>([]);
  const [f, setF] = useState({ category_id: "", description: line.description, tax_rate: "0" });
  useEffect(() => { api<{ id: number; name: string }[]>("/expense-categories").then(setCats).catch(() => setCats([])); }, []);
  const save = async () => {
    try {
      await api(`/banking/lines/${line.id}/expense`, { method: "POST", json: { category_id: f.category_id ? Number(f.category_id) : null, description: f.description, tax_rate: Number(f.tax_rate) } });
      toast("Gasto creado y conciliado"); onDone();
    } catch (e) { toast(errMsg(e), "bad"); }
  };
  return (
    <Modal title="Crear gasto desde el banco" onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" onClick={save}>Crear gasto y conciliar</button></>}>
      <p style={{ fontSize: 13, marginTop: 0 }}>{fmtDate(line.date)} · <b>{fmtMoney(line.amount, currency)}</b></p>
      <Field label="Descripción"><input className="input" value={f.description} maxLength={300} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
      <div className="grid-2">
        <Field label="Categoría"><select className="select" value={f.category_id} onChange={(e) => setF({ ...f, category_id: e.target.value })}><option value="">—</option>{cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
        <Field label="IVA"><select className="select" value={f.tax_rate} onChange={(e) => setF({ ...f, tax_rate: e.target.value })}>{["0", "1", "2", "4", "13"].map((r) => <option key={r} value={r}>{r} %</option>)}</select></Field>
      </div>
    </Modal>
  );
}

/* ---------------------------------------------------------------- estados de cuenta */

function StatementsView({ account, tick, onChanged }: { account: BookAccount; tick: number; onChanged: () => void }) {
  const { toast, allows } = useSession();
  const [rows, setRows] = useState<StatementImport[] | null>(null);
  const [undo, setUndo] = useState<StatementImport | null>(null);
  const load = () => api<StatementImport[]>(`/bank-books/accounts/${account.id}/statements`).then(setRows).catch((e) => toast(errMsg(e), "bad"));
  useEffect(() => { load(); }, [account.id, tick]); // eslint-disable-line react-hooks/exhaustive-deps
  const doUndo = async () => {
    if (!undo) return;
    try { const r = await api<{ deleted: number }>(`/bank-books/statements/${undo.id}`, { method: "DELETE" }); toast(`Importación deshecha: ${r.deleted} líneas borradas`); setUndo(null); load(); onChanged(); }
    catch (e) { toast(errMsg(e), "bad"); }
  };
  if (!rows) return <Loading />;
  return (
    <Card flush title="Archivos importados">
      {rows.length === 0 ? <Empty title="Sin estados de cuenta" hint="Descargá el estado de cuenta del banco en Excel o CSV e importalo: se revisa antes de guardar." /> : (
        <table className="table table--sm table--stack">
          <thead><tr><th>Archivo</th><th>Período</th><th className="num">Movimientos</th><th className="num">Nuevos</th><th className="num">Saldo del banco</th><th>Importado</th><th /></tr></thead>
          <tbody>
            {rows.map((i) => (
              <tr key={i.id}>
                <td data-label="Archivo" className="stack-full" style={{ overflowWrap: "anywhere" }}>{i.filename}</td>
                <td data-label="Período">{i.date_from ? `${fmtDate(i.date_from)} – ${fmtDate(i.date_to)}` : "—"}</td>
                <td data-label="Movimientos" className="num">{i.rows}</td>
                <td data-label="Nuevos" className="num">{i.added}{i.duplicates ? <span className="meta" style={{ marginLeft: 4 }}>({i.duplicates} rep.)</span> : null}</td>
                <td data-label="Saldo del banco" className="num mono">{i.closing_balance ? fmtMoney(i.closing_balance, account.currency) : "—"}</td>
                <td data-label="Importado">{fmtDate(i.created_at.slice(0, 10))}</td>
                <td data-label=" " className="stack-actions">{allows("accounting.editar") && i.added > 0 && <button className="btn btn--ghost btn--sm" onClick={() => setUndo(i)}>Deshacer</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {undo && (
        <Modal title="Deshacer importación" onClose={() => setUndo(null)} foot={<><button className="btn btn--ghost" onClick={() => setUndo(null)}>Cancelar</button><button className="btn btn--crimson" onClick={doUndo}>Borrar sus líneas</button></>}>
          <p>Se borran las {undo.added} líneas que entraron con <b>{undo.filename}</b>. Si alguna ya está conciliada, primero deshacé esa conciliación.</p>
        </Modal>
      )}
    </Card>
  );
}

/* ---------------------------------------------------------------- movimientos propios */

function MovementModal({ account, onClose, onDone }: { account: BookAccount; onClose: () => void; onDone: () => void }) {
  const { toast } = useSession();
  const [f, setF] = useState({ date: new Date().toISOString().slice(0, 10), kind: "deposito", dir: "entra", amount: "", description: "", reference: "" });
  const save = async () => {
    const n = Number(f.amount);
    if (!(n > 0)) { toast("Escribí el monto", "bad"); return; }
    try {
      await api(`/bank-books/accounts/${account.id}/movements`, { method: "POST", json: { date: f.date, kind: f.kind, amount: (f.dir === "entra" ? n : -n).toFixed(2), description: f.description || MOVEMENT_KINDS.find(([k]) => k === f.kind)?.[1], reference: f.reference || null } });
      toast("Movimiento registrado"); onDone();
    } catch (e) { toast(errMsg(e), "bad"); }
  };
  return (
    <Modal title="Movimiento de la cuenta" onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" onClick={save}>Guardar</button></>}>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Para lo que no es un cobro, un gasto ni un pago a proveedor: intereses, cargos sin factura, ajustes. Entre cuentas propias usá «Transferir».</p>
      <div className="grid-2">
        <Field label="Fecha"><input className="input" type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></Field>
        <Field label="Tipo"><select className="select" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{MOVEMENT_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
        <Field label="Dirección"><select className="select" value={f.dir} onChange={(e) => setF({ ...f, dir: e.target.value })}><option value="entra">Entra a la cuenta</option><option value="sale">Sale de la cuenta</option></select></Field>
        <Field label={`Monto (${account.currency})`}><input className="input input--mono" inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
        <Field label="Descripción"><input className="input" maxLength={300} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <Field label="Referencia"><input className="input" maxLength={120} value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
      </div>
    </Modal>
  );
}

function TransferModal({ account, accounts, onClose, onDone }: { account: BookAccount; accounts: BookAccount[]; onClose: () => void; onDone: () => void }) {
  const { toast } = useSession();
  const others = accounts.filter((a) => a.id !== account.id && a.active);
  const [f, setF] = useState({ to: String(others[0]?.id ?? ""), date: new Date().toISOString().slice(0, 10), amount: "", amount_to: "", reference: "" });
  const dest = accounts.find((a) => String(a.id) === f.to);
  const fx = !!dest && dest.currency !== account.currency;
  const save = async () => {
    try {
      await api("/bank-books/transfers", { method: "POST", json: { from_account_id: account.id, to_account_id: Number(f.to), date: f.date, amount: f.amount, amount_to: fx ? f.amount_to : null, reference: f.reference || null } });
      toast("Transferencia registrada en las dos cuentas"); onDone();
    } catch (e) { toast(errMsg(e), "bad"); }
  };
  return (
    <Modal title={`Transferir desde ${account.name}`} onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" onClick={save}>Registrar</button></>}>
      <div className="grid-2">
        <Field label="Hacia"><select className="select" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })}>{others.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.currency})</option>)}</select></Field>
        <Field label="Fecha"><input className="input" type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></Field>
        <Field label={`Sale (${account.currency})`}><input className="input input--mono" inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
        {fx && <Field label={`Entra (${dest?.currency})`} hint="Lo que acreditó el banco con su tipo de cambio."><input className="input input--mono" inputMode="decimal" value={f.amount_to} onChange={(e) => setF({ ...f, amount_to: e.target.value })} /></Field>}
        <Field label="Referencia"><input className="input" maxLength={120} value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
      </div>
    </Modal>
  );
}
