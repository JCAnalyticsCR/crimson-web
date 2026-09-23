/* Comisiones. Andres: "la mayoria de mis negocios se van a basar en comisionar, en tercerizar el brete
   para yo comisionar sobre proyectos de otras personas". Se devengan con el COBRO, no con la factura:
   es la unica plata que existe de verdad. */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";

type Row = {
  id: number; user_id: number; user: string | null; invoice: string | null; invoice_id: number | null;
  base: string; base_amount: string; percent: string; amount: string; currency: string; earned_on: string; status: string; paid_on: string | null;
};
type Rule = { id: number; user_id: number | null; user: string; name: string; base: string; percent: string; active: boolean; notes: string | null };
type Agent = { id: number; name: string };

const ESTADO: Record<string, { label: string; tone: string }> = {
  pendiente: { label: "Pendiente", tone: "warn" }, aprobada: { label: "Aprobada", tone: "info" },
  pagada: { label: "Pagada", tone: "ok" }, anulada: { label: "Anulada", tone: "muted" },
};
const blankRule = { user_id: "", name: "Comisión", base: "venta", percent: "5", active: true, notes: "" };

export default function Commissions() {
  const { toast, allows } = useSession();
  const [data, setData] = useState<{ rows: Row[]; totals: Record<string, string> } | null>(null);
  const [rules, setRules] = useState<Rule[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [status, setStatus] = useState("");
  const [sel, setSel] = useState<Record<number, boolean>>({});
  const [rule, setRule] = useState<(typeof blankRule & { id?: number }) | null>(null);
  const puedeAprobar = allows("commissions.aprobar");
  const configura = allows("commissions.configurar");

  const load = useCallback(() => {
    api<{ rows: Row[]; totals: Record<string, string> }>(`/commissions${status ? `?status=${status}` : ""}`).then((r) => { setData(r); setSel({}); });
  }, [status]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (configura || puedeAprobar) api<Rule[]>("/commissions/rules").then(setRules).catch(() => setRules([]));
    api<{ agents: Agent[] }>("/tickets/meta/config").then((m) => setAgents(m.agents)).catch(() => setAgents([]));
  }, [configura, puedeAprobar]);

  const marcadas = Object.entries(sel).filter(([, v]) => v).map(([k]) => Number(k));

  const mover = async (st: string) => {
    if (!marcadas.length) return;
    try { await api("/commissions", { method: "PATCH", json: { ids: marcadas, status: st } }); toast(`${marcadas.length} comisión(es) ${st}s`); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const saveRule = async () => {
    if (!rule) return;
    const body = { user_id: rule.user_id ? Number(rule.user_id) : null, name: rule.name, base: rule.base, percent: Number(rule.percent || 0), active: rule.active, notes: rule.notes || null };
    try { await api(rule.id ? `/commissions/rules/${rule.id}` : "/commissions/rules", { method: rule.id ? "PUT" : "POST", json: body }); toast("Regla guardada"); setRule(null); api<Rule[]>("/commissions/rules").then(setRules); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  return (
    <>
      <div className="page-head">
        <div><div className="meta">14 · Administración</div><h1 className="h1">Comisiones</h1></div>
        <div className="page-head__actions">
          <select className="select select--sm" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Todas</option>{Object.entries(ESTADO).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select>
          {configura && <button className="btn btn--ghost btn--sm" onClick={() => setRule({ ...blankRule })}><Icon d={I.plus} />Nueva regla</button>}
        </div>
      </div>

      {data && (
        <div className="eco">
          <div className="eco__box is-bad"><span className="meta">Por aprobar</span><b className="money">{fmtMoney(data.totals.pendiente)}</b></div>
          <div className="eco__box"><span className="meta">Aprobadas sin pagar</span><b className="money">{fmtMoney(data.totals.aprobada)}</b></div>
          <div className="eco__box is-good"><span className="meta">Pagadas</span><b className="money">{fmtMoney(data.totals.pagada)}</b></div>
          <div className="eco__box"><span className="meta">Movimientos</span><b>{data.rows.length}</b></div>
        </div>
      )}

      {(configura || puedeAprobar) && (
        <Card title="Reglas" extra={<span className="meta">la del vendedor manda sobre la general</span>} flush>
          {rules.length === 0 ? <p className="muted" style={{ fontSize: 13, padding: "0 18px 14px" }}>Sin reglas: nadie comisiona todavía.</p> : (
            <table className="table">
              <thead><tr><th>Quién</th><th>Regla</th><th>Base</th><th className="num">%</th><th>Activa</th><th /></tr></thead>
              <tbody>{rules.map((r) => (
                <tr key={r.id}>
                  <td style={{ fontWeight: 600 }}>{r.user}</td><td>{r.name}</td>
                  <td className="muted">{r.base === "margen" ? "Sobre la utilidad" : "Sobre lo cobrado"}</td>
                  <td className="num mono">{Number(r.percent)} %</td>
                  <td>{r.active ? <span className="badge badge--ok">Sí</span> : <span className="badge badge--muted">No</span>}</td>
                  <td className="num">{configura && <button className="btn btn--ghost btn--sm" onClick={() => setRule({ id: r.id, user_id: r.user_id ? String(r.user_id) : "", name: r.name, base: r.base, percent: String(r.percent), active: r.active, notes: r.notes || "" })}>Editar</button>}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </Card>
      )}

      <Card
        title="Comisiones devengadas"
        flush
        extra={puedeAprobar && marcadas.length > 0 ? (
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--soft btn--sm" onClick={() => mover("aprobada")}>Aprobar {marcadas.length}</button>
            <button className="btn btn--crimson btn--sm" onClick={() => mover("pagada")}>Marcar pagadas</button>
          </div>
        ) : undefined}
      >
        {!data ? <div style={{ padding: 24, textAlign: "center" }}><span className="spinner" /></div> : data.rows.length === 0 ? (
          <Empty title="Todavía no hay comisiones" hint="Se generan solas cuando entra el pago de una factura, no al facturar." />
        ) : (
          <table className="table">
            <thead><tr>{puedeAprobar && <th style={{ width: 32 }} />}<th>Fecha</th><th>Quién</th><th>Factura</th><th>Base</th><th className="num">Sobre</th><th className="num">%</th><th className="num">Comisión</th><th>Estado</th></tr></thead>
            <tbody>{data.rows.map((r) => (
              <tr key={r.id}>
                {puedeAprobar && <td>{r.status === "pendiente" || r.status === "aprobada" ? <input type="checkbox" checked={!!sel[r.id]} onChange={(e) => setSel({ ...sel, [r.id]: e.target.checked })} /> : null}</td>}
                <td className="muted">{fmtDate(r.earned_on)}</td>
                <td style={{ fontWeight: 600 }}>{r.user || "—"}</td>
                <td className="mono muted">{r.invoice_id ? <Link className="row-link" to={`/facturas/${r.invoice_id}`}>{r.invoice}</Link> : "—"}</td>
                <td className="muted">{r.base === "margen" ? "Utilidad" : "Cobrado"}</td>
                <td className="num money">{fmtMoney(r.base_amount, r.currency)}</td>
                <td className="num mono">{Number(r.percent)} %</td>
                <td className="num money" style={{ fontWeight: 700 }}>{fmtMoney(r.amount, r.currency)}</td>
                <td><span className={`badge badge--${ESTADO[r.status]?.tone || "muted"}`}>{ESTADO[r.status]?.label || r.status}</span></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>

      {rule && (
        <Modal title={rule.id ? "Editar regla" : "Nueva regla de comisión"} onClose={() => setRule(null)} foot={<>
          <button className="btn btn--ghost" onClick={() => setRule(null)}>Cancelar</button>
          <button className="btn btn--crimson" onClick={saveRule}>Guardar</button>
        </>}>
          <div className="grid-2">
            <Field label="Quién comisiona" hint="Vacío = regla general para toda la empresa."><select className="select" value={rule.user_id} onChange={(e) => setRule({ ...rule, user_id: e.target.value })}><option value="">Todos</option>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
            <Field label="Nombre"><input className="input" value={rule.name} onChange={(e) => setRule({ ...rule, name: e.target.value })} /></Field>
            <Field label="Sobre qué" hint="Sobre la utilidad se calcula con el costo del producto; sin costo registrado no comisiona."><select className="select" value={rule.base} onChange={(e) => setRule({ ...rule, base: e.target.value })}><option value="venta">Lo cobrado</option><option value="margen">La utilidad</option></select></Field>
            <Field label="Porcentaje"><input className="input input--mono" inputMode="decimal" value={rule.percent} onChange={(e) => setRule({ ...rule, percent: e.target.value })} /></Field>
          </div>
          <Field label="Notas"><input className="input" value={rule.notes} onChange={(e) => setRule({ ...rule, notes: e.target.value })} /></Field>
        </Modal>
      )}
    </>
  );
}
