import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtMoney, type DocListItem } from "../../lib/api";
import { Badge, Card, Empty, I, Icon, Loading } from "../../ui/components";
import { useSession } from "../../app/session";

/* Filtros que llegan desde "Acciones pendientes" del inicio (?pendiente=<clave>): misma definicion que el conteo. */
const PENDIENTES: Record<string, string> = {
  facturas_vencidas: "Facturas vencidas (sin pagar y con la fecha de vencimiento pasada)",
  facturas_por_cobrar: "Facturas por cobrar (creadas o con pago parcial)",
  documentos_rechazados: "Facturas rechazadas por Hacienda",
  enlaces_abiertos: "Facturas con un enlace de pago abierto (vigente y sin pagar)",
  cotizaciones_sin_respuesta: "Cotizaciones sin respuesta (creadas o enviadas, sin convertir)",
  cotizaciones_por_aprobar: "Cotizaciones con descuento por aprobar",
};

const FILTERS: Record<string, { key: string; label: string }[]> = {
  quotes: [{ key: "", label: "Todas" }, { key: "creado", label: "Creadas" }, { key: "por_aprobar", label: "Por aprobar" }, { key: "enviada", label: "Enviadas" }, { key: "convertida", label: "Convertidas" }, { key: "anulada", label: "Anuladas" }],
  invoices: [{ key: "", label: "Todas" }, { key: "creado", label: "Creadas" }, { key: "parcial", label: "Parciales" }, { key: "pagada", label: "Pagadas" }, { key: "vencida", label: "Vencidas" }, { key: "anulada", label: "Anuladas" }],
};

export default function DocList({ kind }: { kind: "quotes" | "invoices" }) {
  const isQ = kind === "quotes";
  const [params] = useSearchParams();
  const nav = useNavigate();
  const { allows } = useSession();
  const [items, setItems] = useState<DocListItem[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [status, setStatus] = useState(params.get("estado") || "");
  const [loading, setLoading] = useState(true);
  const q = params.get("q") || "";
  const pendiente = params.get("pendiente") || "";

  const load = useCallback(async (append = false, c: number | null = null) => {
    setLoading(true);
    // desde el inicio se muestran todas de una vez: el numero de la tarjeta tiene que verse en la lista
    const qs = new URLSearchParams({ limit: pendiente ? "100" : "20" });
    if (pendiente) qs.set("pendiente", pendiente);
    else if (status) qs.set("status", status);
    if (q) qs.set("q", q);
    if (c) qs.set("cursor", String(c));
    const r = await api<{ items: DocListItem[]; next_cursor: number | null }>(`/${kind}?${qs}`);
    setItems((prev) => (append ? [...prev, ...r.items] : r.items));
    setCursor(r.next_cursor);
    setLoading(false);
  }, [kind, status, q, pendiente]);
  useEffect(() => { load(); }, [load]);

  const base = isQ ? "/cotizaciones" : "/facturas";
  const idx = isQ ? "02" : "03";
  return (
    <>
      <div className="page-head">
        <div><div className="meta">{idx} · Facturación</div><h1 className="h1">{isQ ? "Cotizaciones" : "Facturas"}</h1></div>
        <div className="page-head__actions">
          <button className="btn btn--ghost btn--sm" onClick={() => load()}><Icon d={I.refresh} />Refrescar</button>
          {allows("sales.crear") && <button className="btn btn--crimson" onClick={() => nav(`${base}/nueva`)}><Icon d={I.plus} />{isQ ? "Nueva cotización" : "Nueva factura"}</button>}
        </div>
      </div>
      <Card flush>
        {pendiente && (
          <div className="list-filter" role="status">
            <span>Desde el inicio: <b>{PENDIENTES[pendiente] || pendiente}</b>{!loading && ` · ${items.length}${cursor ? "+" : ""} ${items.length === 1 ? "registro" : "registros"}`}</span>
            <Link className="btn btn--ghost btn--sm" to={base}>Quitar filtro</Link>
          </div>
        )}
        <div className="list-head">
          <span className="muted" style={{ fontSize: 13 }}>{q ? `Resultados para “${q}”` : "Modificadas recientemente"}</span>
          {!pendiente && <div className="tabs">{FILTERS[kind].map((f) => <button key={f.key} className={status === f.key ? "is-active" : ""} onClick={() => setStatus(f.key)}>{f.label}</button>)}</div>}
        </div>
        {items.length === 0 && loading ? <Loading /> : items.length === 0 ? (
          <Empty hint={isQ ? "Creá una cotización y enviala por WhatsApp o correo." : "Las facturas nacen de una cotización convertida o directo."} action={<Link className="btn btn--crimson btn--sm" to={`${base}/nueva`}>Crear</Link>} />
        ) : (
          <table className="table">
            <thead><tr><th>Código</th><th>Cliente</th><th>Fecha</th><th>Vence</th><th className="num">Monto</th>{!isQ && <th className="num">Saldo</th>}<th>Estado</th><th /></tr></thead>
            <tbody>{items.map((d) => (
              <tr key={d.id}>
                <td><Link className="row-link mono" to={`${base}/${d.id}`}>{d.number}</Link></td>
                <td>{d.customer_name || <span className="muted">Sin cliente</span>}</td>
                <td className="muted">{fmtDate(d.issue_date)}</td>
                <td className="muted">{fmtDate(d.due_date)}</td>
                <td className="num money">{fmtMoney(d.total, d.currency)}</td>
                {!isQ && <td className="num money">{fmtMoney(d.balance, d.currency)}</td>}
                <td><Badge status={d.status} /></td>
                <td className="num"><Link className="btn btn--ghost btn--sm" to={`${base}/${d.id}`}>Ver</Link></td>
              </tr>
            ))}</tbody>
          </table>
        )}
        {cursor && <div style={{ padding: 14, textAlign: "center" }}><button className="btn btn--soft btn--sm" onClick={() => load(true, cursor)} disabled={loading}>Más resultados</button></div>}
      </Card>
    </>
  );
}
