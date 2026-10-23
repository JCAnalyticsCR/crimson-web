import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtMoney, type DocListItem } from "../../lib/api";
import { Badge, Card, Empty, I, Icon, Loading } from "../../ui/components";
import { useSession } from "../../app/session";
import { TrashLocked } from "../../ui/TrashButton";
import { LOCKED } from "../../ui/trash";
import PorEmitir from "./PorEmitir";
import "./doc-list.css";

/* Filtros que llegan desde "Acciones pendientes" del inicio (?pendiente=<clave>): misma definicion que el conteo. */
const PENDIENTES: Record<string, string> = {
  facturas_vencidas: "Facturas vencidas (sin pagar y con la fecha de vencimiento pasada)",
  facturas_por_cobrar: "Facturas por cobrar (enviadas al cliente y con saldo)",
  documentos_rechazados: "Facturas rechazadas o con error en Hacienda",
  enlaces_abiertos: "Facturas con un enlace de pago abierto (vigente y sin pagar)",
  cotizaciones_sin_respuesta: "Cotizaciones sin respuesta (creadas o enviadas, sin convertir)",
  cotizaciones_por_vencer: "Cotizaciones sin respuesta que vencen en los próximos 7 días",
  cotizaciones_por_aprobar: "Cotizaciones con descuento por aprobar",
  cotizaciones_aceptadas: "Aceptadas por el cliente y sin convertir (ni a factura ni a proyecto)",
};

const FILTERS: Record<string, { key: string; label: string }[]> = {
  quotes: [{ key: "", label: "Todas" }, { key: "creado", label: "Creadas" }, { key: "por_aprobar", label: "Por aprobar" }, { key: "enviada", label: "Enviadas" }, { key: "convertida", label: "Convertidas" }, { key: "anulada", label: "Anuladas" }],
  invoices: [{ key: "", label: "Todas" }, { key: "creado", label: "Creadas" }, { key: "enviada", label: "Enviadas" }, { key: "parcial", label: "Parciales" }, { key: "pagada", label: "Pagadas" }, { key: "vencida", label: "Vencidas" }, { key: "anulada", label: "Anuladas" }, { key: "por_emitir", label: "Por emitir" }],
};

