/* Detalle de un proyecto en común: estado, fechas, avance, la ejecución (órdenes de su equipo y de Crimson, con quién
   va a cada una: reunión 2, pedido 17), "Lo que recibo en este proyecto"
   (lo calcula economía) y sus precios. Sin precio de venta ni cotización: eso es entre Crimson y el cliente. */
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { Badge, I, Icon } from "../../ui/components";
import { AllyAgreements, AllyScopeList, RequestList } from "./AllyDeal";
import QuoteForm from "./QuoteForm";
import AllyExecution from "./AllyExecution";
import { errorText, useAlly, type ProjectFull, type Req } from "./shared";

export default function AllyProject() {
  const { id } = useParams();
  const { readOnly, can } = useAlly();
  const [p, setP] = useState<ProjectFull | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [resp, setResp] = useState<Req | null>(null);
  const [enviar, setEnviar] = useState(false);
  const load = useCallback(() => { api<ProjectFull>(`/aliado/proyectos/${id}`).then(setP).catch((e) => setErr(errorText(e))); }, [id]);
  useEffect(load, [load]);
  if (err) return <p className="muted">{err}</p>;
  if (!p) return <div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  const precios = can("precios") && !readOnly;
  const t = p.lo_que_recibo?.totales || null;
  const moneda = p.lo_que_recibo?.moneda || "CRC";
  return (
    <>
      <div className="cp-h">
        <div><div className="meta">{p.number}</div><h1 className="h1">{p.name}</h1></div>
        {precios && <button className="btn btn--soft" onClick={() => setEnviar(true)}><Icon d={I.plus} />Enviar mis precios</button>}
      </div>
      <div className="cp-grid cp-grid--2">
        <div className="cp-card">
          <dl className="al-kv">
            <dt>Estado</dt><dd><Badge status={p.status} /></dd>
            <dt>Le paga a Crimson</dt><dd>{p.customer || "—"}</dd>
            {p.end_customer && <><dt>Cliente final</dt><dd>{p.end_customer}</dd></>}
            <dt>Sitio</dt><dd>{p.site || "—"}</dd>
            <dt>Fechas</dt><dd>{p.start_date ? fmtDate(p.start_date) : "—"} → {p.end_date ? fmtDate(p.end_date) : "—"}</dd>
            <dt>Avance</dt><dd>{p.orders_done} de {p.orders_total} órdenes de su equipo</dd>
            <dt>Equipos instalados</dt><dd>{p.assets_count}</dd>
          </dl>
          {p.my_scope_lines.length > 0 && <><b style={{ display: "block", marginTop: 10 }}>Sus partidas</b><ul style={{ margin: "4px 0", paddingLeft: 18 }}>{p.my_scope_lines.map((l) => <li key={l}>{l}</li>)}</ul></>}
        </div>
        <div className="cp-card al-money">
          <b>Lo que recibo en este proyecto</b>
          {t ? (
            <dl className="al-kv" style={{ marginTop: 8 }}>
              <dt>Recibido</dt><dd><b>{fmtMoney(t.recibido as string, moneda)}</b></dd>
              <dt>Por recibir</dt><dd><b>{fmtMoney(t.por_recibir as string, moneda)}</b></dd>
              <dt>Proyectado</dt><dd>{fmtMoney(t.proyectado as string, moneda)}</dd>
            </dl>
          ) : <p className="muted">Disponible pronto.</p>}
          <span className="meta">Montos sin IVA.</span>
        </div>
      </div>
      {/* reunión 2, pedido 17: la ejecución completa (su equipo y Crimson), no solo sus órdenes */}
      <h2 className="h3 al-sec">Ejecución del proyecto</h2>
      {p.ejecucion ? <AllyExecution e={p.ejecucion} /> : <p className="muted">Crimson todavía no programó órdenes en este proyecto.</p>}
      <h2 className="h3 al-sec">Alcances y responsables</h2>
      <AllyScopeList scope={p.alcances} readOnly={readOnly} onChange={(a) => setP({ ...p, alcances: a })} />
      <h2 className="h3 al-sec">Acuerdos</h2>
      <AllyAgreements deals={p.acuerdos} />
      <h2 className="h3 al-sec">Mis precios</h2>
      <RequestList rows={p.requests} onRespond={precios ? setResp : undefined} />
      {resp && <div style={{ marginTop: 12 }}><QuoteForm path={`/aliado/solicitudes/${resp.id}`} method="PUT" onCancel={() => setResp(null)} onDone={() => { setResp(null); load(); }} /></div>}
      {enviar && <div style={{ marginTop: 12 }}><QuoteForm path={`/aliado/proyectos/${p.id}/precios`} method="POST" ask roles={p.my_participations} onCancel={() => setEnviar(false)} onDone={() => { setEnviar(false); load(); }} /></div>}
    </>
  );
}
