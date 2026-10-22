/* Pedidos de la tienda en linea: filtros, detalle, comprobante privado, confirmar pago, estados con aviso al cliente,
   cancelar con motivo, reembolso (registro), factura electronica e impresion. "Otros canales" (POS, enlaces) queda
   con el flujo de siempre. Los saltos de estado los valida el API; aqui solo se ofrecen los que acepta. */
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtMoney, openFile } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Card, Empty, Field, I, Icon, Loading, Modal } from "../../ui/components";
import { TrashLocked } from "../../ui/TrashButton";
import { LOCKED } from "../../ui/trash";
import "./orders.css";

/* note = interna (solo el equipo); client_note = la ve el cliente en su pagina de seguimiento (reunion 21/10, item 36) */
type Hist = { status: string; at: string; by: number | string | null; note?: string; client_note?: string; kind?: string };
type Proof = { media_id: number; filename: string; size: number; content_type: string; at: string };
type Mail = { to: string; subject: string; status: string; error: string | null; at: string };
type Order = {
  id: number; number: string; channel: string; customer_id: number | null; customer_name?: string | null; contact: Record<string, string>; currency: string;
  subtotal: string; discount_total: string; shipping: string; tax_total: string; total: string; coupon_code: string | null; shipping_method: string | null;
  payment_method: string | null; payment_kind: string | null; status: string; status_label: string; invoice_id: number | null; notes: string | null; created_at: string;
  delivery: { type?: string; province?: string; canton?: string; district?: string; address?: string } | null; paid_at: string | null; payment_ref: string | null;
  tracking_number: string | null; carrier: string | null; cancel_reason: string | null; refund: { amount: string; note?: string } | null; history: Hist[];
  proofs: Proof[]; expires_at: string | null; lines: { id: number; name: string; quantity: string; unit_price: string; total: string }[];
  emails?: Mail[]; next?: string[]; track_url?: string | null; email_result?: { to: string; status: string; error: string | null } | null;
  onvo_refundable?: boolean;
  stock_out?: string | number;
  /* la factura del pedido es del que mira (services/ownership.py); si no, se muestra el numero sin enlace */
  invoice_visible?: boolean | null;
};
type List = { items: Order[]; counts: Record<string, number>; por_atender: number; labels: Record<string, string>; comprobantes_facturas?: number | null };
/* comprobante que un cliente subio desde la pagina de pago de una factura (/pagar): misma revision que los pedidos */
type InvProof = {
  id: number; invoice_id: number; invoice_number: string | null; invoice_status: string | null; customer_name: string | null; currency: string;
  balance: string | null; total: string | null; method: string; method_label: string; amount: string | null; reference: string | null;
  filename: string | null; status: string; status_label: string; note: string | null; created_at: string;
  /* el mismo archivo ya se subio en otra factura o en un pedido web: aprobar pide confirmarlo */
  reused_in?: string[];
};

