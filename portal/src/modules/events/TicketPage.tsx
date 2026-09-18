/* Entrada digital publica (boarding pass). Ruta /entrada/:code. Sin sesion. */
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../../lib/api";

/* ---------- Types ---------- */
type TicketData = {
  code: string;
  status: string;
  holder_name: string;
  type: string;
  event: { name: string; venue: string | null; starts_at: string; image_url: string | null };
  tenant: { name: string; logo_url: string | null };
  qr_svg: string | null;
};

/* ---------- Helpers ---------- */
const fmtEventDate = (iso: string) =>
  new Date(iso).toLocaleString("es-CR", {
    timeZone: "America/Costa_Rica",
    weekday: "long", day: "numeric", month: "long",
    year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  });

const STATUS_DISPLAY: Record<string, { label: string; color: string; bg: string; icon: string }> = {
  valido: {
    label: "Entrada válida",
    color: "#1f9d55",
    bg: "rgba(31,157,85,.14)",
    icon: "M5 12l5 5 9-10",
  },
  usado: {
    label: "Ya utilizada",
    color: "#8a858f",
    bg: "rgba(138,133,143,.12)",
    icon: "M5 12l5 5 9-10",
  },
  pendiente: {
    label: "Pago pendiente — la entrada se activa al confirmar el pago",
    color: "#d98a12",
    bg: "rgba(217,138,18,.14)",
    icon: "M12 8v4l3 3|M12 2a10 10 0 1 0 0 20A10 10 0 0 0 12 2z",
  },
  anulado: {
    label: "Entrada anulada",
    color: "#e2233a",
    bg: "rgba(226,35,58,.14)",
    icon: "M18 6L6 18|M6 6l12 12",
  },
};

