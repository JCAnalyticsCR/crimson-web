/* Pagina publica del enlace de pago (sin sesion). ONVO/PayPal se conectan en Fase 3; hoy muestra metodos manuales. */
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { fmtDate, fmtMoney } from "../../lib/api";
import { Badge } from "../../ui/components";

type Pay = { business: { name: string; logo_url: string | null }; invoice: { number: string; date: string; due_date: string | null; currency: string; total: string; balance: string; status: string; lines: { name: string; quantity: string; unit_price: string; total: string }[] }; customer: { name: string } | null; methods: { online: { onvo: boolean; paypal: boolean }; manual: { name: string; instructions: string }[] } };

export default function PayPage() {
  const { token } = useParams();
  const [d, setD] = useState<Pay | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { fetch(`/api/public/pay/${token}`).then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail); setD(await r.json()); }).catch((e) => setErr(e.message)); }, [token]);

  return (
    <div className="pay">
      <div className="pay__card">
        {err && <div className="empty"><div className="h2">Enlace no disponible</div><p className="muted">{err}</p></div>}
        {d && (
          <>
            <div className="pay__head">
              <div><div className="meta" style={{ color: "rgba(255,255,255,.6)" }}>{d.business.name} · {d.invoice.number}</div><div className="pay__amount">{fmtMoney(d.invoice.balance, d.invoice.currency)}</div><div style={{ fontSize: 13, opacity: .75 }}>Saldo por pagar · vence {fmtDate(d.invoice.due_date)}</div></div>
              <Badge status={d.invoice.status} />
            </div>
            <div style={{ padding: 22, display: "flex", flexDirection: "column", gap: 16 }}>
              {d.customer && <p className="muted">Hola <b style={{ color: "var(--text)" }}>{d.customer.name}</b>, este es el detalle de tu factura.</p>}
              <table className="table"><tbody>{d.invoice.lines.map((l, i) => <tr key={i}><td>{l.name}<div className="meta">{Number(l.quantity)} × {fmtMoney(l.unit_price, d.invoice.currency)}</div></td><td className="num money">{fmtMoney(l.total, d.invoice.currency)}</td></tr>)}</tbody></table>
              <div className="meta">Métodos de pago</div>
              {d.methods.online.onvo && <button className="btn btn--crimson">Pagar con tarjeta o SINPE (ONVO)</button>}
              {d.methods.manual.map((m) => <div className="pay__method" key={m.name}><b>{m.name}</b><span>{m.instructions}</span></div>)}
              <p className="muted" style={{ fontSize: 12 }}>Después de pagar, compartí el comprobante por WhatsApp con {d.business.name}. Este enlace es único y expira automáticamente.</p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
