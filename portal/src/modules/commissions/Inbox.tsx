/* Bandeja (13/10, 27:32: "yo aqui le voy a poder aprobar"): lo que espera un dato, lo que hay que aprobar y lo que
   hay que pagar, agrupado por persona o aliado. Nadie aprueba ni paga lo suyo (salvo el admin). */
import { useCallback, useEffect, useState } from "react";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";
import { KIND, errText, type Beneficiaries, type Entry, type Inbox as InboxData, type InboxGroup } from "./types";

type PayForm = { ids: number[]; paid_on: string; paid_ref: string };
type VoidForm = { ids: number[]; reason: string };
type AdjForm = { beneficiary: "vendedor" | "aliado"; user_id: string; ally_company_id: string; amount: string; currency: string; description: string };
type Backfill = { from: string; result: { creados: number; por_beneficiario: { name: string; count: number; total_crc: string }[] } | null };

const hoy = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Costa_Rica" });

export default function Inbox({ onChange }: { onChange?: () => void }) {
  const { toast, allows } = useSession();
  const configura = allows("commissions.configurar");
  const [d, setD] = useState<InboxData | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [sel, setSel] = useState<Record<number, boolean>>({});
  const [pay, setPay] = useState<PayForm | null>(null);
  const [anular, setAnular] = useState<VoidForm | null>(null);
  const [adj, setAdj] = useState<AdjForm | null>(null);
  const [bf, setBf] = useState<Backfill | null>(null);
  const [ben, setBen] = useState<Beneficiaries | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => { api<InboxData>("/commissions/inbox").then((x) => { setD(x); setSel({}); }).catch((e) => toast(errText(e), "bad")); }, [toast]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (configura && (adj || bf) && !ben) api<Beneficiaries>("/commissions/beneficiaries").then(setBen).catch(() => setBen({ users: [], allies: [] })); }, [configura, adj, bf, ben]);

  const done = (msg: string) => { toast(msg); load(); onChange?.(); };
  const patch = async (ids: number[], status: string, extra: Record<string, unknown> = {}) => {
    if (!ids.length) return;
    setBusy(true);
    try {
      const r = await api<{ actualizadas: number; omitidas: { id: number; motivo: string }[] }>("/commissions", { method: "PATCH", json: { ids, status, ...extra } });
      done(`${r.actualizadas} movimiento(s) ${status === "aprobada" ? "aprobados" : status === "pagada" ? "marcados como pagados" : status === "anulada" ? "anulados" : "actualizados"}${r.omitidas.length ? ` · ${r.omitidas.length} omitido(s): ${r.omitidas[0].motivo}` : ""}`);
      setPay(null); setAnular(null);
    } catch (e) { toast(errText(e), "bad"); } finally { setBusy(false); }
  };
  const recalc = async (id?: number) => {
    try {
      if (id) { const r = await api<Entry>(`/commissions/entries/${id}/recalc`, { method: "POST" }); done(r.status === "en_espera" ? `Sigue en espera: ${r.hold_reason}` : "Recalculado"); }
      else { const r = await api<{ recalculadas: number; siguen_en_espera: number }>("/commissions/recalc-waiting", { method: "POST" }); done(`${r.recalculadas} recalculado(s) · ${r.siguen_en_espera} siguen en espera`); }
    } catch (e) { toast(errText(e), "bad"); }
  };
  const saveAdj = async () => {
    if (!adj) return;
    const body = { beneficiary: adj.beneficiary, user_id: adj.beneficiary === "vendedor" ? Number(adj.user_id) || null : null, ally_company_id: adj.beneficiary === "aliado" ? Number(adj.ally_company_id) || null : null, amount: adj.amount, currency: adj.currency, description: adj.description };
    try { await api("/commissions/adjustments", { method: "POST", json: body }); setAdj(null); done("Ajuste registrado: queda pendiente de aprobación"); }
    catch (e) { toast(errText(e), "bad"); }
  };
  const runBackfill = async (dry: boolean) => {
    if (!bf) return;
    setBusy(true);
    try {
      const r = await api<Backfill["result"] & { dry_run: boolean }>("/commissions/backfill", { method: "POST", json: { from: bf.from, dry_run: dry } });
      if (dry) setBf({ ...bf, result: r });
      else { setBf(null); done(`${r?.creados ?? 0} movimiento(s) creados con las reglas actuales`); }
    } catch (e) { toast(errText(e), "bad"); } finally { setBusy(false); }
  };

  const marcadas = (g: InboxGroup) => g.entries.filter((e) => sel[e.id]).map((e) => e.id);
  const aprobables = (g: InboxGroup) => g.entries.filter((e) => e.can_approve).map((e) => e.id);

  const group = (g: InboxGroup, stage: "aprobar" | "pagar") => {
    const k = `${stage}:${g.key}`;
    const ids = marcadas(g).length ? marcadas(g) : aprobables(g);
    return (
      <div className="com-group" key={k}>
        <div className="com-group__head" onClick={() => setOpen({ ...open, [k]: !open[k] })}>
          <b>{g.name} <span className="badge badge--muted">{g.beneficiary === "aliado" ? "Aliado" : "Vendedor"}</span>{g.has_adjustments && <span className="badge badge--warn" style={{ marginLeft: 6 }}>con ajustes</span>}</b>
          <span className="muted" style={{ fontSize: 13 }}>{g.count} · desde {fmtDate(g.oldest)}</span>
          <span className={`money ${Number(g.total_crc) < 0 ? "com-neg" : ""}`}>{fmtMoney(g.total_crc)}</span>
          <span onClick={(e) => e.stopPropagation()} style={{ display: "flex", gap: 6 }}>
            {stage === "aprobar" && <button className="btn btn--soft btn--sm" disabled={busy || !ids.length} onClick={() => patch(ids, "aprobada")}>{marcadas(g).length ? `Aprobar ${marcadas(g).length}` : "Aprobar todo"}</button>}
            <button className="btn btn--crimson btn--sm" disabled={busy || !ids.length} onClick={() => setPay({ ids, paid_on: hoy(), paid_ref: "" })}>Marcar pagado</button>
            <button className="btn btn--ghost btn--sm" disabled={busy || !ids.length} onClick={() => setAnular({ ids, reason: "" })}>Anular</button>
          </span>
        </div>
        {open[k] && (
          <div className="com-scroll">
            <table className="table">
              <thead><tr><th style={{ width: 32 }} /><th>Fecha</th><th>Tipo</th><th>Concepto</th><th>Proyecto</th><th className="num">Monto</th></tr></thead>
              <tbody>{g.entries.map((e) => (
                <tr key={e.id}>
                  <td>{e.can_approve ? <input type="checkbox" aria-label={`Elegir ${e.description || e.id}`} checked={!!sel[e.id]} onChange={(ev) => setSel({ ...sel, [e.id]: ev.target.checked })} /> : null}</td>
                  <td className="muted">{fmtDate(e.earned_on)}</td>
                  <td>{KIND[e.kind] || e.kind}</td>
                  <td>{e.description}{!e.can_approve && <div className="com-own">No podés aprobar tu propia comisión</div>}</td>
                  <td className="mono muted">{e.project || e.invoice || "—"}</td>
                  <td className={`num money ${Number(e.amount) < 0 ? "com-neg" : ""}`} style={{ fontWeight: 700 }}>{fmtMoney(e.amount, e.currency)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </div>
    );
  };

  if (!d) return <div style={{ padding: 40, textAlign: "center" }}><span className="spinner" /></div>;
  return (
    <>
      <div className="com-cards">
        <div className="com-card"><span className="meta">En espera de un dato</span><b>{d.totals.en_espera}</b></div>
        <div className="com-card is-warn"><span className="meta">Por aprobar</span><b className="money">{fmtMoney(d.totals.por_aprobar_crc)}</b></div>
        <div className="com-card"><span className="meta">Aprobado por pagar</span><b className="money">{fmtMoney(d.totals.por_pagar_crc)}</b></div>
      </div>
      {configura && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
          <button className="btn btn--ghost btn--sm" onClick={() => setAdj({ beneficiary: "vendedor", user_id: "", ally_company_id: "", amount: "", currency: "CRC", description: "" })}><Icon d={I.plus} />Ajuste manual</button>
          <button className="btn btn--ghost btn--sm" onClick={() => setBf({ from: hoy().slice(0, 8) + "01", result: null })}><Icon d={I.refresh} />Aplicar reglas a fechas anteriores</button>
        </div>
      )}
      <Card title={`En espera · ${d.en_espera.length}`} flush extra={d.en_espera.length ? <button className="btn btn--soft btn--sm" onClick={() => recalc()}>Recalcular todo</button> : undefined}>
        {d.en_espera.length === 0 ? <p className="muted" style={{ fontSize: 13, padding: "0 18px 14px" }}>Nada esperando un dato.</p> : (
          <div className="com-scroll"><table className="table">
            <thead><tr><th>Fecha</th><th>Quién</th><th>Concepto</th><th>Motivo</th><th /></tr></thead>
            <tbody>{d.en_espera.map((e) => (
              <tr key={e.id}><td className="muted">{fmtDate(e.earned_on)}</td><td style={{ fontWeight: 600 }}>{e.beneficiary_name || "—"}</td><td>{e.description}</td><td className="muted">{e.hold_reason}</td>
                <td className="num" style={{ whiteSpace: "nowrap" }}><button className="btn btn--soft btn--sm" onClick={() => recalc(e.id)}>Recalcular</button> {e.can_approve && <button className="btn btn--ghost btn--sm" onClick={() => setAnular({ ids: [e.id], reason: "" })}>Anular</button>}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
      <Card title={`Por aprobar · ${d.por_aprobar.reduce((a, g) => a + g.count, 0)}`} flush>
        {d.por_aprobar.length === 0 ? <Empty title="Nada por aprobar" hint="Las comisiones nacen solas con el cobro (o con el evento de su regla)." /> : d.por_aprobar.map((g) => group(g, "aprobar"))}
      </Card>
      <Card title={`Por pagar · ${d.por_pagar.reduce((a, g) => a + g.count, 0)}`} flush>
        {d.por_pagar.length === 0 ? <p className="muted" style={{ fontSize: 13, padding: "0 18px 14px" }}>Nada aprobado pendiente de pago.</p> : d.por_pagar.map((g) => group(g, "pagar"))}
      </Card>

      {pay && (
        <Modal title={`Marcar pagado · ${pay.ids.length}`} onClose={() => setPay(null)} foot={<><button className="btn btn--ghost" onClick={() => setPay(null)}>Cancelar</button><button className="btn btn--crimson" disabled={busy} onClick={() => patch(pay.ids, "pagada", { paid_on: pay.paid_on, paid_ref: pay.paid_ref || null })}>Marcar pagado</button></>}>
          <div className="grid-2">
            <Field label="Fecha de pago"><input className="input" type="date" value={pay.paid_on} onChange={(e) => setPay({ ...pay, paid_on: e.target.value })} /></Field>
            <Field label="Referencia" hint="Planilla oct-2026, transferencia 1234…"><input className="input" maxLength={120} value={pay.paid_ref} onChange={(e) => setPay({ ...pay, paid_ref: e.target.value })} /></Field>
          </div>
          <p className="com-note">No se crea un gasto: la factura del aliado entra por Recepción. Los ajustes negativos se registran como descontados.</p>
        </Modal>
      )}
      {anular && (
        <Modal title={`Anular · ${anular.ids.length}`} onClose={() => setAnular(null)} foot={<><button className="btn btn--ghost" onClick={() => setAnular(null)}>Cancelar</button><button className="btn btn--danger" disabled={busy || anular.reason.trim().length < 3} onClick={() => patch(anular.ids, "anulada", { reason: anular.reason })}>Anular</button></>}>
          <Field label="Motivo" hint="Queda en la bitácora."><input className="input" maxLength={240} value={anular.reason} onChange={(e) => setAnular({ ...anular, reason: e.target.value })} /></Field>
        </Modal>
      )}
      {adj && (
        <Modal title="Ajuste o bono manual" onClose={() => setAdj(null)} foot={<><button className="btn btn--ghost" onClick={() => setAdj(null)}>Cancelar</button><button className="btn btn--crimson" disabled={!adj.amount || Number(adj.amount) === 0 || adj.description.trim().length < 3} onClick={saveAdj}>Guardar</button></>}>
          <div className="grid-2">
            <Field label="Para"><select className="select" value={adj.beneficiary} onChange={(e) => setAdj({ ...adj, beneficiary: e.target.value as AdjForm["beneficiary"] })}><option value="vendedor">Una persona del equipo</option><option value="aliado">Una empresa aliada</option></select></Field>
            {adj.beneficiary === "vendedor"
              ? <Field label="Persona"><select className="select" value={adj.user_id} onChange={(e) => setAdj({ ...adj, user_id: e.target.value })}><option value="">Elegí…</option>{ben?.users.map((u) => <option key={u.id} value={u.id}>{u.name} · {u.role_label}</option>)}</select></Field>
              : <Field label="Aliado"><select className="select" value={adj.ally_company_id} onChange={(e) => setAdj({ ...adj, ally_company_id: e.target.value })}><option value="">Elegí…</option>{ben?.allies.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>}
            <Field label="Monto" hint="Negativo = descuento; positivo = bono."><input className="input input--mono" inputMode="decimal" value={adj.amount} onChange={(e) => setAdj({ ...adj, amount: e.target.value })} /></Field>
            <Field label="Moneda"><select className="select" value={adj.currency} onChange={(e) => setAdj({ ...adj, currency: e.target.value })}><option>CRC</option><option>USD</option></select></Field>
          </div>
          <Field label="Concepto"><input className="input" maxLength={240} value={adj.description} onChange={(e) => setAdj({ ...adj, description: e.target.value })} /></Field>
        </Modal>
      )}
      {bf && (
        <Modal title="Aplicar reglas a fechas anteriores" onClose={() => setBf(null)} foot={<><button className="btn btn--ghost" onClick={() => setBf(null)}>Cancelar</button>{bf.result ? <button className="btn btn--crimson" disabled={busy || !bf.result.creados} onClick={() => runBackfill(false)}>Confirmar y crear {bf.result.creados}</button> : <button className="btn btn--soft" disabled={busy} onClick={() => runBackfill(true)}>Simular</button>}</>}>
          <Field label="Desde" hint="Cobros, cotizaciones aceptadas y proyectos completados desde esta fecha (máximo un año). Lo ya devengado no se repite."><input className="input" type="date" value={bf.from} onChange={(e) => setBf({ from: e.target.value, result: null })} /></Field>
          {bf.result && (bf.result.creados === 0 ? <p className="muted">No hay nada nuevo que devengar con las reglas actuales.</p> : (
            <table className="table"><thead><tr><th>Quién</th><th className="num">Movimientos</th><th className="num">Total</th></tr></thead>
              <tbody>{bf.result.por_beneficiario.map((x) => <tr key={x.name}><td>{x.name}</td><td className="num">{x.count}</td><td className="num money">{fmtMoney(x.total_crc)}</td></tr>)}</tbody></table>
          ))}
        </Modal>
      )}
    </>
  );
}