export default function TicketPage() {
  const { code } = useParams<{ code: string }>();
  const [ticket, setTicket] = useState<TicketData | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!code) return;
    api<TicketData>(`/public/tickets/${code}`)
      .then(setTicket)
      .catch((e) => setErr(e instanceof Error ? e.message : "Entrada no encontrada"))
      .finally(() => setLoading(false));
  }, [code]);

  if (loading) {
    return (
      <div style={{ minHeight: "100dvh", background: "#f3efea", display: "grid", placeItems: "center" }}>
        <span className="spinner" />
      </div>
    );
  }

  if (err || !ticket) {
    return (
      <div style={{ minHeight: "100dvh", background: "#f3efea", display: "grid", placeItems: "center", padding: 24 }}>
        <div style={{ textAlign: "center", maxWidth: 340 }}>
          <div style={{ fontSize: 40, marginBottom: 12 }}>🎟</div>
          <h1 style={{ fontFamily: "var(--display)", fontSize: 26, fontWeight: 700, marginBottom: 10 }}>Entrada no encontrada</h1>
          <p style={{ color: "var(--text-2)", fontSize: 14, lineHeight: 1.7 }}>{err ?? "El código no corresponde a ninguna entrada válida."}</p>
        </div>
      </div>
    );
  }

  const st = STATUS_DISPLAY[ticket.status] ?? STATUS_DISPLAY.anulado;

  return (
    <>
      {/* Estilos de impresión inline */}
      <style>{`
        @media print {
          .no-print { display: none !important; }
          body { background: white !important; }
          .ticket-card { box-shadow: none !important; max-width: 100% !important; }
        }
        @media screen {
          body { background: var(--bg); }
        }
      `}</style>

      <div style={{ minHeight: "100dvh", background: "#f3efea", padding: "clamp(16px,4vw,40px)", display: "flex", flexDirection: "column", alignItems: "center", gap: 16 }}>

        {/* Print button */}
        <div className="no-print" style={{ width: "min(520px,100%)", display: "flex", justifyContent: "flex-end" }}>
          <button
            onClick={() => window.print()}
            style={{ height: 34, padding: "0 14px", borderRadius: 8, border: "1px solid var(--hair-2)", background: "var(--surface)", color: "var(--text)", fontSize: 13, fontWeight: 600, cursor: "pointer", display: "flex", gap: 7, alignItems: "center" }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path d="M6 9V2h12v7|M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2|M6 14h12v8H6z" />
            </svg>
            Imprimir
          </button>
        </div>

        {/* Ticket card (boarding-pass style) */}
        <div className="ticket-card" style={{
          width: "min(520px,100%)", background: "var(--surface)",
          borderRadius: 20, overflow: "hidden",
          boxShadow: "0 30px 60px -20px rgba(21,19,26,.35)",
          border: "1px solid var(--hair)",
        }}>
          {/* Status banner */}
          <div style={{ padding: "14px 20px", background: st.bg, display: "flex", gap: 10, alignItems: "flex-start" }}>
            <div style={{ width: 32, height: 32, borderRadius: "50%", background: st.color, color: "#fff", flexShrink: 0, display: "grid", placeItems: "center" }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                {st.icon.split("|").map((p, i) => <path key={i} d={p} />)}
              </svg>
            </div>
            <p style={{ margin: 0, fontWeight: 700, fontSize: 14, color: st.color, lineHeight: 1.4 }}>
              {st.label}
            </p>
          </div>

          {/* Event image */}
          {ticket.event.image_url && (
            <div style={{ height: 160, background: `center/cover no-repeat url("${ticket.event.image_url}")` }} />
          )}

          {/* Tenant + event info */}
          <div style={{ padding: "18px 22px 0" }}>
            <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 14 }}>
              {ticket.tenant.logo_url
                ? <img src={ticket.tenant.logo_url} alt={ticket.tenant.name} style={{ height: 22, width: "auto" }} />
                : <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, letterSpacing: ".14em", textTransform: "uppercase", color: "var(--text-3)" }}>{ticket.tenant.name}</span>
              }
            </div>
            <h1 style={{ fontFamily: "var(--display)", fontSize: "clamp(20px,4vw,26px)", fontWeight: 700, letterSpacing: "-0.015em", margin: "0 0 6px" }}>
              {ticket.event.name}
            </h1>
            <div style={{ fontSize: 13, color: "var(--text-2)", lineHeight: 1.6 }}>
              {fmtEventDate(ticket.event.starts_at)}
            </div>
            {ticket.event.venue && (
              <div style={{ fontSize: 13, color: "var(--text-3)", marginTop: 2 }}>{ticket.event.venue}</div>
            )}
          </div>

          {/* Tear line */}
          <div style={{ margin: "18px 0", display: "flex", alignItems: "center", gap: 0, position: "relative" }}>
            <div style={{ width: 20, height: 20, borderRadius: "50%", background: "#f3efea", flexShrink: 0, marginLeft: -10 }} />
            <div style={{ flex: 1, borderTop: "2px dashed var(--hair-2)" }} />
            <div style={{ width: 20, height: 20, borderRadius: "50%", background: "#f3efea", flexShrink: 0, marginRight: -10 }} />
          </div>

          {/* Holder + type + code */}
          <div style={{ padding: "0 22px 20px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            <div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 9.5, letterSpacing: ".16em", textTransform: "uppercase", color: "var(--text-3)", marginBottom: 4 }}>Titular</div>
              <b style={{ fontSize: 14 }}>{ticket.holder_name}</b>
            </div>
            <div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 9.5, letterSpacing: ".16em", textTransform: "uppercase", color: "var(--text-3)", marginBottom: 4 }}>Tipo</div>
              <b style={{ fontSize: 14 }}>{ticket.type}</b>
            </div>
            <div style={{ gridColumn: "1/-1" }}>
              <div style={{ fontFamily: "var(--mono)", fontSize: 9.5, letterSpacing: ".16em", textTransform: "uppercase", color: "var(--text-3)", marginBottom: 4 }}>Código</div>
              <span style={{ fontFamily: "var(--mono)", fontSize: "clamp(14px,3.5vw,18px)", fontWeight: 700, letterSpacing: ".06em", color: "#15131a", wordBreak: "break-all" }}>{ticket.code}</span>
            </div>
          </div>

          {/* QR — generated server-side by segno library from our own entry URL */}
          {ticket.qr_svg && (
            <div style={{ padding: "0 22px 24px", display: "flex", justifyContent: "center" }}>
              <div
                style={{ width: "min(180px,55%)", aspectRatio: "1" }}
                dangerouslySetInnerHTML={{ __html: ticket.qr_svg }}
              />
            </div>
          )}
        </div>

        <p className="no-print" style={{ fontSize: 12, color: "var(--text-3)", textAlign: "center", maxWidth: 380 }}>
          Presentá este código en la entrada. {ticket.tenant.name} verifica la autenticidad del QR.
        </p>
      </div>
    </>
  );
}
