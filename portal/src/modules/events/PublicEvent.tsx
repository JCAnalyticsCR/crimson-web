/* Pagina publica de evento: hero, seleccion de entradas, checkout. Sin sesion. Ruta /eventos/:slug/:event. */
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError } from "../../lib/api";

/* ---------- Types ---------- */
type PublicTicketType = {
  id: number; name: string; price: number; quantity: number;
  max_per_order: number; active: boolean; available: number;
};
type PublicEventData = {
  tenant: { name: string; logo_url: string | null };
  event: {
    id: number; name: string; description: string | null;
    venue: string | null; starts_at: string; ends_at: string | null;
    image_url: string | null; currency: string; status: string;
    ticket_types: PublicTicketType[];
  };
  payment_methods: { name: string; instructions: string }[];
};
type CheckoutResult = {
  number: string; total: number; currency: string;
  free: boolean; tickets: string[]; instructions: string;
};

/* ---------- Helpers ---------- */
const money = (v: number | string, cur = "CRC") =>
  new Intl.NumberFormat("es-CR", { style: "currency", currency: cur, maximumFractionDigits: cur === "CRC" ? 0 : 2 }).format(Number(v));

const fmtEventDate = (iso: string) =>
  new Date(iso).toLocaleString("es-CR", {
    timeZone: "America/Costa_Rica",
    weekday: "long", day: "numeric", month: "long",
    year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  });

/* ---------- Style constants ---------- */
const C = "#e2233a";     // --crimson