const TABS = ["en_revision", "nuevo", "pagado", "preparando", "listo", "enviado", "entregado", "cancelado", "reembolsado"];
const METHODS: Record<string, string> = { sinpe: "SINPE Móvil", transferencia: "Transferencia", tarjeta: "Tarjeta", efectivo: "Efectivo" };
const ACTION: Record<string, string> = {
  nuevo: "Rechazar comprobante", pagado: "Confirmar pago", preparando: "Preparando", listo: "Listo para retiro",
  enviado: "Marcar enviado", entregado: "Entregado", cancelado: "Cancelar pedido", reembolsado: "Registrar reembolso",
};
const MAIL: Record<string, string> = { enviado: "enviado", simulado: "NO enviado (correo sin configurar)", error: "NO enviado (error)", pendiente: "pendiente" };
const when = (s: string | null | undefined) => (s ? new Date(s).toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");

export default function Orders() {
  const [channel, setChannel] = useState<"web" | "otros">("web");
  return (
    <>
      <div className="page-head">
        <div><div className="meta">11 · Pedidos</div><h1 className="h1">Pedidos de la tienda</h1></div>
        <div className="page-head__actions">
          <div className="tabs" role="tablist" aria-label="Canal">
            <button role="tab" aria-selected={channel === "web"} className={channel === "web" ? "is-active" : ""} onClick={() => setChannel("web")}>Tienda en línea</button>
            <button role="tab" aria-selected={channel === "otros"} className={channel === "otros" ? "is-active" : ""} onClick={() => setChannel("otros")}>Otros canales</button>
          </div>
        </div>
      </div>
      {channel === "web" ? <WebOrders /> : <LegacyOrders />}
    </>
  );
}

function WebOrders() {
  const { toast } = useSession();
  const [sp, setSp] = useSearchParams();
  const [data, setData] = useState<List | null>(null);
  const [status, setStatus] = useState<string>(sp.get("pendiente") === "por_atender" ? "atender" : "");
  const [method, setMethod] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<Order | null>(null);
  const load = useCallback(() => {
    const s = new URLSearchParams();
    if (status === "atender") s.set("pendiente", "por_atender"); else if (status) s.set("status", status);
    if (method) s.set("method", method);
    if (from) s.set("from", from);
    if (to) s.set("to", to);
    if (q.trim()) s.set("q", q.trim());
    return api<List>(`/store-orders?${s}`).then(setData).catch((e) => toast(e instanceof Error ? e.message : "Error", "bad"));
  }, [status, method, from, to, q, toast]);
  useEffect(() => { const t = window.setTimeout(load, q ? 300 : 0); return () => window.clearTimeout(t); }, [load, q]);
  const open = (o: Order) => api<Order>(`/store-orders/${o.id}`).then(setSel).catch((e) => toast(e.message, "bad"));
  const pick = (s: string) => { setStatus(s); if (sp.get("pendiente")) { sp.delete("pendiente"); setSp(sp, { replace: true }); } };
  const labels = data?.labels || {};
  const items = data?.items || [];
  const total = Object.values(data?.counts || {}).reduce((a, b) => a + b, 0);

  const facturas = data?.comprobantes_facturas ?? null;
  return (
    <>
      {facturas !== null && (facturas > 0 || status === "atender" || status === "en_revision") && <InvoiceProofs onChange={load} />}
      <Card flush>
        <div className="wo-tabs">
          <div className="tabs" role="tablist" aria-label="Estado">
            <button className={status === "atender" ? "is-active" : ""} onClick={() => pick("atender")}>Por atender<i>{data ? (data.por_atender + (facturas ?? 0)) || "" : ""}</i></button>
            <button className={status === "" ? "is-active" : ""} onClick={() => pick("")}>Todos<i>{total || ""}</i></button>
            {TABS.map((s) => <button key={s} className={status === s ? "is-active" : ""} onClick={() => pick(s)}>{labels[s] || s}<i>{data?.counts[s] || ""}</i></button>)}
          </div>
        </div>
        <div className="wo-filters">
          <input className="input input--search" type="search" placeholder="Buscar número, nombre, correo o teléfono" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar pedidos" />
          <select className="select" value={method} onChange={(e) => setMethod(e.target.value)} aria-label="Método de pago"><option value="">Todos los métodos</option>{Object.entries(METHODS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          <label className="muted" style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}>Desde<input className="input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
          <label className="muted" style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}>Hasta<input className="input" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
          <button className="btn btn--ghost btn--sm" onClick={load}><Icon d={I.refresh} />Refrescar</button>
        </div>
        {data === null ? <Loading /> : items.length === 0 ? <Empty hint={status === "atender" ? "No hay pedidos esperando acción. ¡Al día!" : "Cuando alguien compre en la tienda, el pedido aparece aquí."} /> : (
          <>
            <table className="table wo-table">
              <thead><tr><th>Pedido</th><th>Fecha</th><th>Cliente</th><th>Entrega</th><th>Pago</th><th className="num">Total</th><th>Estado</th><th /></tr></thead>
              <tbody>{items.map((o) => (
                <tr key={o.id}>
                  <td className="mono" style={{ fontWeight: 600 }}>{o.number}</td>
                  <td className="muted">{when(o.created_at)}</td>
                  <td>{o.contact.name}<div className="meta" style={{ textTransform: "none" }}>{o.contact.email || o.contact.phone}</div></td>
                  <td className="muted">{o.delivery?.type === "retiro" ? "Retiro" : o.shipping_method || "—"}{o.delivery?.canton ? <div className="meta" style={{ textTransform: "none" }}>{o.delivery.canton}, {o.delivery.province}</div> : null}</td>
                  <td className="muted">{o.payment_kind ? METHODS[o.payment_kind] : o.payment_method || "—"}{o.proofs.length > 0 && <div className="meta" style={{ textTransform: "none" }}>{o.proofs.length} comprobante(s)</div>}</td>
                  <td className="num money">{fmtMoney(o.total, o.currency)}</td>
                  <td><span className="wo-badge" data-s={o.status}>{o.status_label}</span></td>
                  <td className="num row-actions"><span className="row-actions__in"><button className="btn btn--ghost btn--sm" onClick={() => open(o)}>Ver</button><TrashLocked reason={LOCKED.order} /></span></td>
                </tr>
              ))}</tbody>
            </table>
            <div className="wo-cards">{items.map((o) => (
              <button key={o.id} className="wo-card" onClick={() => open(o)}>
                <b>{o.number}</b><span className="wo-badge" data-s={o.status}>{o.status_label}</span>
                <span>{o.contact.name}</span><span className="money">{fmtMoney(o.total, o.currency)}</span>
                <span className="muted">{when(o.created_at)} · {o.payment_kind ? METHODS[o.payment_kind] : "—"}</span><span className="muted">{o.delivery?.type === "retiro" ? "Retiro" : "Envío"}</span>
              </button>
            ))}</div>
          </>
        )}
      </Card>
      {sel && <Detail o={sel} labels={labels} onClose={() => setSel(null)} onChange={(o) => { setSel(o); load(); }} />}
    </>
  );
}

function InvoiceProofs({ onChange }: { onChange: () => void }) {
  const { toast, allows } = useSession();
  const [rows, setRows] = useState<InvProof[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const load = useCallback(() => api<{ items: InvProof[] }>("/payment-proofs").then((r) => setRows(r.items)).catch((e) => toast(e instanceof Error ? e.message : "Error", "bad")), [toast]);
  useEffect(() => { load(); }, [load]);
  const done = () => { load(); onChange(); };
  const approve = async (r: InvProof) => {
    const sug = r.amount != null ? Number(r.amount) : Number(r.balance ?? 0);
    const txt = window.prompt(`Monto a registrar en ${r.invoice_number} (${r.currency}). Saldo: ${fmtMoney(r.balance ?? 0, r.currency)}`, String(sug));
    if (txt === null) return;
    const amount = Number(txt.replace(/[^\d.]/g, ""));
    if (!(amount > 0)) { toast("Monto inválido", "bad"); return; }
    const reused = r.reused_in || [];
    if (reused.length && !window.confirm(`Este mismo comprobante ya se subió en ${reused.join(", ")}. ¿Lo aprobás igual para ${r.invoice_number}?`)) return;
    setBusy(r.id);
    try { await api(`/payment-proofs/${r.id}/approve`, { method: "POST", json: { amount, confirm_reused: reused.length > 0 } }); toast(`Pago registrado en ${r.invoice_number}`); done(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } finally { setBusy(null); }
  };
  const reject = async (r: InvProof) => {
    const note = (window.prompt("¿Por qué se rechaza? El cliente lo ve en la página de pago.") || "").trim();
    if (!note) return;
    setBusy(r.id);
    try { await api(`/payment-proofs/${r.id}/reject`, { method: "POST", json: { note } }); toast("Comprobante rechazado"); done(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } finally { setBusy(null); }
  };
  const canReview = allows("payments.crear");
  return (
    <Card title="Comprobantes de facturas" extra={<span className="muted" style={{ fontSize: 12.5 }}>Subidos por clientes desde el enlace de pago de una factura</span>} flush>
      {rows === null ? <Loading /> : rows.length === 0 ? <Empty hint="No hay comprobantes de facturas por revisar." /> : (
        <div style={{ overflowX: "auto" }}>
          <table className="table">
            <thead><tr><th>Factura</th><th>Cliente</th><th>Método</th><th className="num">Dice que pagó</th><th className="num">Saldo</th><th>Subido</th><th /></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id}>
                <td className="mono" style={{ fontWeight: 600 }}><Link to={`/facturas/${r.invoice_id}`}>{r.invoice_number}</Link></td>
                <td>{r.customer_name || "—"}</td>
                <td className="muted">{r.method_label}{r.reference && <div className="meta" style={{ textTransform: "none" }}>Ref. {r.reference}</div>}{!!r.reused_in?.length && <div className="meta" style={{ textTransform: "none", color: "var(--bad)" }}>Ya se subió en {r.reused_in.join(", ")}</div>}</td>
                <td className="num money">{r.amount != null ? fmtMoney(r.amount, r.currency) : "—"}</td>
                <td className="num money">{fmtMoney(r.balance ?? 0, r.currency)}</td>
                <td className="muted">{when(r.created_at)}</td>
                <td className="num" style={{ whiteSpace: "nowrap" }}>
                  <button className="btn btn--ghost btn--sm" onClick={() => openFile(`/payment-proofs/${r.id}/file`).catch((e) => toast(e.message, "bad"))}><Icon d={I.upload} />Ver</button>
                  {canReview && <>
                    <button className="btn btn--soft btn--sm" disabled={busy === r.id} onClick={() => approve(r)}>Aprobar</button>
                    <button className="btn btn--ghost btn--sm" disabled={busy === r.id} onClick={() => reject(r)}>Rechazar</button>
                  </>}
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Detail({ o, labels, onClose, onChange }: { o: Order; labels: Record<string, string>; onClose: () => void; onChange: (o: Order) => void }) {
  const { toast, allows } = useSession();
  const [act, setAct] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [clientNote, setClientNote] = useState("");
  const [freeNote, setFreeNote] = useState("");
  const [freeClient, setFreeClient] = useState(false);
  const [guide, setGuide] = useState(o.tracking_number || "");
  const [carrier, setCarrier] = useState(o.carrier || "Correos de Costa Rica");
  const [ref, setRef] = useState("");
  const [refund, setRefund] = useState(String(Number(o.total)));
  const [notify, setNotify] = useState(true);
  const [busy, setBusy] = useState(false);
  const [withPay, setWithPay] = useState(true);
  const canOnvoRefund = !!(o.onvo_refundable && allows("sales.anular"));
  const [refundViaOnvo, setRefundViaOnvo] = useState(canOnvoRefund);
  const c = o.contact || {};
  const dv = o.delivery || {};
  const needNote = act === "cancelado" || (act === "nuevo" && o.status === "en_revision");
  const needRestock = (act === "cancelado" || act === "reembolsado") && Number(o.stock_out) > 0;
  const [restock, setRestock] = useState<boolean | null>(null);
  /* Lo devuelto entra directo a la bodega de venta de Crimson (Andrés, 19/10: el proveedor casi nunca acepta
     devoluciones): no se elige bodega, solo se confirma si el producto volvió. */
  const [restockWh, setRestockWh] = useState("la bodega principal");
  useEffect(() => { setRestock(null); }, [act]);
  useEffect(() => {
    if (Number(o.stock_out) <= 0) return;
    api<{ name: string; is_default: boolean; active: boolean }[]>("/warehouses").then((ws) => {
      const def = ws.find((w) => w.active && w.is_default);
      if (def) setRestockWh(def.name);
    }).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async () => {
    if (!act) return;
    if (needNote && !note.trim()) { toast(act === "cancelado" ? "Escribí el motivo de la cancelación" : "Escribí qué falló en el comprobante", "bad"); return; }
    if (needRestock && restock === null) { toast("Indicá si el producto volvió a bodega", "bad"); return; }
    setBusy(true);
    try {
      const r = await api<Order>(`/store-orders/${o.id}/status`, {
        method: "POST",
        json: { status: act, note: note.trim() || null, ...(!needNote && clientNote.trim() ? { client_note: clientNote.trim() } : {}), notify, ...(act === "enviado" ? { tracking_number: guide, carrier } : {}), ...(act === "pagado" && ref ? { payment_ref: ref } : {}), ...(act === "reembolsado" ? { refund_amount: refund.trim() || null, ...(canOnvoRefund ? { refund_via_onvo: refundViaOnvo } : {}) } : {}), ...(needRestock ? { restock: restock as boolean } : {}) },
      });
      const m = r.email_result;
      toast(`${labels[act] || act}${m ? ` · correo al cliente ${MAIL[m.status] || m.status}` : notify ? " · sin correo del cliente" : ""}`, m && m.status !== "enviado" ? "bad" : undefined);
      setAct(null); setNote(""); setClientNote(""); onChange(r);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } finally { setBusy(false); }
  };
  const invoice = async (doc_type: "TE" | "FE") => {
    try {
      const r = await api<{ number: string; invoice_id: number }>(`/orders/${o.id}/invoice?doc_type=${doc_type}&register_payment=${withPay && !!o.paid_at}`, { method: "POST" });
      toast(`${doc_type === "TE" ? "Tiquete" : "Factura"} ${r.number} creado`);
      onChange(await api<Order>(`/store-orders/${o.id}`));
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const resend = async () => {
    try { const r = await api<{ status: string }>(`/store-orders/${o.id}/resend`, { method: "POST" }); toast(`Correo ${MAIL[r.status] || r.status}`, r.status !== "enviado" ? "bad" : undefined); onChange(await api<Order>(`/store-orders/${o.id}`)); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const addNote = async () => {
    if (busy || !freeNote.trim()) return; // Enter dos veces antes de la respuesta no duplica la nota
    setBusy(true);
    try {
      const r = await api<Order>(`/store-orders/${o.id}/note`, { method: "POST", json: { note: freeNote.trim(), client: freeClient } });
      toast(freeClient ? "Nota agregada: el cliente la ve en su seguimiento" : "Nota interna agregada");
      // la casilla vuelve a desmarcarse: cada nota para el cliente se marca a proposito (la siguiente no se filtra)
      setFreeNote(""); setFreeClient(false); onChange(r);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } finally { setBusy(false); }
  };
  const copyLink = async () => { if (!o.track_url) return; try { await navigator.clipboard.writeText(o.track_url); toast("Enlace de seguimiento copiado"); } catch { toast("No se pudo copiar", "bad"); } };
  const waUrl = c.phone ? `https://wa.me/${(c.phone.replace(/\D/g, "").length === 8 ? "506" : "") + c.phone.replace(/\D/g, "")}?text=${encodeURIComponent(`Hola ${c.name || ""}, le escribimos por su pedido ${o.number}.${o.track_url ? ` Estado: ${o.track_url}` : ""}`)}` : null;

  return (
    <Modal title={<>Pedido {o.number} <span className="wo-badge" data-s={o.status} style={{ verticalAlign: "middle", marginLeft: 6 }}>{o.status_label}</span></>} onClose={onClose} wide foot={
      <div className="wo-foot">
        <button className="btn btn--ghost btn--sm" onClick={() => openFile(`/store-orders/${o.id}/print`).catch((e) => toast(e.message, "bad"))}><Icon d={I.invoice} />Imprimir</button>
        {o.track_url && <button className="btn btn--ghost btn--sm" onClick={copyLink}><Icon d={I.link} />Enlace de seguimiento</button>}
        {waUrl && <a className="btn btn--ghost btn--sm" href={waUrl} target="_blank" rel="noopener noreferrer"><Icon d={I.whatsapp} />WhatsApp</a>}
        {c.email && <button className="btn btn--ghost btn--sm" onClick={resend}><Icon d={I.refresh} />Reenviar correo</button>}
        {o.invoice_id ? (o.invoice_visible === false
          ? <span className="muted" style={{ fontSize: 13 }} title="La facturó otra persona del equipo">Facturado · comprobante #{o.invoice_id}</span>
          : <Link className="btn btn--crimson btn--sm" to={`/facturas/${o.invoice_id}`}>Ver comprobante #{o.invoice_id}</Link>) : (
          <>
            {o.paid_at && <label style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={withPay} onChange={(e) => setWithPay(e.target.checked)} />Aplicar el cobro</label>}
            <button className="btn btn--soft btn--sm" onClick={() => invoice("TE")}>Emitir tiquete</button>
            <button className="btn btn--crimson btn--sm" onClick={() => invoice("FE")} disabled={!c.id_number} title={c.id_number ? "" : "El cliente no indicó cédula"}>Factura electrónica</button>
          </>
        )}
      </div>
    }>
      <div className="wo-detail">
        <div className="wo-grid">
          <div className="wo-box"><span className="meta">Cliente</span><b>{c.name}</b>{c.company && <span>{c.company}</span>}<span>{c.email}</span><span>{c.phone}</span>{c.id_number && <span className="mono">Céd. {c.id_number}</span>}{o.customer_id && <Link to={`/clientes/${o.customer_id}`} className="row-link">Ficha del cliente →</Link>}</div>
          <div className="wo-box"><span className="meta">Entrega</span>
            {dv.type === "retiro" ? <b>Retiro en sitio</b> : <><b>{o.shipping_method || "Envío"}</b><span>{[dv.address, dv.district, dv.canton, dv.province].filter(Boolean).join(", ") || c.address || "—"}</span></>}
            {o.tracking_number && <span>Guía <b className="mono">{o.tracking_number}</b>{o.carrier ? ` · ${o.carrier}` : ""}</span>}
          </div>
          <div className="wo-box"><span className="meta">Pago</span><b>{o.payment_method || "A coordinar"}</b>
            <span>{o.paid_at ? `Pagado ${when(o.paid_at)}` : o.status === "en_revision" ? "Comprobante en revisión" : o.expires_at ? `Pendiente · vence ${when(o.expires_at)}` : "Pendiente"}</span>
            {o.payment_ref && <span className="mono">Ref. {o.payment_ref}</span>}{o.coupon_code && <span>Cupón {o.coupon_code}</span>}
            {o.refund && <span>Reembolso registrado: {fmtMoney(o.refund.amount, o.currency)}</span>}
          </div>
        </div>
        {o.notes && <div className="wo-box"><span className="meta">Nota del cliente</span>“{o.notes}”</div>}
        {o.cancel_reason && <div className="wo-box"><span className="meta">Motivo de cancelación</span>{o.cancel_reason}</div>}
        {o.proofs.length > 0 && (
          <div className="wo-box"><span className="meta">Comprobantes (privados)</span>
            <div className="wo-proofs">{o.proofs.map((p) => <button key={p.media_id} className="btn btn--soft btn--sm" onClick={() => openFile(`/store-orders/${o.id}/proofs/${p.media_id}`).catch((e) => toast(e.message, "bad"))}><Icon d={I.upload} />{p.filename} · {when(p.at)}</button>)}</div>
          </div>
        )}

        {(o.next?.length ?? 0) > 0 && (
          <div className="wo-act">
            <div className="wo-act__row">{o.next!.map((s) => (
              <button key={s} className={`btn btn--sm ${act === s ? "btn--crimson" : s === "cancelado" || (s === "nuevo" && o.status === "en_revision") ? "btn--ghost" : "btn--soft"}`} onClick={() => setAct(act === s ? null : s)}>{ACTION[s] || labels[s] || s}</button>
            ))}</div>
            {act && (
              <>
                <div className="wo-act__row">
                  {act === "pagado" && <Field label="Referencia del pago (opcional)"><input className="input" value={ref} onChange={(e) => setRef(e.target.value)} placeholder="N.º de comprobante SINPE / transferencia" /></Field>}
                  {act === "enviado" && <><Field label="Número de guía (opcional)"><input className="input input--mono" style={{ textAlign: "left" }} value={guide} onChange={(e) => setGuide(e.target.value)} /></Field><Field label="Transportista"><input className="input" value={carrier} onChange={(e) => setCarrier(e.target.value)} /></Field></>}
                  {act === "reembolsado" && <><Field label={`Monto (${o.currency})`} hint={canOnvoRefund && refundViaOnvo ? "Monto a devolver por ONVO (vacío = total)." : "Solo registro: el dinero se devuelve por el banco o por ONVO."}><input className="input input--mono" value={refund} onChange={(e) => setRefund(e.target.value)} /></Field>{canOnvoRefund && <label style={{ fontSize: 12.5, display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={refundViaOnvo} onChange={(e) => setRefundViaOnvo(e.target.checked)} />Devolver el dinero por ONVO a la tarjeta del cliente</label>}</>}
                  {needNote
                    ? <Field label={act === "cancelado" ? "Motivo (lo ve el cliente)" : "Qué falló (lo ve el cliente)"}><input className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} /></Field>
                    : <>
                      <Field label="Nota para el cliente (opcional)" hint="Sale en su página de seguimiento. Ej.: presentar el comprobante con el número de pedido."><input className="input" value={clientNote} onChange={(e) => setClientNote(e.target.value)} maxLength={500} /></Field>
                      <Field label="Nota interna (opcional)" hint="Solo la ve el equipo."><input className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} /></Field>
                    </>}
                </div>
                {needRestock && (
                  <div className="wo-act__row" style={{ flexDirection: "column", alignItems: "flex-start", gap: 4 }}>
                    <span className="meta">¿Volvió el producto a bodega? (salieron {Number(o.stock_out)} unidades)</span>
                    <label style={{ fontSize: 12.5, display: "flex", gap: 6, alignItems: "center" }}><input type="radio" name={`restock-${o.id}`} checked={restock === true} onChange={() => setRestock(true)} />Sí, entra a {restockWh} y queda a la venta</label>
                    <label style={{ fontSize: 12.5, display: "flex", gap: 6, alignItems: "center" }}><input type="radio" name={`restock-${o.id}`} checked={restock === false} onChange={() => setRestock(false)} />No, queda fuera (se registra en el historial)</label>
                  </div>
                )}
                <div className="wo-act__row" style={{ justifyContent: "space-between" }}>
                  <label style={{ fontSize: 12.5, display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} />Avisar al cliente por correo</label>
                  <button className="btn btn--crimson btn--sm" onClick={run} disabled={busy || (needRestock && restock === null)}><Icon d={I.check} />{busy ? "Guardando…" : ACTION[act] || "Aplicar"}</button>
                </div>
              </>
            )}
          </div>
        )}

        <table className="table">
          <thead><tr><th>Producto</th><th className="num">Cant.</th><th className="num">Precio s/IVA</th><th className="num">Total</th></tr></thead>
          <tbody>{o.lines.map((l) => <tr key={l.id}><td>{l.name}</td><td className="num mono">{Number(l.quantity)}</td><td className="num money">{fmtMoney(l.unit_price, o.currency)}</td><td className="num money">{fmtMoney(l.total, o.currency)}</td></tr>)}</tbody>
        </table>
        <div className="totals">
          <div><span>Subtotal</span><span className="money">{fmtMoney(o.subtotal, o.currency)}</span></div>
          {Number(o.discount_total) > 0 && <div><span>Descuento</span><span className="money">−{fmtMoney(o.discount_total, o.currency)}</span></div>}
          <div><span>Envío</span><span className="money">{fmtMoney(o.shipping, o.currency)}</span></div>
          <div><span>IVA</span><span className="money">{fmtMoney(o.tax_total, o.currency)}</span></div>
          <div className="total"><span>Total</span><span className="money">{fmtMoney(o.total, o.currency)}</span></div>
        </div>

        <div className="grid-2">
          <div><div className="meta" style={{ marginBottom: 6 }}>Historial</div>
            <ul className="wo-hist">{[...o.history].reverse().map((h, i) => (
              <li key={i}><time>{when(h.at)}</time><span>
                <b>{h.kind === "nota" ? (h.client_note ? "Nota al cliente" : "Nota") : labels[h.status] || h.status}</b>{typeof h.by === "string" ? ` · ${h.by}` : ""}{h.note && h.note !== h.client_note ? ` — ${h.note}` : ""}
                {h.client_note && <span className="wo-cnote" title="La ve el cliente en su página de seguimiento">Al cliente: «{h.client_note}»</span>}
              </span></li>
            ))}</ul>
            {o.channel === "tienda" && (
              <div className="wo-note">
                <input className="input" value={freeNote} onChange={(e) => setFreeNote(e.target.value)} maxLength={500} placeholder="Agregar una nota…" aria-label="Nota del pedido" onKeyDown={(e) => { if (e.key === "Enter") addNote(); }} />
                <label style={{ fontSize: 12.5, display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={freeClient} onChange={(e) => setFreeClient(e.target.checked)} />La ve el cliente</label>
                <button className="btn btn--soft btn--sm" onClick={addNote} disabled={busy || !freeNote.trim()}>Agregar</button>
              </div>
            )}
          </div>
          <div><div className="meta" style={{ marginBottom: 6 }}>Correos al cliente y al equipo</div>
            {(o.emails?.length ?? 0) === 0 ? <span className="muted" style={{ fontSize: 12.5 }}>Sin correos.</span> : o.emails!.map((m, i) => <div key={i} className="wo-mail" data-s={m.status}><b>{MAIL[m.status] || m.status}</b><span className="muted">{m.subject} → {m.to}</span></div>)}
          </div>
        </div>
      </div>
    </Modal>
  );
}

/* Otros canales (POS, enlaces): el flujo de siempre. */
type Legacy = { id: number; number: string; channel: string; contact: Record<string, string>; currency: string; total: string; shipping_method: string | null; payment_method: string | null; status: string; invoice_id: number | null; invoice_visible?: boolean | null; created_at: string };
const STATES = ["nuevo", "pagado", "preparando", "enviado", "entregado", "cancelado"];
function LegacyOrders() {
  const { toast } = useSession();
  const [items, setItems] = useState<Legacy[] | null>(null);
  const load = () => api<Legacy[]>("/orders?limit=200").then((r) => setItems(r.filter((o) => o.channel !== "tienda")));
  useEffect(() => { load(); }, []);
  const setStatus = async (o: Legacy, status: string) => { try { await api(`/orders/${o.id}`, { method: "PATCH", json: { status } }); toast("Estado actualizado"); load(); } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } };
  return (
    <Card flush>
      {items === null ? <Loading /> : items.length === 0 ? <Empty hint="Pedidos del punto de venta y enlaces de pago aparecen aquí." /> : (
        <table className="table">
          <thead><tr><th>Pedido</th><th>Canal</th><th>Cliente</th><th>Fecha</th><th>Pago</th><th className="num">Total</th><th>Estado</th><th>Comprobante</th></tr></thead>
          <tbody>{items.map((o) => (
            <tr key={o.id}>
              <td className="mono" style={{ fontWeight: 600 }}>{o.number}</td>
              <td className="muted">{o.channel}</td>
              <td>{o.contact.name}<div className="meta" style={{ textTransform: "none" }}>{o.contact.email || o.contact.phone}</div></td>
              <td className="muted">{fmtDate(o.created_at.slice(0, 10))}</td>
              <td className="muted">{o.payment_method || "—"}</td>
              <td className="num money">{fmtMoney(o.total, o.currency)}</td>
              <td><select className="select" style={{ maxWidth: 150 }} value={o.status} onChange={(e) => setStatus(o, e.target.value)} aria-label={`Estado de ${o.number}`}>{STATES.map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}</select></td>
              <td>{o.invoice_id ? (o.invoice_visible === false
                ? <span className="mono muted" title="La facturó otra persona del equipo">#{o.invoice_id}</span>
                : <Link className="row-link mono" to={`/facturas/${o.invoice_id}`}>#{o.invoice_id}</Link>) : <Badge status={o.status} />}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </Card>
  );
}
