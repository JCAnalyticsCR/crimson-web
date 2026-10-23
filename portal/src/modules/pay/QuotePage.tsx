/* Ventas etapa 3 · página pública de una cotización (sin sesión): el cliente la ve tal como se le envió (última
   versión) y la acepta con su nombre y su cédula. Nunca muestra costos, márgenes ni notas internas: la API no los manda.
   Aceptar no gana la venta: el vendedor recibe el aviso y la tarea de facturar. Funciona en teléfono y tablet. */
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { fmtDate, fmtMoney } from "../../lib/api";

type Line = { name: string; description: string | null; quantity: string; unit_price: string; discount_type: string; discount_value: string; total: string; treatment?: string | null };
type Q = {
  company: { name: string; logo_url: string | null }; customer: { name: string | null }; version: number | null;
  number: string; currency: string; issue_date: string | null; due_date: string | null; subtotal: string; discount_total: string; tax_total: string; total: string; external_notes: string | null;
  lines: Line[]; footer: string | null; can_accept: boolean; why_not: string | null; accepted: { name: string | null; at: string | null } | null;
};

export default function QuotePage() {
  const { token } = useParams();
  const [d, setD] = useState<Q | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [f, setF] = useState({ name: "", id_number: "", ok: false });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    fetch(`/api/public/quote/${token}`).then(async (r) => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || "Enlace no disponible"); setD(j); }).catch((e) => setErr(e.message));
  }, [token]);

  const accept = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await fetch(`/api/public/quote/${token}/accept`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: f.name.trim(), id_number: f.id_number.trim() }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { setMsg(typeof j.detail === "string" ? j.detail : "Revise su nombre y su cédula."); return; }
      setD(j);
    } catch { setMsg("Error de conexión. Intente de nuevo."); } finally { setBusy(false); }
  };

  const cur = d?.currency || "CRC";
  const listo = f.name.trim().length >= 3 && f.id_number.replace(/[^0-9A-Za-z]/g, "").length >= 9 && f.ok;
  return (
    <div className="pay">
      <div className="pay__card">
        {err && <div className="empty"><div className="h2">Enlace no disponible</div><p className="muted">{err}</p></div>}
        {d && (
          <>
            <div className="pay__head">
              <div>
                {d.company.logo_url && <img className="pay__logo" src={d.company.logo_url} alt={d.company.name} />}
                <div className="meta" style={{ color: "rgba(255,255,255,.6)" }}>{d.company.name} · Cotización {d.number}{d.version ? ` · v${d.version}` : ""}</div>
                <div className="pay__amount">{fmtMoney(d.total, cur)}</div>
                <div style={{ fontSize: 13, opacity: .75 }}>{d.due_date ? `Válida hasta el ${fmtDate(d.due_date)}` : "Total con impuestos"}</div>
              </div>
            </div>
            <div style={{ padding: 22, display: "flex", flexDirection: "column", gap: 16 }}>
              {d.customer.name && <p className="muted" style={{ margin: 0 }}>Hola <b style={{ color: "var(--text)" }}>{d.customer.name}</b>, este es el detalle de su cotización.</p>}
              <table className="table"><tbody>{d.lines.map((l, i) => (
                <tr key={i}>
                  <td>{l.name}{l.description && <div className="muted" style={{ fontSize: 12 }}>{l.description}</div>}<div className="meta">{Number(l.quantity)} × {fmtMoney(l.unit_price, cur)}{Number(l.discount_value) > 0 ? ` · descuento ${l.discount_type === "percent" ? `${Number(l.discount_value)} %` : fmtMoney(l.discount_value, cur)}` : ""}</div></td>
                  <td className="num money">{fmtMoney(l.total, cur)}</td>
                </tr>
              ))}</tbody></table>
              <dl className="pay__details">
                <div><dt>Subtotal</dt><dd className="money">{fmtMoney(d.subtotal, cur)}</dd></div>
                {Number(d.discount_total) > 0 && <div><dt>Descuento</dt><dd className="money">−{fmtMoney(d.discount_total, cur)}</dd></div>}
                <div><dt>Impuestos</dt><dd className="money">{fmtMoney(d.tax_total, cur)}</dd></div>
                <div><dt><b>Total</b></dt><dd className="money"><b>{fmtMoney(d.total, cur)}</b></dd></div>
              </dl>
              {d.external_notes && <p style={{ whiteSpace: "pre-wrap", fontSize: 13, margin: 0 }}>{d.external_notes}</p>}
              <div><a className="btn btn--soft btn--sm" href={`/api/public/quote/${token}/pdf`} target="_blank" rel="noopener">Ver el documento completo</a></div>

              {d.accepted ? (
                <div style={{ background: "var(--bg-2)", padding: "12px 14px", borderRadius: 10, fontSize: 14 }}>
                  <b>Cotización aceptada.</b> {d.accepted.name ? `La aceptó ${d.accepted.name}` : ""}{d.accepted.at ? ` el ${new Date(d.accepted.at).toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}` : ""}. Su vendedor le contactará para la factura.
                </div>
              ) : d.can_accept ? (
                <div className="pay__form">
                  <div className="meta">Aceptar la cotización</div>
                  <div className="pay__form-row">
                    <label><span className="meta">Nombre completo</span><input className="input" autoComplete="name" maxLength={160} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
                    <label><span className="meta">Número de cédula</span><input className="input" maxLength={30} inputMode="text" value={f.id_number} onChange={(e) => setF({ ...f, id_number: e.target.value })} /></label>
                  </div>
                  <label style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 13 }}>
                    <input type="checkbox" checked={f.ok} onChange={(e) => setF({ ...f, ok: e.target.checked })} style={{ marginTop: 3 }} />
                    <span>Acepto esta cotización por {fmtMoney(d.total, cur)} en nombre de {d.customer.name || "mi empresa"}. Queda registrado mi nombre, mi cédula, la fecha y la dirección desde donde la acepto.</span>
                  </label>
                  {msg && <p style={{ color: "var(--bad)", fontSize: 12, margin: 0 }} role="alert">{msg}</p>}
                  <div><button className="btn btn--crimson" disabled={busy || !listo} onClick={accept}>{busy ? "Registrando…" : "Aceptar cotización"}</button></div>
                </div>
              ) : (
                d.why_not && <p className="muted" style={{ margin: 0 }}>{d.why_not}</p>
              )}
              {d.footer && <p className="muted" style={{ fontSize: 12, margin: 0, whiteSpace: "pre-wrap" }}>{d.footer}</p>}
              <p className="muted" style={{ fontSize: 12, margin: 0 }}>Este enlace es único y vence con la validez de la cotización. Si tiene dudas, escríbale a {d.company.name}.</p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
