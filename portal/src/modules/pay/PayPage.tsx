/* Pagina publica del enlace de pago (sin sesion). ONVO conectado via Ajustes → Pasarelas; pago confirmado por
   webhook y verificado server-side — el regreso del navegador nunca confirma el cobro por sí solo. */
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { fmtDate, fmtMoney } from "../../lib/api";
import { Badge } from "../../ui/components";

type Pay = { business: { name: string; logo_url: string | null }; invoice: { number: string; date: string; due_date: string | null; currency: string; total: string; balance: string; status: string; lines: { name: string; quantity: string; unit_price: string; total: string }[] }; customer: { name: string } | null; methods: { online: { onvo: boolean; paypal: boolean }; manual: { name: string; instructions: string }[] } };

export default function PayPage() {
  const { token } = useParams();
  const [d, setD] = useState<Pay | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [onvoBusy, setOnvoBusy] = useState(false);
  const [onvoErr, setOnvoErr] = useState<string | null>(null);
  const [onvoNotice, setOnvoNotice] = useState<string | null>(null);
  const syncTimer = useRef<number | null>(null);
  const syncCount = useRef(0);

  const doSync = async (): Promise<boolean> => {
    try {
      const r = await fetch(`/api/public/pay/${token}/onvo/sync`, { method: "POST" });
      if (!r.ok) return false;
      const data: Pay = await r.json();
      setD(data);
      return Number(data.invoice.balance) <= 0 || data.invoice.status === "pagada";
    } catch { return false; }
  };

  const startSync = () => {
    syncCount.current = 0;
    doSync().then((done) => {
      if (done) { setOnvoNotice("¡Pago recibido! Gracias."); return; }
      syncTimer.current = window.setInterval(async () => {
        if (++syncCount.current > 12) { window.clearInterval(syncTimer.current!); setOnvoNotice("Todavía no vemos el pago confirmado. Si ya pagaste, se aplica solo apenas ONVO lo confirme: no hace falta pagar de nuevo."); return; }
        const finished = await doSync();
        if (finished) { window.clearInterval(syncTimer.current!); setOnvoNotice("¡Pago recibido! Gracias."); }
      }, 5_000);
    });
  };

  useEffect(() => {
    fetch(`/api/public/pay/${token}`).then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail); setD(await r.json()); }).catch((e) => setErr(e.message));
    // Detectar regreso de ONVO
    const sp = new URLSearchParams(window.location.search);
    const ret = sp.get("onvo");
    if (ret) {
      sp.delete("onvo");
      const newUrl = [window.location.pathname, sp.toString()].filter(Boolean).join("?");
      window.history.replaceState(null, "", newUrl);
      if (ret === "cancelado") {
        setOnvoNotice("Cancelaste el pago. Podés intentarlo de nuevo cuando quieras.");
      } else if (ret === "volver") {
        setOnvoNotice("Estamos confirmando tu pago con ONVO…");
        startSync();
      }
    }
    return () => { if (syncTimer.current) window.clearInterval(syncTimer.current); };
  }, [token]);

  const payOnvo = async () => {
    setOnvoBusy(true); setOnvoErr(null);
    try {
      const r = await fetch(`/api/public/pay/${token}/onvo`, { method: "POST" });
      if (!r.ok) { const j = await r.json(); setOnvoErr(j.detail || "No disponible en este momento."); return; }
      const { checkout_url } = await r.json();
      window.location.assign(checkout_url);
    } catch { setOnvoErr("Error de conexión. Intentá de nuevo."); } finally { setOnvoBusy(false); }
  };

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
              {onvoNotice && <p style={{ background: "var(--bg-2)", padding: "10px 14px", borderRadius: 8, fontSize: 13, margin: 0 }}>{onvoNotice}</p>}
              <div className="meta">Métodos de pago</div>
              {d.methods.online.onvo && Number(d.invoice.balance) > 0 && (
                <div>
                  <button className="btn btn--crimson" disabled={onvoBusy} onClick={payOnvo}>{onvoBusy ? "Abriendo ONVO…" : "Pagar con tarjeta o SINPE (ONVO)"}</button>
                  {onvoErr && <p style={{ color: "var(--bad)", fontSize: 12, marginTop: 6, marginBottom: 0 }}>{onvoErr}</p>}
                </div>
              )}
              {d.methods.manual.map((m) => <div className="pay__method" key={m.name}><b>{m.name}</b><span>{m.instructions}</span></div>)}
              <p className="muted" style={{ fontSize: 12 }}>Después de pagar, compartí el comprobante por WhatsApp con {d.business.name}. Este enlace es único y expira automáticamente.</p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
