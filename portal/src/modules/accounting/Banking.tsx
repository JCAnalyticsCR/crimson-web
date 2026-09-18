/* Conciliacion bancaria: importar estado de cuenta, casar movimientos. */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney, uploadFile } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Card, Empty, Field, I, Icon, Modal } from "../../ui/components";

/* ---------- tipos ---------- */
type BankAccount = {
  id: number; name: string; bank: string; currency: string; number: string | null;
  pending: number; reconciled: number; ignored: number; last_date: string | null;
};
type LineMatch = { type: "payment" | "expense"; id: number; label: string; amount: string; date: string; to: string };
type BankLine = {
  id: number; date: string; description: string; reference: string | null;
  amount: string; status: "pendiente" | "conciliado" | "ignorado";
  matched_by: "auto" | "manual" | null;
  match: LineMatch | null;
};
type Candidate = { type: string; id: number; label: string; amount: string; date: string; diff: number; days: number; ref: boolean };
type ExpCat = { id: number; name: string };

type StatusFilter = "pendiente" | "conciliado" | "ignorado" | "";

const STATUS_TABS: [StatusFilter, string][] = [["", "Todos"], ["pendiente", "Pendientes"], ["conciliado", "Conciliados"], ["ignorado", "Ignorados"]];

export default function Banking() {
  const { toast } = useSession();
  const [accounts, setAccounts] = useState<BankAccount[] | null>(null);
  const [selId, setSelId] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("pendiente");
  const [lines, setLines] = useState<BankLine[]>([]);
  const [linesLoading, setLinesLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  /* match modal */
  const [matchLine, setMatchLine] = useState<BankLine | null>(null);
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);

  /* expense modal */
  const [expLine, setExpLine] = useState<BankLine | null>(null);
  const [expForm, setExpForm] = useState({ category_id: "", description: "", tax_rate: "0" });
  const [cats, setCats] = useState<ExpCat[]>([]);

  const loadAccounts = () => api<BankAccount[]>("/banking/accounts").then(setAccounts).catch((e) => toast(e.message, "bad"));
  useEffect(() => { loadAccounts(); api<ExpCat[]>("/expense-categories").then(setCats); }, []);

  const sel = accounts?.find((a) => a.id === selId) ?? null;

  const loadLines = (aid: number, filter: StatusFilter) => {
    setLinesLoading(true);
    const q = filter ? `?status=${filter}` : "";
    api<BankLine[]>(`/banking/${aid}/lines${q}`)
      .then(setLines)
      .catch((e) => toast(e.message, "bad"))
      .finally(() => setLinesLoading(false));
  };

  useEffect(() => {
    if (selId) loadLines(selId, statusFilter);
  }, [selId, statusFilter]);

  /* auto-select first account */
  useEffect(() => {
    if (accounts && accounts.length > 0 && !selId) setSelId(accounts[0].id);
  }, [accounts]);

  const importFile = async (f: File) => {
    if (!sel) return;
    setImporting(true);
    try {
      const r = await uploadFile<{ added: number; duplicates: number; matched: number; batch: number }>(`/banking/${sel.id}/import`, f);
      toast(`Importado: ${r.added} nuevos, ${r.duplicates} duplicados omitidos, ${r.matched} casados`);
      loadAccounts(); loadLines(sel.id, statusFilter);
    } catch (e) { toast(e instanceof Error ? e.message : "Error importando", "bad"); }
    finally { setImporting(false); }
  };

  const autoMatch = async () => {
    if (!sel) return;
    try {
      const r = await api<{ matched: number }>(`/banking/${sel.id}/auto`, { method: "POST" });
      toast(`Conciliación automática: ${r.matched} movimientos casados`);
      loadAccounts(); loadLines(sel.id, statusFilter);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const openMatchModal = async (l: BankLine) => {
    setMatchLine(l); setCandidates(null);
    try { const c = await api<Candidate[]>(`/banking/lines/${l.id}/candidates`); setCandidates(c); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const doMatch = async (c: Candidate) => {
    if (!matchLine || !sel) return;
    try {
      await api(`/banking/lines/${matchLine.id}/match`, { method: "POST", json: { type: c.type, id: c.id } });
      toast("Movimiento casado"); setMatchLine(null); loadAccounts(); loadLines(sel.id, statusFilter);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const doIgnore = async (l: BankLine) => {
    if (!sel) return;
    try {
      await api(`/banking/lines/${l.id}/ignore`, { method: "POST" });
      toast(l.status === "ignorado" ? "Movimiento reactivado" : "Movimiento ignorado");
      loadAccounts(); loadLines(sel.id, statusFilter);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const doUnmatch = async (l: BankLine) => {
    if (!sel) return;
    try {
      await api(`/banking/lines/${l.id}/unmatch`, { method: "POST" });
      toast("Casación deshecha"); loadAccounts(); loadLines(sel.id, statusFilter);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const createExpense = async () => {
    if (!expLine || !sel) return;
    try {
      await api(`/banking/lines/${expLine.id}/expense`, { method: "POST", json: { category_id: expForm.category_id ? Number(expForm.category_id) : null, description: expForm.description, tax_rate: Number(expForm.tax_rate) } });
      toast("Gasto creado y movimiento casado"); setExpLine(null); loadAccounts(); loadLines(sel.id, statusFilter);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  if (accounts === null) return <span className="spinner" style={{ margin: "40px auto", display: "block" }} />;

  return (
    <>
      <div className="page-head">
        <div><div className="meta">08 · Conciliación</div><h1 className="h1">Conciliación bancaria</h1></div>
        {sel && (
          <div className="page-head__actions">
            <button className="btn btn--ghost btn--sm" onClick={autoMatch}><Icon d={I.check} />Conciliar automáticamente</button>
            <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls,.txt" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) importFile(f); e.target.value = ""; }} />
            <button className="btn btn--crimson btn--sm" onClick={() => fileRef.current?.click()} disabled={importing}>
              {importing ? <span className="spinner" /> : <><Icon d={I.plus} />Importar estado de cuenta</>}
            </button>
          </div>
        )}
      </div>

      {accounts.length === 0 ? (
        <Empty title="Sin cuentas bancarias" hint="Creá una cuenta bancaria en Ajustes → Pagos y cobros para poder conciliar." />
      ) : (
        <>
          {/* selector de cuentas */}
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 4 }}>
            {accounts.map((a) => {
              const pct = a.reconciled + a.pending > 0 ? Math.round((a.reconciled / (a.reconciled + a.pending)) * 100) : 0;
              return (
                <button
                  key={a.id}
                  onClick={() => setSelId(a.id)}
                  style={{ padding: "10px 16px", borderRadius: 12, border: `2px solid ${selId === a.id ? "var(--crimson)" : "var(--hair-2)"}`, background: selId === a.id ? "var(--crimson-soft)" : "var(--surface)", cursor: "pointer", textAlign: "left", minWidth: 180 }}
                >
                  <div style={{ fontWeight: 700, fontSize: 14 }}>{a.name}</div>
                  <div className="meta">{a.bank} · {a.currency}</div>
                  <div style={{ marginTop: 6, fontSize: 12, color: "var(--text-3)" }}>
                    <span style={{ color: "var(--warn)" }}>{a.pending} pend.</span>
                    {" · "}
                    <span style={{ color: "var(--ok)" }}>{pct}% conciliado</span>
                  </div>
                </button>
              );
            })}
          </div>

          {sel && (
            <>
              {/* KPIs */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(160px,1fr))", gap: 10 }}>
                {[
                  ["Pendientes", sel.pending, "var(--warn)"],
                  ["Conciliados", sel.reconciled, "var(--ok)"],
                  ["% conciliado", sel.reconciled + sel.pending > 0 ? `${Math.round((sel.reconciled / (sel.reconciled + sel.pending)) * 100)}%` : "—", "var(--info)"],
                  ["Último movimiento", sel.last_date ? fmtDate(sel.last_date) : "—", "var(--text-3)"],
                ].map(([label, val, color]) => (
                  <div key={String(label)} style={{ background: "var(--surface)", border: "1px solid var(--hair-2)", borderRadius: 12, padding: "12px 16px" }}>
                    <div className="meta">{label}</div>
                    <div style={{ fontWeight: 700, fontSize: 20, color: String(color) }}>{String(val)}</div>
                  </div>
                ))}
              </div>

              {/* ayuda formato */}
              <div style={{ background: "var(--info-soft)", borderRadius: 10, padding: "10px 16px", fontSize: 12, color: "var(--text-2)", lineHeight: 1.6 }}>
                <b>Formato del archivo:</b> columnas <span className="mono">Fecha + Monto</span> o <span className="mono">Fecha + Débito + Crédito</span>. CSV con <span className="mono">;</span> o <span className="mono">,</span> y Excel (.xlsx/.xls) aceptados. Importaciones repetidas se omiten automáticamente.
              </div>

              {/* tabla de líneas */}
              <Card flush extra={
                <div className="tabs">{STATUS_TABS.map(([k, l]) => (
                  <button key={k || "all"} className={statusFilter === k ? "is-active" : ""} onClick={() => setStatusFilter(k)}>{l}</button>
                ))}</div>
              }>
                {linesLoading ? <span className="spinner" style={{ margin: "20px auto", display: "block" }} /> : lines.length === 0 ? (
                  <Empty hint={statusFilter === "pendiente" ? "Sin movimientos pendientes. Importá un estado de cuenta o usá la conciliación automática." : "Sin movimientos para este filtro."} />
                ) : (
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Fecha</th>
                        <th>Descripción</th>
                        <th className="num">Monto</th>
                        <th>Estado</th>
                        <th>Casado con</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {lines.map((l) => {
                        const amt = Number(l.amount);
                        return (
                          <tr key={l.id}>
                            <td className="muted">{fmtDate(l.date)}</td>
                            <td>
                              <span style={{ fontWeight: 600 }}>{l.description}</span>
                              {l.reference && <div className="meta" style={{ textTransform: "none" }}>{l.reference}</div>}
                            </td>
                            <td className="num mono" style={{ fontWeight: 700, color: amt >= 0 ? "var(--ok)" : "var(--bad)" }}>
                              {amt >= 0 ? "+" : ""}{fmtMoney(l.amount, sel.currency)}
                            </td>
                            <td>
                              <Badge status={l.status === "conciliado" ? "confirmado" : l.status === "ignorado" ? "anulada" : "pendiente"} />
                              {l.matched_by && <span className="meta" style={{ marginLeft: 4 }}>{l.matched_by}</span>}
                            </td>
                            <td style={{ fontSize: 13 }}>
                              {l.match
                                ? <Link className="row-link" to={l.match.to}>{l.match.label}<span className="meta" style={{ marginLeft: 4 }}>{fmtMoney(l.match.amount, sel.currency)}</span></Link>
                                : <span className="muted">—</span>}
                            </td>
                            <td className="num" style={{ whiteSpace: "nowrap", display: "flex", gap: 4 }}>
                              {l.status === "pendiente" && (
                                <>
                                  <button className="btn btn--soft btn--sm" onClick={() => openMatchModal(l)}>Casar</button>
                                  {amt < 0 && <button className="btn btn--ghost btn--sm" onClick={() => { setExpLine(l); setExpForm({ category_id: "", description: l.description, tax_rate: "0" }); }}>Crear gasto</button>}
                                  <button className="btn btn--ghost btn--sm" onClick={() => doIgnore(l)}>Ignorar</button>
                                </>
                              )}
                              {l.status === "conciliado" && (
                                <button className="btn btn--ghost btn--sm" onClick={() => doUnmatch(l)}>Deshacer</button>
                              )}
                              {l.status === "ignorado" && (
                                <button className="btn btn--ghost btn--sm" onClick={() => doIgnore(l)}>Reactivar</button>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </Card>
            </>
          )}
        </>
      )}

      {/* modal casar */}
      {matchLine && (
        <Modal title="Casar movimiento" onClose={() => setMatchLine(null)} wide>
          <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
            <b>{matchLine.description}</b> · {fmtDate(matchLine.date)} · <span style={{ fontWeight: 700, color: Number(matchLine.amount) >= 0 ? "var(--ok)" : "var(--bad)" }}>{fmtMoney(matchLine.amount, sel?.currency)}</span>
          </p>
          {candidates === null
            ? <span className="spinner" />
            : candidates.length === 0
            ? <Empty title="Sin candidatos" hint="No se encontraron transacciones que coincidan con este movimiento." />
            : (
              <table className="table">
                <thead><tr><th>Tipo</th><th>Descripción</th><th>Fecha</th><th className="num">Monto</th><th className="num">Diferencia</th><th /></tr></thead>
                <tbody>
                  {candidates.map((c) => (
                    <tr key={`${c.type}-${c.id}`} style={{ background: c.ref ? "var(--ok-soft)" : undefined }}>
                      <td><span className="meta" style={{ textTransform: "capitalize" }}>{c.type}</span></td>
                      <td style={{ fontWeight: c.ref ? 700 : undefined }}>{c.label}{c.ref && <span className="meta" style={{ marginLeft: 4, color: "var(--ok)" }}>ref. exacta</span>}</td>
                      <td className="muted">{fmtDate(c.date)}</td>
                      <td className="num money">{fmtMoney(c.amount, sel?.currency)}</td>
                      <td className="num" style={{ color: Math.abs(c.diff) < 1 ? "var(--ok)" : "var(--warn)" }}>{c.diff === 0 ? "Exacto" : fmtMoney(c.diff, sel?.currency)}</td>
                      <td className="num"><button className="btn btn--crimson btn--sm" onClick={() => doMatch(c)}>Casar</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </Modal>
      )}

      {/* modal crear gasto */}
      {expLine && (
        <Modal title="Crear gasto desde movimiento" onClose={() => setExpLine(null)} foot={
          <><button className="btn btn--ghost" onClick={() => setExpLine(null)}>Cancelar</button>
            <button className="btn btn--crimson" onClick={createExpense}>Crear gasto y casar</button></>
        }>
          <p className="muted" style={{ fontSize: 13 }}>{expLine.description} · {fmtMoney(expLine.amount, sel?.currency)}</p>
          <Field label="Descripción del gasto"><input className="input" value={expForm.description} onChange={(e) => setExpForm({ ...expForm, description: e.target.value })} /></Field>
          <div className="grid-2">
            <Field label="Categoría">
              <select className="select" value={expForm.category_id} onChange={(e) => setExpForm({ ...expForm, category_id: e.target.value })}>
                <option value="">—</option>
                {cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </Field>
            <Field label="IVA %">
              <select className="select" value={expForm.tax_rate} onChange={(e) => setExpForm({ ...expForm, tax_rate: e.target.value })}>
                {["0", "1", "2", "4", "13"].map((r) => <option key={r} value={r}>{r}%</option>)}
              </select>
            </Field>
          </div>
        </Modal>
      )}
    </>
  );
}
