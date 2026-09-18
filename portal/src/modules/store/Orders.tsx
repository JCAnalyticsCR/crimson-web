/* Ordenes de tienda/POS: estados, detalle y conversion a tiquete o factura. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Card, Empty, I, Icon, Modal } from "../../ui/components";

type Order = { id: number; number: string; channel: string; contact: Record<string, string>; currency: string; subtotal: string; discount_total: string; shipping: string; tax_total: string; total: string; coupon_code: string | null; shipping_method: string | null; payment_method: string | null; status: string; invoice_id: number | null; notes: string | null; created_at: string; lines: { id: number; name: string; quantity: string; unit_price: string; total: string }[] };
const STATES = ["nuevo", "pagado", "preparando", "enviado", "entregado", "cancelado"];

export default function Orders() {
  const { toast } = useSession();
  const [items, setItems] = useState<Order[]>([]);
  const [filter, setFilter] = useState("");
  const [sel, setSel] = useState<Order | null>(null);
  const load = () => api<Order[]>(`/orders${filter ? `?status=${filter}` : ""}`).then(setItems);
  useEffect(() => { load(); }, [filter]);

  const setStatus = async (o: Order, status: string) => { try { await api(`/orders/${o.id}`, { method: "PATCH", json: { status } }); toast("Estado actualizado"); load(); setSel(null); } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } };
  const invoice = async (o: Order, doc_type: "TE" | "FE") => {
    try { const r = await api<{ number: string; invoice_id: number }>(`/orders/${o.id}/invoice?doc_type=${doc_type}`, { method: "POST" }); toast(`${doc_type === "TE" ? "Tiquete" : "Factura"} ${r.number} creado`); load(); setSel(null); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  return (
    <>
      <div className="page-head">
        <div><div className="meta">11 · Órdenes</div><h1 className="h1">Órdenes</h1></div>
        <div className="page-head__actions"><button className="btn btn--ghost btn--sm" onClick={load}><Icon d={I.refresh} />Refrescar</button></div>
      </div>
      <Card flush>
        <div className="list-head">
          <span className="muted" style={{ fontSize: 13 }}>Pedidos de la tienda en línea</span>
          <div className="tabs">{["", ...STATES].map((s) => <button key={s || "todas"} className={filter === s ? "is-active" : ""} onClick={() => setFilter(s)}>{s ? s[0].toUpperCase() + s.slice(1) : "Todas"}</button>)}</div>
        </div>
        {items.length === 0 ? <Empty hint="Cuando alguien compre en la tienda, el pedido aparece aquí listo para facturar." /> : (
          <table className="table">
            <thead><tr><th>Pedido</th><th>Cliente</th><th>Fecha</th><th>Envío</th><th>Pago</th><th className="num">Total</th><th>Estado</th><th>Comprobante</th><th /></tr></thead>
            <tbody>{items.map((o) => (
              <tr key={o.id}>
                <td className="mono" style={{ fontWeight: 600 }}>{o.number}</td>
                <td>{o.contact.name}<div className="meta" style={{ textTransform: "none" }}>{o.contact.email || o.contact.phone}</div></td>
                <td className="muted">{fmtDate(o.created_at.slice(0, 10))}</td>
                <td className="muted">{o.shipping_method || "—"}</td>
                <td className="muted">{o.payment_method || "—"}</td>
                <td className="num money">{fmtMoney(o.total, o.currency)}</td>
                <td><Badge status={o.status} /></td>
                <td>{o.invoice_id ? <Link className="row-link mono" to={`/facturas/${o.invoice_id}`}>#{o.invoice_id}</Link> : <span className="muted">—</span>}</td>
                <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setSel(o)}>Ver</button></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>

      {sel && (
        <Modal title={`Pedido ${sel.number}`} onClose={() => setSel(null)} wide foot={
          <>
            <select className="select" style={{ maxWidth: 160, marginRight: "auto" }} value={sel.status} onChange={(e) => setStatus(sel, e.target.value)}>{STATES.map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}</select>
            {!sel.invoice_id && <><button className="btn btn--soft" onClick={() => invoice(sel, "TE")}>Emitir tiquete</button><button className="btn btn--crimson" onClick={() => invoice(sel, "FE")}>Emitir factura</button></>}
            {sel.invoice_id && <Link className="btn btn--crimson" to={`/facturas/${sel.invoice_id}`}>Ver comprobante</Link>}
          </>
        }>
          <div className="grid-2">
            <div><div className="meta">Cliente</div><b>{sel.contact.name}</b><p className="muted" style={{ fontSize: 13 }}>{sel.contact.email}<br />{sel.contact.phone}<br />{sel.contact.id_number}<br />{sel.contact.address}</p></div>
            <div><div className="meta">Entrega y pago</div><p className="muted" style={{ fontSize: 13 }}>{sel.shipping_method || "Sin envío"}<br />{sel.payment_method || "Sin método"}<br />{sel.coupon_code ? `Cupón ${sel.coupon_code}` : ""}</p>{sel.notes && <p className="muted" style={{ fontSize: 13 }}>“{sel.notes}”</p>}</div>
          </div>
          <table className="table">
            <thead><tr><th>Producto</th><th className="num">Cant.</th><th className="num">Precio</th><th className="num">Total</th></tr></thead>
            <tbody>{sel.lines.map((l) => <tr key={l.id}><td>{l.name}</td><td className="num mono">{Number(l.quantity)}</td><td className="num money">{fmtMoney(l.unit_price, sel.currency)}</td><td className="num money">{fmtMoney(l.total, sel.currency)}</td></tr>)}</tbody>
          </table>
          <div className="totals">
            <div><span>Subtotal</span><span className="money">{fmtMoney(sel.subtotal, sel.currency)}</span></div>
            {Number(sel.discount_total) > 0 && <div><span>Descuento</span><span className="money">−{fmtMoney(sel.discount_total, sel.currency)}</span></div>}
            <div><span>Envío</span><span className="money">{fmtMoney(sel.shipping, sel.currency)}</span></div>
            <div><span>IVA</span><span className="money">{fmtMoney(sel.tax_total, sel.currency)}</span></div>
            <div className="total"><span>Total</span><span className="money">{fmtMoney(sel.total, sel.currency)}</span></div>
          </div>
        </Modal>
      )}
    </>
  );
}
