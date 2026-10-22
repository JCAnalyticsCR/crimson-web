/* Pagina publica del enlace de pago (sin sesion). ONVO conectado via Ajustes → Pasarelas; pago confirmado por
   webhook y verificado server-side — el regreso del navegador nunca confirma el cobro por sí solo.
   SINPE Móvil y transferencia (pedido 21/10): muestran los datos de Mi Tienda → Pagos y dejan subir el comprobante,
   que entra a la misma revisión que los pedidos web; el pago se registra cuando el equipo lo aprueba. */
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { fmtDate, fmtMoney } from "../../lib/api";
import { Badge } from "../../ui/components";

type Method = { code: string; label: string; needs_proof: boolean; instructions: string; details: [string, string][]; note: string | null };
type Proof = { id: number; method: string; filename: string; at: string; status: "pendiente" | "aprobado" | "rechazado"; status_label: string; note: string | null };
type Pay = {
  business: { name: string; logo_url: string | null };
  invoice: { number: string; date: string; due_date: string | null; currency: string; total: string; balance: string; status: string; lines: { name: string; quantity: string; unit_price: string; total: string }[] };
  customer: { name: string } | null;
  methods: { online: { onvo: boolean; paypal: boolean }; manual: { name: string; instructions: string }[]; list?: Method[] };
  proofs?: Proof[];
};

const when = (s: string) => new Date(s).toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