export default function DocList({ kind }: { kind: "quotes" | "invoices" }) {
  const isQ = kind === "quotes";
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const { allows } = useSession();
  const todo = allows("sales.ver_todo"); // sin ver_todo (el vendedor) la API solo devuelve lo suyo, también al buscar
  const [items, setItems] = useState<DocListItem[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [status, setStatus] = useState(params.get("estado") || "");
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0); // Refrescar en "Por emitir"
  const q = params.get("q") || "";
  const pendiente = params.get("pendiente") || "";
  /* "Por emitir" (plan 1.7) no es un estado de la factura: es la cola fiscal, con su propia lista y acciones.
     El correo diario de contabilidad abre /facturas?estado=por_emitir. */
  const porEmitir = !isQ && status === "por_emitir" && !q && !pendiente;
  const [text, setText] = useState(q);
  useEffect(() => { setText(q); }, [q]); // atrás/adelante del navegador

  /* El buscador vive en ?q= (se puede compartir o volver con "atrás"). Buscar quita los filtros del inicio y de estado:
     "la 004" se busca entre todas. */
  const search = useCallback((v: string) => {
    const next = new URLSearchParams(params);
    const t = v.trim();
    if (t) next.set("q", t); else next.delete("q");
    next.delete("pendiente"); next.delete("estado");
    if (next.toString() === params.toString()) return;
    setStatus("");
    setParams(next, { replace: true });
  }, [params, setParams]);
  useEffect(() => {
    if (text.trim() === q) return;
    const t = setTimeout(() => search(text), 300);
    return () => clearTimeout(t);
  }, [text, q, search]);

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
  useEffect(() => { if (!porEmitir) load(); }, [load, porEmitir]);

  const base = isQ ? "/cotizaciones" : "/facturas";
  const idx = isQ ? "02" : "03";
  return (
    <>
      <div className="page-head">
        <div><div className="meta">{idx} · Facturación</div><h1 className="h1">{isQ ? "Cotizaciones" : "Facturas"}</h1></div>
        <div className="page-head__actions">
          <button className="btn btn--ghost btn--sm" onClick={() => (porEmitir ? setTick((t) => t + 1) : load())}><Icon d={I.refresh} />Refrescar</button>
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
        <div className="doc-search">
          <div className="search">
            <Icon d={I.search} size={16} />
            <input type="search" aria-label="Buscar" placeholder={isQ ? "Buscar por número o cliente…" : "Buscar por número, cliente o clave…"} value={text}
              onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") search(text); }} />
          </div>
          {!todo && <p className="muted doc-search__scope">Solo ves tus {isQ ? "cotizaciones" : "facturas"}.</p>}
        </div>
        <div className="list-head doc-list__head">
          <span className="muted" style={{ fontSize: 13 }}>{q ? `Resultados para “${q}”` : "Modificadas recientemente"}</span>
          {!pendiente && <div className="tabs">{FILTERS[kind].map((f) => <button key={f.key} className={status === f.key ? "is-active" : ""} onClick={() => setStatus(f.key)}>{f.label}</button>)}</div>}
        </div>
        {porEmitir ? <PorEmitir key={tick} /> : items.length === 0 && loading ? <Loading /> : items.length === 0 ? (
          q
            /* sin sugerir que exista en otro lado: lo ajeno no existe para el vendedor (13/10 35:50) */
            ? <Empty title={`No encontramos «${q}» entre ${todo ? "las" : "sus"} ${isQ ? "cotizaciones" : "facturas"}.`} hint="Pruebe con otro número, el nombre del cliente o parte de él." />
            /* "Crear" solo a quien crea ventas: contabilidad envia y cobra pero no crea (plan 1.7) */
            : <Empty hint={isQ ? "Cree una cotización y envíela por WhatsApp o correo." : "Las facturas nacen de una cotización convertida o directo."} action={allows("sales.crear") ? <Link className="btn btn--crimson btn--sm" to={`${base}/nueva`}>Crear</Link> : undefined} />
        ) : (
          <table className="table">
            <thead><tr><th>Código</th><th>Cliente</th>{todo && <th>Vendedor</th>}<th>Fecha</th><th>Vence</th><th className="num">Monto</th>{!isQ && <th className="num">Saldo</th>}<th>Estado</th><th /></tr></thead>
            <tbody>{items.map((d) => (
              <tr key={d.id}>
                <td><Link className="row-link mono" to={`${base}/${d.id}`}>{d.number}</Link>{d.number_provisional && <> <span className="badge badge--warn" title="Número provisional: toma su número fiscal al emitir ante Hacienda">Provisional</span></>}</td>
                <td>{d.customer_name || <span className="muted">Sin cliente</span>}</td>
                {todo && <td className="muted">{d.seller_name || "—"}</td>}
                <td className="muted">{fmtDate(d.issue_date)}</td>
                <td className="muted">{fmtDate(d.due_date)}</td>
                <td className="num money">{fmtMoney(d.total, d.currency)}</td>
                {!isQ && <td className="num money">{fmtMoney(d.balance, d.currency)}</td>}
                <td><span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap" }}><Badge status={d.status} />{isQ && d.acceptance_status && d.acceptance_status !== "pendiente" && <Badge status={d.acceptance_status} />}</span></td>
                <td className="num row-actions"><span className="row-actions__in"><Link className="btn btn--ghost btn--sm" to={`${base}/${d.id}`}>Ver</Link><TrashLocked reason={isQ ? LOCKED.quote : LOCKED.invoice} /></span></td>
              </tr>
            ))}</tbody>
          </table>
        )}
        {!porEmitir && cursor && <div style={{ padding: 14, textAlign: "center" }}><button className="btn btn--soft btn--sm" onClick={() => load(true, cursor)} disabled={loading}>Más resultados</button></div>}
      </Card>
    </>
  );
}
