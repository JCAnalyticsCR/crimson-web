/* Proyectos: la cotizacion aprobada convertida en ejecucion.
   Aqui se responde la pregunta que hoy es dificil de contestar: cuanto dejo realmente cada instalacion. */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, fmtDate, fmtMoney, openFile } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Card, Empty, Field, I, Icon, Modal, Loading } from "../../ui/components";
import { ArchiveActions } from "../../ui/ArchiveActions";
import { ALLY_ROLES, CONTACT_FUNCS, partyLine, type Ally } from "../crm/OpportunityAllies";

type Eco = {
  price: string; cost_planned: string; cost_materials: string; cost_labor: string; cost_travel: string; cost_extra: string;
  cost_real: string; profit: string | null; margin_real: number | null; margin_planned: number; hours: number;
  sin_costos?: boolean; provisional?: boolean; aviso?: string | null;
};
type Project = {
  id: number; number: string; name: string; status: string; customer_id: number | null; customer: string | null; site: string | null;
  scope: string | null; supervisor: string | null; start_date: string | null; end_date: string | null; quote_id: number | null;
  survey_id: number | null; invoice_id: number | null; notes: string | null; delivered_at: string | null;
  orders_total: number; orders_done: number; created_at: string; economics?: Eco; archived_at?: string | null; trashed_at?: string | null;
  orders?: { id: number; number: string; title: string; status: string; scheduled_at: string | null; technician_id: number | null; materials: number }[];
  assets?: { id: number; name: string; serial: string | null; location: string | null; warranty_until: string | null }[];
  end_customer?: string | null; opportunity_id?: number | null; allies?: Ally[];
};
type Requirement = { product_id: number; name: string; planned: string; used: string; stock: string; pending: string; to_buy: string };

export const PROJECT_STATUS: Record<string, { label: string; tone: string }> = {
  planificado: { label: "Planificado", tone: "info" }, en_curso: { label: "En curso", tone: "warn" }, pausado: { label: "Pausado", tone: "muted" },
  terminado: { label: "Terminado", tone: "ok" }, entregado: { label: "Entregado", tone: "ok" }, facturado: { label: "Facturado", tone: "ok" },
  cancelado: { label: "Cancelado", tone: "muted" },
};

export default function Projects() {
  const { id } = useParams();
  return id ? <Detail id={Number(id)} /> : <List />;
}