export default function PublicEvent() {
  const { slug, event: eventParam } = useParams<{ slug: string; event: string }>();

  const [data, setData] = useState<PublicEventData | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const [qtys, setQtys] = useState<Record<number, number>>({});
  const [form, setForm] = useState({ name: "", email: "", phone: "", payment_method: "" });
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<CheckoutResult | null>(null);
  const [checkoutErr, setCheckoutErr] = useState<string | null>(null);

  useEffect(() => {
    if (!slug || !eventParam) return;
    api<PublicEventData>(`/public/events/${slug}/${eventParam}`)
      .then(setData)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setNotFound(true);
        else setErr(e instanceof Error ? e.message : "Error al cargar el evento");
      })
      .finally(() => setLoading(false));
  }, [slug, eventParam]);

  if (loading) {
    return (
      <div style={{ minHeight: "100dvh", background: "#0f0e12", display: "grid", placeItems: "center" }}>
        <span className="spinner" />
      </div>
    );
  }

  if (notFound || (!loading && !data)) {
    return (
      <div style={{ minHeight: "100dvh", background: "#0f0e12", color: "#f4f1ee", display: "grid", placeItems: "center", padding: 24 }}>
        <div style={{ textAlign: "center", maxWidth: 360 }}>
          <div style={{ fontFamily: "var(--mono)", fontSize: 11, letterSpacing: ".2em", color: C, marginBottom: 12 }}>EVENTO NO DISPONIBLE</div>
          <h1 style={{ fontFamily: "var(--display)", fontSize: 32, fontWeight: 700, marginBottom: 12 }}>
            {err ?? "Este evento no existe o ya no está disponible."}
          </h1>
          <p style={{ color: "rgba(244,241,238,.6)", fontSize: 14, lineHeight: 1.7 }}>
            Verificá el enlace o consultá con el organizador.
          </p>
        </div>
      </div>
    );
  }

  if (!data) return null;
  const { event, tenant, payment_methods } = data;

  /* totals */
  const items = event.ticket_types
    .map((tt) => ({ tt, qty: qtys[tt.id] ?? 0 }))
    .filter((x) => x.qty > 0);
  const total = items.reduce((s, x) => s + Number(x.tt.price) * x.qty, 0);
  const totalQty = items.reduce((s, x) => s + x.qty, 0);

  const setQty = (id: number, delta: number, tt: PublicTicketType) => {
    setQtys((prev) => {
      const cur = prev[id] ?? 0;
      const next = Math.max(0, Math.min(cur + delta, tt.available, tt.max_per_order));
      return { ...prev, [id]: next };
    });
  };

  const checkout = async () => {
    if (totalQty === 0) return;
    if (!form.name.trim()) { setCheckoutErr("Ingresá tu nombre"); return; }
    if (!form.email.trim() && !form.phone.trim()) { setCheckoutErr("Ingresá un correo o teléfono"); return; }
    if (payment_methods.length > 0 && !form.payment_method) { setCheckoutErr("Seleccioná un método de pago"); return; }
    setCheckoutErr(null);
    setBusy(true);
    try {
      const r = await api<CheckoutResult>(
        `/public/events/${slug}/${eventParam}/checkout`,
        {
          method: "POST",
          json: {
            items: items.map((x) => ({ ticket_type_id: x.tt.id, quantity: x.qty })),
            name: form.name,
            email: form.email || undefined,
            phone: form.phone || undefined,
            payment_method: form.payment_method || undefined,
          },
        },
      );
      setDone(r);
    } catch (e) {
      setCheckoutErr(e instanceof Error ? e.message : "No se pudo completar la compra");
    } finally {
      setBusy(false);
    }
  };

  /* ---- Header constants ---- */
  const bg = event.image_url
    ? `linear-gradient(to bottom, rgba(15,14,18,.55) 0%, rgba(15,14,18,.88) 60%, #0f0e12 100%), url("${event.image_url}") center/cover no-repeat`
    : `linear-gradient(135deg, #1a0810 0%, #0f0e12 100%)`;

  /* ===== SUCCESS SCREEN ===== */
  if (done) {
    return (
      <div style={{ minHeight: "100dvh", background: "#0f0e12", color: "#f4f1ee", display: "grid", placeItems: "center", padding: "24px 16px" }}>
        <div style={{ width: "min(520px,100%)", background: "var(--surface)", color: "var(--text)", borderRadius: "var(--r-xl)", overflow: "hidden", boxShadow: "0 40px 80px -30px rgba(0,0,0,.9)" }}>
          <div style={{ padding: "28px 28px 22px", background: `linear-gradient(135deg, #1a0810, #0f0e12)`, color: "#fff" }}>
            <div style={{ fontFamily: "var(--mono)", fontSize: 10.5, letterSpacing: ".18em", color: C, marginBottom: 6 }}>
              PEDIDO CONFIRMADO
            </div>
            <div style={{ fontFamily: "var(--display)", fontSize: 38, fontWeight: 700, letterSpacing: "-0.02em", margin: "4px 0" }}>
              {done.number}
            </div>
            <div style={{ fontFamily: "var(--mono)", fontSize: 22, fontWeight: 700, color: C }}>
              {done.free ? "GRATIS" : money(done.total, done.currency)}
            </div>
          </div>
          <div style={{ padding: "22px 28px", display: "flex", flexDirection: "column", gap: 14 }}>
            {done.free && done.tickets.length > 0 ? (
              <>
                <p style={{ fontSize: 14, color: "var(--text-2)", lineHeight: 1.6 }}>
                  Tus entradas están listas. Guardá los enlaces o imprimílos.
                </p>
                {done.tickets.map((url, i) => (
                  <a key={i} href={url} target="_blank" rel="noopener"
                    style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 14px", border: "1px solid var(--hair-2)", borderRadius: "var(--r)", textDecoration: "none", color: "var(--text)", fontWeight: 600, fontSize: 13 }}>
                    <span style={{ width: 28, height: 28, borderRadius: "50%", background: "var(--ok-soft)", color: "var(--ok)", display: "grid", placeItems: "center", fontWeight: 700, fontSize: 12, flexShrink: 0 }}>{i + 1}</span>
                    Ver entrada #{i + 1}
                  </a>
                ))}
              </>
            ) : (
              <>
                <p style={{ fontSize: 14, color: "var(--text-2)", lineHeight: 1.6 }}>
                  Una vez confirmado tu pago, recibirás las entradas por correo electrónico.
                </p>
                {done.instructions && (
                  <div style={{ padding: "14px 16px", background: "var(--surface-2)", border: "1px solid var(--hair)", borderRadius: "var(--r)", fontSize: 13, lineHeight: 1.7, color: "var(--text-2)", whiteSpace: "pre-wrap" }}>
                    {done.instructions}
                  </div>
                )}
              </>
            )}
            <p style={{ fontSize: 11.5, color: "var(--text-3)", marginTop: 4 }}>
              Evento: <b>{event.name}</b> · Organizador: {tenant.name}
            </p>
          </div>
        </div>
      </div>
    );
  }

  /* ===== MAIN EVENT PAGE ===== */
  return (
    <div style={{ minHeight: "100dvh", background: "#0f0e12", color: "#f4f1ee", fontFamily: "var(--sans)" }}>

      {/* Header sticky */}
      <header style={{ position: "sticky", top: 0, zIndex: 10, background: "rgba(15,14,18,.88)", backdropFilter: "blur(10px)", borderBottom: "1px solid rgba(255,255,255,.07)", padding: "12px 20px", display: "flex", alignItems: "center", gap: 12 }}>
        {tenant.logo_url
          ? <img src={tenant.logo_url} alt={tenant.name} style={{ height: 26, width: "auto" }} />
          : <b style={{ fontFamily: "var(--display)", fontSize: 16 }}>{tenant.name}</b>
        }
        <span style={{ color: "rgba(244,241,238,.4)", fontSize: 12 }}>·</span>
        <span style={{ fontSize: 13, color: "rgba(244,241,238,.7)", fontWeight: 600 }}>{event.name}</span>
      </header>

      {/* Hero */}
      <section style={{ background: bg, padding: "clamp(48px,8vw,96px) clamp(16px,5vw,80px) 56px" }}>
        <div style={{ maxWidth: 760, margin: "0 auto" }}>
          <div style={{ fontFamily: "var(--mono)", fontSize: 11, letterSpacing: ".18em", color: C, marginBottom: 14, textTransform: "uppercase" }}>
            {fmtEventDate(event.starts_at)}
          </div>
          <h1 style={{ fontFamily: "var(--display)", fontSize: "clamp(32px,6vw,58px)", fontWeight: 700, letterSpacing: "-0.02em", lineHeight: 1.05, margin: "0 0 16px" }}>
            {event.name}
          </h1>
          {event.venue && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", color: "rgba(244,241,238,.7)", fontSize: 15 }}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M12 22s-8-4.5-8-11.8A8 8 0 0 1 12 2a8 8 0 0 1 8 8.2c0 7.3-8 11.8-8 11.8z" /><circle cx="12" cy="10" r="3" /></svg>
              {event.venue}
            </div>
          )}
          {event.ends_at && (
            <div style={{ marginTop: 6, color: "rgba(244,241,238,.5)", fontSize: 13 }}>
              Hasta: {fmtEventDate(event.ends_at)}
            </div>
          )}
          {event.description && (
            <p style={{ marginTop: 20, color: "rgba(244,241,238,.75)", fontSize: 15, lineHeight: 1.75, maxWidth: 580, whiteSpace: "pre-wrap" }}>
              {event.description}
            </p>
          )}
        </div>
      </section>

      {/* Content — ticket selector + form */}
      <section style={{ padding: "0 clamp(16px,5vw,80px) 80px", maxWidth: 920, margin: "0 auto" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr min(380px,100%)", gap: 28, alignItems: "start" }}>

          {/* Ticket types */}
          <div>
            <h2 style={{ fontFamily: "var(--display)", fontSize: 22, fontWeight: 700, margin: "32px 0 18px" }}>Entradas</h2>

            {event.ticket_types.filter((tt) => tt.active).length === 0 ? (
              <p style={{ color: "rgba(244,241,238,.5)", fontSize: 14 }}>No hay entradas disponibles en este momento.</p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {event.ticket_types.filter((tt) => tt.active).map((tt) => {
                  const qty = qtys[tt.id] ?? 0;
                  const soldOut = tt.available <= 0;
                  return (
                    <div key={tt.id} style={{
                      padding: "16px 18px", borderRadius: "var(--r-lg)",
                      background: "rgba(255,255,255,.05)", border: "1px solid rgba(255,255,255,.1)",
                      display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap",
                      opacity: soldOut ? 0.5 : 1,
                    }}>
                      <div style={{ flex: 1, minWidth: 180 }}>
                        <b style={{ fontSize: 15 }}>{tt.name}</b>
                        <div style={{ fontFamily: "var(--mono)", fontSize: 18, fontWeight: 700, color: C, marginTop: 4 }}>
                          {Number(tt.price) === 0 ? "GRATIS" : money(tt.price, event.currency)}
                        </div>
                        {soldOut
                          ? <div style={{ fontSize: 12, color: C, marginTop: 2 }}>Agotado</div>
                          : <div style={{ fontSize: 12, color: "rgba(244,241,238,.45)", marginTop: 2 }}>{tt.available} disponibles · máx. {tt.max_per_order} por orden</div>
                        }
                      </div>
                      {!soldOut && (
                        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                          <button
                            onClick={() => setQty(tt.id, -1, tt)}
                            disabled={qty <= 0}
                            style={{ width: 36, height: 36, borderRadius: "50%", border: "1.5px solid rgba(255,255,255,.25)", background: "transparent", color: "#fff", fontSize: 20, cursor: qty <= 0 ? "not-allowed" : "pointer", display: "grid", placeItems: "center", opacity: qty <= 0 ? 0.3 : 1, transition: "opacity .15s" }}
                          >−</button>
                          <span style={{ fontFamily: "var(--mono)", fontSize: 22, fontWeight: 700, minWidth: 28, textAlign: "center" }}>{qty}</span>
                          <button
                            onClick={() => setQty(tt.id, +1, tt)}
                            disabled={qty >= tt.available || qty >= tt.max_per_order}
                            style={{ width: 36, height: 36, borderRadius: "50%", border: "1.5px solid rgba(255,255,255,.25)", background: "transparent", color: "#fff", fontSize: 20, cursor: qty >= tt.available || qty >= tt.max_per_order ? "not-allowed" : "pointer", display: "grid", placeItems: "center", opacity: qty >= tt.available || qty >= tt.max_per_order ? 0.3 : 1, transition: "opacity .15s" }}
                          >+</button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Checkout form — sticky on desktop */}
          <div style={{ position: "sticky", top: "calc(60px + 16px)" }}>
            <div style={{
              background: "var(--surface)", color: "var(--text)", borderRadius: "var(--r-xl)",
              overflow: "hidden", boxShadow: "0 30px 60px -20px rgba(0,0,0,.6)", marginTop: 32,
            }}>
              <div style={{ padding: "18px 20px", background: "#15131a", color: "#f4f1ee", borderBottom: "1px solid rgba(255,255,255,.07)" }}>
                <div style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: ".16em", color: "rgba(244,241,238,.5)" }}>RESUMEN</div>
                {items.length > 0 ? (
                  <>
                    {items.map((x) => (
                      <div key={x.tt.id} style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginTop: 8 }}>
                        <span>{x.qty}× {x.tt.name}</span>
                        <span style={{ fontFamily: "var(--mono)", color: C }}>{money(Number(x.tt.price) * x.qty, event.currency)}</span>
                      </div>
                    ))}
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 19, fontWeight: 700, marginTop: 12, paddingTop: 10, borderTop: "1px solid rgba(255,255,255,.1)" }}>
                      <span>Total</span>
                      <span style={{ fontFamily: "var(--mono)", color: C }}>{total === 0 ? "GRATIS" : money(total, event.currency)}</span>
                    </div>
                  </>
                ) : (
                  <p style={{ fontSize: 13, marginTop: 10, color: "rgba(244,241,238,.45)" }}>Seleccioná al menos una entrada para continuar.</p>
                )}
              </div>

              <div style={{ padding: "18px 20px", display: "flex", flexDirection: "column", gap: 12 }}>
                {/* Contact form */}
                <input
                  className="input"
                  placeholder="Nombre completo *"
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                />
                <input
                  className="input"
                  type="email"
                  placeholder="Correo electrónico"
                  value={form.email}
                  onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                />
                <input
                  className="input"
                  placeholder="Teléfono"
                  value={form.phone}
                  onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))}
                />

                {/* Payment method */}
                {payment_methods.length > 0 && total > 0 && (
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-2)", marginBottom: 8 }}>Método de pago</div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {payment_methods.map((m) => (
                        <label key={m.name} style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", padding: "10px 12px", borderRadius: "var(--r)", border: `1.5px solid ${form.payment_method === m.name ? C : "var(--hair-2)"}`, background: form.payment_method === m.name ? "var(--crimson-soft)" : "var(--surface-2)", transition: "border-color .15s, background .15s" }}>
                          <input
                            type="radio"
                            name="payment"
                            value={m.name}
                            checked={form.payment_method === m.name}
                            onChange={() => setForm((f) => ({ ...f, payment_method: m.name }))}
                            style={{ marginTop: 2, accentColor: C }}
                          />
                          <div>
                            <b style={{ fontSize: 13 }}>{m.name}</b>
                            {m.instructions && <p style={{ margin: "3px 0 0", fontSize: 11.5, color: "var(--text-3)", lineHeight: 1.5 }}>{m.instructions}</p>}
                          </div>
                        </label>
                      ))}
                    </div>
                  </div>
                )}

                {checkoutErr && (
                  <p style={{ fontSize: 13, color: "var(--bad)", margin: 0 }}>{checkoutErr}</p>
                )}

                <button
                  disabled={totalQty === 0 || busy}
                  onClick={checkout}
                  style={{
                    height: 46, border: 0, borderRadius: "var(--r-lg)", background: C, color: "#fff",
                    fontWeight: 700, fontSize: 15, cursor: totalQty === 0 || busy ? "not-allowed" : "pointer",
                    opacity: totalQty === 0 ? 0.45 : 1, transition: "opacity .15s",
                    display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                  }}
                >
                  {busy ? <span className="spinner" style={{ width: 16, height: 16 }} /> : null}
                  {totalQty === 0 ? "Elegí tus entradas" : `Comprar ${totalQty} entrada${totalQty > 1 ? "s" : ""}`}
                </button>
                <p style={{ fontSize: 11, color: "var(--text-3)", textAlign: "center", margin: 0 }}>
                  {tenant.name} · Seguro y verificado
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      <footer style={{ borderTop: "1px solid rgba(255,255,255,.07)", padding: "24px 24px", textAlign: "center", color: "rgba(244,241,238,.3)", fontSize: 12.5 }}>
        {event.name} · {tenant.name}
      </footer>
    </div>
  );
}