export default function PayPage() {
  const { token } = useParams();
  const [d, setD] = useState<Pay | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [onvoBusy, setOnvoBusy] = useState(false);
  const [onvoErr, setOnvoErr] = useState<string | null>(null);
  const [onvoNotice, setOnvoNotice] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
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
        setOnvoNotice("Cancelaste el pago. Puede intentarlo de nuevo cuando quieras.");
      } else if (ret === "volver") {
        setOnvoNotice("Estamos confirmando tu pago con ONVO…");
        startSync();
      }
    }
    return () => { if (syncTimer.current) window.clearInterval(syncTimer.current); };
  }, [token]); // eslint-disable-line react-hooks/exhaustive-deps

  const payOnvo = async () => {
    setOnvoBusy(true); setOnvoErr(null);
    try {
      const r = await fetch(`/api/public/pay/${token}/onvo`, { method: "POST" });
      if (!r.ok) { const j = await r.json(); setOnvoErr(j.detail || "No disponible en este momento."); return; }
      const { checkout_url } = await r.json();
      window.location.assign(checkout_url);
    } catch { setOnvoErr("Error de conexión. Intentá de nuevo."); } finally { setOnvoBusy(false); }
  };

  const pending = Number(d?.invoice.balance ?? 0) > 0 && d?.invoice.status !== "anulada";
  // pantallas de antes de esta version del API: solo nombre + instrucciones
  const methods: Method[] = d ? (d.methods.list ?? d.methods.manual.map((m) => ({ code: m.name, label: m.name, needs_proof: false, instructions: m.instructions, details: [], note: null }))) : [];

  return (
    <div className="pay">
      <div className="pay__card">
        {err && <div className="empty"><div className="h2">Enlace no disponible</div><p className="muted">{err}</p></div>}
        {d && (
          <>
            <div className="pay__head">
              <div>
                {d.business.logo_url && <img className="pay__logo" src={d.business.logo_url} alt={d.business.name} />}
                <div className="meta" style={{ color: "rgba(255,255,255,.6)" }}>{d.business.name} · {d.invoice.number}</div>
                <div className="pay__amount">{fmtMoney(d.invoice.balance, d.invoice.currency)}</div>
                <div style={{ fontSize: 13, opacity: .75 }}>Saldo por pagar{d.invoice.due_date ? ` · vence ${fmtDate(d.invoice.due_date)}` : ""}</div>
              </div>
              <Badge status={d.invoice.status} />
            </div>
            <div style={{ padding: 22, display: "flex", flexDirection: "column", gap: 16 }}>
              {d.customer && <p className="muted">Hola <b style={{ color: "var(--text)" }}>{d.customer.name}</b>, este es el detalle de tu factura.</p>}
              <table className="table"><tbody>{d.invoice.lines.map((l, i) => <tr key={i}><td>{l.name}<div className="meta">{Number(l.quantity)} × {fmtMoney(l.unit_price, d.invoice.currency)}</div></td><td className="num money">{fmtMoney(l.total, d.invoice.currency)}</td></tr>)}</tbody></table>
              {onvoNotice && <p style={{ background: "var(--bg-2)", padding: "10px 14px", borderRadius: 8, fontSize: 13, margin: 0 }}>{onvoNotice}</p>}
              {(d.proofs?.length ?? 0) > 0 && (
                <div className="pay__proofs">
                  <div className="meta">Tus comprobantes</div>
                  {d.proofs!.map((p) => (
                    <div key={p.id} className="pay__proof" data-s={p.status}>
                      <span><b>{p.status_label}</b> · {p.method} · {p.filename} · {when(p.at)}</span>
                      {p.status === "pendiente" && <span className="muted">Lo estamos revisando: te avisamos al aplicar el pago.</span>}
                      {p.status === "rechazado" && p.note && <span>Motivo: {p.note}. Puede subir otro.</span>}
                    </div>
                  ))}
                </div>
              )}
              {pending && (
                <>
                  <div className="meta">Métodos de pago</div>
                  {methods.length === 0 && <p className="muted" style={{ margin: 0 }}>Escribinos a {d.business.name} para coordinar el pago.</p>}
                  {methods.map((m) => m.code === "tarjeta" ? (
                    <div className="pay__method" key={m.code}>
                      <b>{m.label}</b><span>{m.instructions}</span>
                      <div><button className="btn btn--crimson" disabled={onvoBusy} onClick={payOnvo}>{onvoBusy ? "Abriendo ONVO…" : "Pagar con tarjeta"}</button></div>
                      {onvoErr && <p style={{ color: "var(--bad)", fontSize: 12, margin: 0 }}>{onvoErr}</p>}
                    </div>
                  ) : (
                    <div className="pay__method" key={m.code}>
                      <b>{m.label}</b>
                      <span>{m.instructions}</span>
                      {m.details.length > 0 && (
                        <dl className="pay__details">{m.details.map(([k, v]) => <div key={k}><dt>{k}</dt><dd><span className="mono">{v}</span><CopyBtn text={v} /></dd></div>)}</dl>
                      )}
                      {m.note && <span>{m.note}</span>}
                      {m.needs_proof && (open === m.code
                        ? <ProofForm token={token!} method={m.code} balance={d.invoice.balance} currency={d.invoice.currency} onDone={(p) => { setD(p); setOpen(null); }} onCancel={() => setOpen(null)} />
                        : <div><button className="btn btn--soft btn--sm" onClick={() => setOpen(m.code)}>Ya pagué: subir comprobante</button></div>)}
                    </div>
                  ))}
                </>
              )}
              {!pending && <p className="muted" style={{ margin: 0 }}>{d.invoice.status === "anulada" ? "Esta factura está anulada." : "Esta factura no tiene saldo pendiente. ¡Gracias!"}</p>}
              <p className="muted" style={{ fontSize: 12, margin: 0 }}>Este enlace es único y expira automáticamente. Si tiene dudas, escribinos a {d.business.name}.</p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function CopyBtn({ text }: { text: string }) {
  const [ok, setOk] = useState(false);
  const copy = () => navigator.clipboard?.writeText(text).then(() => { setOk(true); window.setTimeout(() => setOk(false), 1500); }).catch(() => {});
  return <button type="button" className="btn btn--ghost btn--sm" onClick={copy} aria-label={`Copiar ${text}`}>{ok ? "Copiado" : "Copiar"}</button>;
}

function ProofForm({ token, method, balance, currency, onDone, onCancel }: { token: string; method: string; balance: string; currency: string; onDone: (p: Pay) => void; onCancel: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [ref, setRef] = useState("");
  const [amount, setAmount] = useState(String(Number(balance)));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const send = async () => {
    if (!file) { setMsg("Elegí la foto o el PDF del comprobante."); return; }
    setBusy(true); setMsg(null);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("method", method);
    if (ref.trim()) fd.append("reference", ref.trim());
    if (Number(amount) > 0) fd.append("amount", String(Number(amount)));
    try {
      const r = await fetch(`/api/public/pay/${token}/proof`, { method: "POST", body: fd });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { setMsg(typeof j.detail === "string" ? j.detail : "No se pudo subir el comprobante."); return; }
      onDone(j as Pay);
    } catch { setMsg("Error de conexión. Intentá de nuevo."); } finally { setBusy(false); }
  };
  return (
    <div className="pay__form">
      <label className="meta" htmlFor={`pf-file-${method}`}>Comprobante (foto o PDF, hasta 5 MB)</label>
      <input id={`pf-file-${method}`} className="input" type="file" accept="image/png,image/jpeg,image/webp,application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      <div className="pay__form-row">
        <label><span className="meta">Número de comprobante (opcional)</span><input className="input" value={ref} maxLength={120} onChange={(e) => setRef(e.target.value)} /></label>
        <label><span className="meta">Monto pagado ({currency})</span><input className="input" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))} /></label>
      </div>
      {msg && <p style={{ color: "var(--bad)", fontSize: 12, margin: 0 }}>{msg}</p>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn btn--crimson btn--sm" disabled={busy} onClick={send}>{busy ? "Subiendo…" : "Enviar comprobante"}</button>
        <button className="btn btn--ghost btn--sm" disabled={busy} onClick={onCancel}>Cancelar</button>
      </div>
      <span className="muted" style={{ fontSize: 12 }}>Lo revisamos y aplicamos el pago a la factura. No hace falta enviarlo también por WhatsApp.</span>
    </div>
  );
}