function List() {
  const { allows } = useSession();
  const [rowsSt, setRows] = useState<Project[] | null>(null); // null = cargando
  const rows = rowsSt ?? [];
  const [status, setStatus] = useState("activos");
  const verPlata = allows("catalog.costos");

  useEffect(() => { api<Project[]>(`/projects${status ? `?status=${status}` : ""}`).then(setRows); }, [status]);

  return (
    <>
      <div className="page-head">
        <div><div className="meta">09 · Operación</div><h1 className="h1">Proyectos</h1></div>
        <div className="page-head__actions">
          <select className="select" value={status} onChange={(e) => setStatus(e.target.value)} style={{ maxWidth: 200 }}>
            <option value="activos">Activos</option><option value="">Todos</option>
            {Object.entries(PROJECT_STATUS).map(([k, s]) => <option key={k} value={k}>{s.label}</option>)}
          </select>
        </div>
      </div>
      <Card flush>
        {rowsSt === null ? <Loading /> : rows.length === 0 ? <Empty title="Sin proyectos" hint="Una cotización aprobada se convierte en proyecto con un clic, desde la cotización." /> : (
          <table className="table">
            <thead><tr><th>Número</th><th>Proyecto</th><th>Cliente</th><th>Órdenes</th>{verPlata && <><th className="num">Venta</th><th className="num">Margen real</th></>}<th>Estado</th><th /></tr></thead>
            <tbody>{rows.map((p) => (
              <tr key={p.id}>
                <td className="mono muted">{p.number}</td><td style={{ fontWeight: 600 }}>{p.name}</td><td className="muted">{p.customer || "—"}</td>
                <td className="mono muted">{p.orders_done}/{p.orders_total}</td>
                {verPlata && <><td className="num money">{fmtMoney(p.economics?.price)}</td><td className="num mono" title={p.economics?.aviso || undefined} style={{ color: p.economics?.margin_real == null || p.economics.provisional ? "var(--text-3)" : p.economics.margin_real < 15 ? "var(--bad)" : "var(--ok)" }}>{!p.economics ? "—" : p.economics.margin_real == null ? `${p.economics.margin_planned.toFixed(1)}% cotizado` : `${p.economics.margin_real.toFixed(1)}%${p.economics.provisional ? " (provisional)" : ""}`}</td></>}
                <td><span className={`badge badge--${PROJECT_STATUS[p.status]?.tone || "muted"}`}>{PROJECT_STATUS[p.status]?.label || p.status}</span></td>
                <td className="num"><Link className="btn btn--ghost btn--sm" to={`/proyectos/${p.id}`}>Abrir</Link></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
    </>
  );
}

function Detail({ id }: { id: number }) {
  const { toast, allows } = useSession();
  const nav = useNavigate();
  const [p, setP] = useState<Project | null>(null);
  const [reqSt, setReq] = useState<Requirement[] | null>(null); // null = cargando
  const req = reqSt ?? [];
  const [edit, setEdit] = useState<{ status: string; cost_labor: string; cost_travel: string; cost_extra: string; end_date: string; notes: string } | null>(null);
  const verPlata = allows("catalog.costos");

  const load = useCallback(() => {
    api<Project>(`/projects/${id}`).then(setP);
    api<Requirement[]>(`/projects/${id}/requirements`).then(setReq).catch(() => setReq([]));
  }, [id]);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!p || !edit) return;
    try {
      await api(`/projects/${p.id}`, { method: "PUT", json: {
        name: p.name, customer_id: p.customer_id, site: p.site, scope: p.scope, start_date: p.start_date, end_date: edit.end_date || null,
        status: edit.status, cost_labor: Number(edit.cost_labor || 0), cost_travel: Number(edit.cost_travel || 0), cost_extra: Number(edit.cost_extra || 0), notes: edit.notes || null,
      } });
      toast("Proyecto actualizado"); setEdit(null); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const pedirMateriales = async () => {
    try { const r = await api<{ number: string }>(`/projects/${id}/purchase-request`, { method: "POST" }); toast(`Solicitud de compra ${r.number} creada`); nav("/compras"); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const facturar = async () => {
    try { const inv = await api<{ invoice_id: number; number: string }>(`/projects/${id}/invoice`, { method: "POST" }); toast(`Factura ${inv.number} creada`); nav(`/facturas/${inv.invoice_id}`); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  if (!p) return <div style={{ padding: 60, textAlign: "center" }}><span className="spinner" /></div>;
  const e = p.economics;
  const faltantes = req.filter((r) => Number(r.to_buy) > 0);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="meta">09 · Operación · <Link to="/proyectos">Proyectos</Link></div>
          <h1 className="h1">{p.number} · {p.name}</h1>
          <p className="muted" style={{ fontSize: 13.5, margin: "6px 0 0" }}>
            {partyLine(p)} · <span className={`badge badge--${PROJECT_STATUS[p.status]?.tone || "muted"}`}>{PROJECT_STATUS[p.status]?.label || p.status}</span>
          </p>
        </div>
        <div className="page-head__actions">
          <ArchiveActions kind="project" id={p.id} number={p.number} archivedAt={p.archived_at} trashedAt={p.trashed_at} onDone={() => (p.archived_at || p.trashed_at ? load() : nav("/proyectos"))} />
          <button className="btn btn--ghost btn--sm" onClick={() => openFile(`/projects/${p.id}/report`)}><Icon d={I.reports} />Informe de entrega</button>
          {allows("projects.editar") && <button className="btn btn--ghost btn--sm" onClick={() => setEdit({ status: p.status, cost_labor: String(e?.cost_labor ?? 0), cost_travel: String(e?.cost_travel ?? 0), cost_extra: String(e?.cost_extra ?? 0), end_date: p.end_date || "", notes: p.notes || "" })}>Editar</button>}
          {allows("sales.crear") && !p.invoice_id && p.status !== "facturado" && <button className="btn btn--crimson btn--sm" onClick={facturar}>Facturar</button>}
        </div>
      </div>

      {e && verPlata && (
        <div className="eco">
          <div className="eco__box"><span className="meta">Venta</span><b className="money">{fmtMoney(e.price)}</b></div>
          {/* sin costos o sin cerrar: el real es provisional y manda el cotizado (nunca 100 % como definitivo) */}
          <div className="eco__box"><span className="meta">Costo real{e.provisional ? " · provisional" : ""}</span><b className="money">{fmtMoney(e.cost_real)}</b></div>
          <div className={`eco__box ${e.profit == null || e.provisional ? "" : Number(e.profit) >= 0 ? "is-good" : "is-bad"}`}><span className="meta">Utilidad{e.provisional ? " · provisional" : ""}</span><b className="money">{e.profit == null ? "—" : fmtMoney(e.profit)}</b></div>
          <div className="eco__box"><span className="meta">Margen presupuestado</span><b>{e.margin_planned.toFixed(1)}%</b></div>
          <div className={`eco__box ${e.margin_real == null || e.provisional ? "" : e.margin_real >= e.margin_planned - 5 ? "is-good" : "is-bad"}`}><span className="meta">Margen real{e.provisional ? " · provisional" : ""}</span><b>{e.margin_real == null ? "—" : `${e.margin_real.toFixed(1)}%`}</b>{e.aviso && <small className="muted" style={{ display: "block", fontSize: 12 }}>{e.aviso}</small>}</div>
          <div className="eco__box"><span className="meta">Horas de campo</span><b>{e.hours}</b></div>
        </div>
      )}

      <div className="grid-2">
        <Card title={`Órdenes de trabajo · ${p.orders_done}/${p.orders_total}`} flush extra={<Link className="btn btn--ghost btn--sm" to="/ordenes-trabajo">Ver todas</Link>}>
          {(p.orders || []).length === 0 ? <p className="muted" style={{ fontSize: 13, padding: "0 18px 14px" }}>Sin órdenes.</p> : (
            <table className="table">
              <thead><tr><th>Número</th><th>Trabajo</th><th>Estado</th><th /></tr></thead>
              <tbody>{(p.orders || []).map((o) => (
                <tr key={o.id}><td className="mono muted">{o.number}</td><td>{o.title}</td><td><Badge status={o.status} /></td>
                  <td className="num"><Link className="btn btn--ghost btn--sm" to={`/ordenes-trabajo?id=${o.id}`}>Abrir</Link></td></tr>
              ))}</tbody>
            </table>
          )}
        </Card>

        <Card title="Equipos instalados" flush extra={<Link className="btn btn--ghost btn--sm" to={`/activos?proyecto=${p.id}`}>Ver activos</Link>}>
          {(p.assets || []).length === 0 ? <p className="muted" style={{ fontSize: 13, padding: "0 18px 14px" }}>Todavía no se registraron equipos del cliente.</p> : (
            <table className="table">
              <thead><tr><th>Equipo</th><th>Serie</th><th>Ubicación</th><th>Garantía</th></tr></thead>
              <tbody>{(p.assets || []).map((a) => (
                <tr key={a.id}><td style={{ fontWeight: 600 }}>{a.name}</td><td className="mono muted">{a.serial || "—"}</td><td className="muted">{a.location || "—"}</td><td>{fmtDate(a.warranty_until)}</td></tr>
              ))}</tbody>
            </table>
          )}
        </Card>
      </div>

      {(p.allies || []).length > 0 && (
        /* aliados heredados de la oportunidad: quién participa y a quién llamar (los costos viven en la oportunidad) */
        <Card title="Aliados" extra={p.opportunity_id && allows("crm_pipeline.ver") ? <Link className="btn btn--ghost btn--sm" to={`/oportunidades?id=${p.opportunity_id}`}>Ver oportunidad</Link> : undefined}>
          <div className="prj-allies">
            {(p.allies || []).map((a) => (
              <div key={a.id} className="prj-ally">
                <b>{a.name}</b> <span className="badge badge--muted">{ALLY_ROLES[a.role] || a.role}</span>
                {a.scope && <p className="muted" style={{ margin: "4px 0 0", fontSize: 13 }}>{a.scope}</p>}
                {Object.entries(a.contacts || {}).map(([k, c]) => <div key={k} className="meta" style={{ overflowWrap: "anywhere" }}>{CONTACT_FUNCS[k] || k}: {[c.name, c.phone, c.email].filter(Boolean).join(" · ")}</div>)}
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card
        title="Materiales: planificado contra bodega"
        flush
        extra={faltantes.length > 0 && allows("purchases.crear") ? <button className="btn btn--crimson btn--sm" onClick={pedirMateriales}><Icon d={I.box} />Solicitar {faltantes.length} faltante{faltantes.length !== 1 ? "s" : ""}</button> : undefined}
      >
        {reqSt === null ? <Loading /> : req.length === 0 ? <p className="muted" style={{ fontSize: 13, padding: "0 18px 14px" }}>Sin materiales planificados en las órdenes.</p> : (
          <table className="table">
            <thead><tr><th>Material</th><th className="num">Planificado</th><th className="num">Usado</th><th className="num">En bodega</th><th className="num">Hay que comprar</th></tr></thead>
            <tbody>{req.map((r) => (
              <tr key={r.product_id}>
                <td style={{ fontWeight: 600 }}>{r.name}</td><td className="num mono">{Number(r.planned)}</td><td className="num mono">{Number(r.used)}</td>
                <td className="num mono">{Number(r.stock)}</td>
                <td className="num mono" style={{ color: Number(r.to_buy) > 0 ? "var(--bad)" : "var(--ok)" }}>{Number(r.to_buy) || "—"}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>

      {p.notes && <Card title="Notas"><pre style={{ whiteSpace: "pre-wrap", fontSize: 13, margin: 0, fontFamily: "inherit", color: "var(--text-2)" }}>{p.notes}</pre></Card>}

      {edit && (
        <Modal title="Editar proyecto" onClose={() => setEdit(null)} foot={<><button className="btn btn--ghost" onClick={() => setEdit(null)}>Cancelar</button><button className="btn btn--crimson" onClick={save}>Guardar</button></>}>
          <div className="grid-2">
            <Field label="Estado"><select className="select" value={edit.status} onChange={(ev) => setEdit({ ...edit, status: ev.target.value })}>{Object.entries(PROJECT_STATUS).map(([k, s]) => <option key={k} value={k}>{s.label}</option>)}</select></Field>
            <Field label="Fecha de cierre"><input className="input" type="date" value={edit.end_date} onChange={(ev) => setEdit({ ...edit, end_date: ev.target.value })} /></Field>
          </div>
          {verPlata && <>
            <p className="muted" style={{ fontSize: 13 }}>Los equipos se costean solos con lo que descontaron las órdenes. Aquí van los costos que no pasan por bodega.</p>
            <div className="grid-3">
              <Field label="Mano de obra"><input className="input input--mono" inputMode="decimal" value={edit.cost_labor} onChange={(ev) => setEdit({ ...edit, cost_labor: ev.target.value })} /></Field>
              <Field label="Viáticos y transporte"><input className="input input--mono" inputMode="decimal" value={edit.cost_travel} onChange={(ev) => setEdit({ ...edit, cost_travel: ev.target.value })} /></Field>
              <Field label="Otros costos"><input className="input input--mono" inputMode="decimal" value={edit.cost_extra} onChange={(ev) => setEdit({ ...edit, cost_extra: ev.target.value })} /></Field>
            </div>
          </>}
          <Field label="Notas"><textarea className="textarea" value={edit.notes} onChange={(ev) => setEdit({ ...edit, notes: ev.target.value })} /></Field>
        </Modal>
      )}
    </>
  );
}
