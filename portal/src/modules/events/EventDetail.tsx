/* Detalle de evento (admin): KPIs, tabs entradas / cortesias / acceso. Ruta /eventos/:id. */
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, fmtDate } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon } from "../../ui/components";
import { EventEditor, type EventData } from "./Events";

/* ---------- Types locales ---------- */
type Ticket = {
  id: number; code: string; holder_name: string; holder_email: string | null;
  type: string; status: string; order_id: number | null;
  checked_in_at: string | null; url: string;
};
type CheckinResult = {
  ok: boolean; reason: string; code: string;
  holder_name: string; type: string; event: string;
};
type ScanRecord = { code: string; result: CheckinResult; ts: number };
type CourtesyForm = {
  ticket_type_id: number; holder_name: string; holder_email: string; quantity: number;
};

/* ---------- Helpers ---------- */
const TICKET_STATUS: Record<string, { label: string; tone: string }> = {
  pendiente: { label: "Pendiente", tone: "warn" },
  valido:    { label: "Válida",    tone: "ok"   },
  usado:     { label: "Usada",     tone: "muted" },
  anulado:   { label: "Anulada",   tone: "bad"  },
};

function TicketBadge({ status }: { status: string }) {
  const s = TICKET_STATUS[status] ?? { label: status, tone: "muted" };
  return <span className={`badge badge--${s.tone}`}>{s.label}</span>;
}

const fmtDT = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleString("es-CR", {
        timeZone: "America/Costa_Rica",
        day: "2-digit", month: "short", year: "numeric",
        hour: "2-digit", minute: "2-digit", hour12: false,
      })
    : "—";

/* ---------- BarcodeDetector types (WICG API, no está en @types/dom aún) ---------- */
interface BarcodeDetectorResult { rawValue: string }
interface IBarcodeDetector {
  detect(source: HTMLVideoElement): Promise<BarcodeDetectorResult[]>;
}
interface BarcodeDetectorCtor { new(opts: { formats: string[] }): IBarcodeDetector }

const getBarcodeDetector = () =>
  "BarcodeDetector" in window
    ? (window as unknown as { BarcodeDetector: BarcodeDetectorCtor }).BarcodeDetector
    : null;

/* ====================================================== */
export default function EventDetail() {
  const { id } = useParams<{ id: string }>();
  const { toast } = useSession();

  const [event, setEvent] = useState<EventData | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [tab, setTab] = useState<"entradas" | "cortesias" | "acceso">("entradas");

  /* entradas tab */
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [ticketsLoading, setTicketsLoading] = useState(false);
  const [ticketQ, setTicketQ] = useState("");

  /* cortesías tab */
  const blankCourtesy = (): CourtesyForm => ({
    ticket_type_id: 0, holder_name: "", holder_email: "", quantity: 1,
  });
  const [courtesy, setCourtesy] = useState<CourtesyForm>(blankCourtesy);
  const [courtesyBusy, setCourtesyBusy] = useState(false);
  const [courtesyResult, setCourtesyResult] = useState<{ code: string; url: string }[]>([]);

  /* acceso tab */
  const [checkCode, setCheckCode] = useState("");
  const [checkinResult, setCheckinResult] = useState<CheckinResult | null>(null);
  const [recentScans, setRecentScans] = useState<ScanRecord[]>([]);
  const [cameraActive, setCameraActive] = useState(false);
  const [cameraError, setCameraError] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const checkinFnRef = useRef<((code: string) => void) | null>(null);
  const checkInputRef = useRef<HTMLInputElement>(null);

  /* ---------- Load event ---------- */
  const loadEvent = useCallback(() => {
    if (!id) return;
    setLoading(true);
    api<EventData>(`/events/${id}`)
      .then(setEvent)
      .catch((e) => toast(e instanceof Error ? e.message : "Error al cargar el evento", "bad"))
      .finally(() => setLoading(false));
  }, [id, toast]);

  useEffect(() => { loadEvent(); }, [loadEvent]);

  /* ---------- Load tickets ---------- */
  const loadTickets = useCallback(() => {
    if (!id) return;
    setTicketsLoading(true);
    api<Ticket[]>(`/events/${id}/tickets${ticketQ ? `?q=${encodeURIComponent(ticketQ)}` : ""}`)
      .then(setTickets)
      .catch(() => setTickets([]))
      .finally(() => setTicketsLoading(false));
  }, [id, ticketQ]);

  useEffect(() => {
    if (tab === "entradas") {
      const t = setTimeout(loadTickets, 200);
      return () => clearTimeout(t);
    }
  }, [tab, loadTickets]);

  /* ---------- Check-in handler ---------- */
  const handleCheckin = useCallback(
    async (code: string) => {
      if (!id || !code.trim()) return;
      try {
        const r = await api<CheckinResult>("/events/checkin", {
          method: "POST",
          json: { code: code.trim(), event_id: Number(id) },
        });
        setCheckinResult(r);
        setRecentScans((prev) => [{ code: code.trim(), result: r, ts: Date.now() }, ...prev.slice(0, 9)]);
        setCheckCode("");
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Error al validar";
        setCheckinResult({ ok: false, reason: msg, code: code.trim(), holder_name: "", type: "", event: "" });
      }
    },
    [id],
  );

  /* keep ref fresh so the camera effect loop can call the latest version */
  useEffect(() => { checkinFnRef.current = (code) => { void handleCheckin(code); }; });

  /* ---------- Camera effect ---------- */
  useEffect(() => {
    if (!cameraActive) return;
    setCameraError(null);
    let running = true;
    const lastCode = { val: "" };
    const lastTs = { val: 0 };

    const BD = getBarcodeDetector();
    if (!BD) {
      setCameraError("Tu navegador no soporta BarcodeDetector. Escribí o escaneá con un lector USB.");
      setCameraActive(false);
      return;
    }

    const detector = new BD({ formats: ["qr_code"] });

    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment" } })
      .then((stream) => {
        if (!running) { stream.getTracks().forEach((t) => t.stop()); return; }
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          videoRef.current.play().catch(() => undefined);
        }

        const scan = async () => {
          if (!running) return;
          if (videoRef.current && videoRef.current.readyState >= 2) {
            try {
              const results = await detector.detect(videoRef.current);
              for (const r of results) {
                const now = Date.now();
                if (r.rawValue !== lastCode.val || now - lastTs.val > 3000) {
                  lastCode.val = r.rawValue;
                  lastTs.val = now;
                  checkinFnRef.current?.(r.rawValue);
                }
              }
            } catch { /* frame not ready */ }
          }
          setTimeout(scan, 300);
        };
        void scan();
      })
      .catch(() => {
        if (running) setCameraError("No se pudo acceder a la cámara. Verificá los permisos del navegador.");
      });

    return () => {
      running = false;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
  }, [cameraActive]);

  /* ---------- Void ticket ---------- */
  const voidTicket = async (tid: number) => {
    if (!confirm("¿Anular esta entrada? Esta acción no se puede deshacer.")) return;
    try {
      await api(`/tickets/${tid}/void`, { method: "POST" });
      toast("Entrada anulada");
      loadTickets();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Error al anular", "bad");
    }
  };

  /* ---------- Courtesy ---------- */
  const sendCourtesy = async () => {
    if (!id) return;
    if (!courtesy.holder_name.trim()) { toast("Ingresá el nombre del titular", "bad"); return; }
    if (!courtesy.ticket_type_id) { toast("Seleccioná el tipo de entrada", "bad"); return; }
    setCourtesyBusy(true);
    try {
      const body = {
        ticket_type_id: courtesy.ticket_type_id,
        holder_name: courtesy.holder_name,
        ...(courtesy.holder_email ? { holder_email: courtesy.holder_email } : {}),
        quantity: courtesy.quantity,
      };
      const result = await api<{ code: string; url: string }[]>(`/events/${id}/tickets`, { method: "POST", json: body });
      setCourtesyResult(result);
      setCourtesy(blankCourtesy());
      toast("Cortesía generada");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Error al generar cortesía", "bad");
    } finally {
      setCourtesyBusy(false);
    }
  };

  /* ---------- Copy public URL ---------- */
  const copyPublicUrl = () => {
    if (!event) return;
    navigator.clipboard.writeText(event.public_url).then(
      () => toast("Enlace copiado"),
      () => toast("No se pudo copiar al portapapeles", "bad"),
    );
  };

  /* =========================================================
     RENDER
     ========================================================= */
  if (loading) {
    return (
      <div style={{ padding: 60, display: "grid", placeItems: "center" }}>
        <span className="spinner" />
      </div>
    );
  }
  if (!event) return <Empty title="Evento no encontrado" />;

  const vendidas = event.stats.valido + event.stats.usado + event.stats.pendiente;
  const disponibles = event.ticket_types.reduce((s, t) => s + Number(t.available), 0);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="meta">Eventos · #{event.id}</div>
          <h1 className="h1">{event.name}</h1>
          {event.venue && <p className="muted" style={{ marginTop: 4, fontSize: 13 }}>{event.venue} · {fmtDT(event.starts_at)}</p>}
        </div>
        <div className="page-head__actions">
          <button className="btn btn--ghost btn--sm" onClick={copyPublicUrl}>
            <Icon d={I.copy} />Copiar enlace público
          </button>
          <button className="btn btn--ghost btn--sm" onClick={() => setEditing(true)}>
            <Icon d={I.settings} />Editar
          </button>
        </div>
      </div>

      {/* KPIs */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
        {[
          { label: "Vendidas", value: vendidas },
          { label: "Ingresaron", value: event.stats.usado },
          { label: "Pago pendiente", value: event.stats.pendiente },
          { label: "Disponibles", value: disponibles },
        ].map((k) => (
          <div key={k.label} style={{ padding: "16px 18px", borderRadius: "var(--r-lg)", background: "var(--ink)", color: "#fff", border: "1px solid rgba(255,255,255,.06)" }}>
            <div style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: ".16em", textTransform: "uppercase", opacity: .6 }}>{k.label}</div>
            <div style={{ fontFamily: "var(--display)", fontSize: 36, fontWeight: 700, letterSpacing: "-0.03em", lineHeight: 1, margin: "8px 0 0" }}>{k.value}</div>
          </div>
        ))}
      </div>

      {/* Tabs */}
      <Card flush>
        <div className="list-head">
          <div className="tabs">
            {(["entradas", "cortesias", "acceso"] as const).map((t) => (
              <button key={t} className={tab === t ? "is-active" : ""} onClick={() => setTab(t)}>
                {t === "entradas" ? "Entradas" : t === "cortesias" ? "Cortesías" : "Control de acceso"}
              </button>
            ))}
          </div>
        </div>

        {/* ---- Entradas ---- */}
        {tab === "entradas" && (
          <>
            <div className="list-head" style={{ borderTop: 0 }}>
              <div className="search" style={{ maxWidth: 380 }}>
                <Icon d={I.search} size={16} />
                <input placeholder="Buscar por nombre, correo o código…" value={ticketQ} onChange={(e) => setTicketQ(e.target.value)} />
              </div>
              <button className="btn btn--ghost btn--sm" onClick={loadTickets}><Icon d={I.refresh} /></button>
            </div>
            {ticketsLoading ? (
              <div style={{ padding: 36, display: "grid", placeItems: "center" }}><span className="spinner" /></div>
            ) : tickets.length === 0 ? (
              <Empty hint="No hay entradas que coincidan." />
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Código</th>
                    <th>Titular</th>
                    <th>Tipo</th>
                    <th>Estado</th>
                    <th>Ingreso</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {tickets.map((tk) => (
                    <tr key={tk.id}>
                      <td className="mono" style={{ fontWeight: 600, fontSize: 12 }}>{tk.code}</td>
                      <td>
                        {tk.holder_name}
                        {tk.holder_email && <div className="meta" style={{ textTransform: "none" }}>{tk.holder_email}</div>}
                      </td>
                      <td className="muted">{tk.type}</td>
                      <td><TicketBadge status={tk.status} /></td>
                      <td className="muted" style={{ fontSize: 12 }}>
                        {tk.checked_in_at ? fmtDate(tk.checked_in_at) : "—"}
                      </td>
                      <td className="num" style={{ whiteSpace: "nowrap" }}>
                        <a className="btn btn--ghost btn--sm" href={tk.url} target="_blank" rel="noopener">
                          <Icon d={I.link} size={13} />Ver
                        </a>
                        {tk.status !== "anulado" && (
                          <button className="btn btn--danger btn--sm" style={{ marginLeft: 4 }} onClick={() => voidTicket(tk.id)}>
                            Anular
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}

        {/* ---- Cortesías ---- */}
        {tab === "cortesias" && (
          <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 16, maxWidth: 520 }}>
            <p className="muted" style={{ fontSize: 13 }}>Generá entradas gratuitas para invitados, prensa o colaboradores.</p>
            <div className="grid-2">
              <Field label="Tipo de entrada">
                <select
                  className="select"
                  value={courtesy.ticket_type_id}
                  onChange={(e) => setCourtesy((f) => ({ ...f, ticket_type_id: Number(e.target.value) }))}
                >
                  <option value={0}>Seleccioná…</option>
                  {event.ticket_types.map((tt) => (
                    <option key={tt.id} value={tt.id}>{tt.name}</option>
                  ))}
                </select>
              </Field>
              <Field label="Cantidad">
                <input
                  className="input input--mono"
                  type="number" min={1} max={50}
                  value={courtesy.quantity}
                  onChange={(e) => setCourtesy((f) => ({ ...f, quantity: Math.max(1, Number(e.target.value)) }))}
                />
              </Field>
            </div>
            <Field label="Nombre del titular">
              <input className="input" value={courtesy.holder_name} onChange={(e) => setCourtesy((f) => ({ ...f, holder_name: e.target.value }))} placeholder="Nombre completo" />
            </Field>
            <Field label="Correo (opcional)" hint="Si lo agregás, se le envía la entrada automáticamente.">
              <input className="input" type="email" value={courtesy.holder_email} onChange={(e) => setCourtesy((f) => ({ ...f, holder_email: e.target.value }))} placeholder="correo@ejemplo.com" />
            </Field>
            <button className="btn btn--crimson" onClick={sendCourtesy} disabled={courtesyBusy} style={{ alignSelf: "flex-start" }}>
              {courtesyBusy ? <span className="spinner" style={{ width: 15, height: 15 }} /> : <Icon d={I.plus} />}
              Generar cortesía
            </button>

            {courtesyResult.length > 0 && (
              <div style={{ background: "var(--ok-soft)", borderRadius: "var(--r)", padding: "14px 16px" }}>
                <div className="meta" style={{ color: "var(--ok)", marginBottom: 6 }}>Entradas generadas</div>
                {courtesyResult.map((tk) => (
                  <div key={tk.code} style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 4 }}>
                    <span className="mono" style={{ fontSize: 12.5, fontWeight: 600 }}>{tk.code}</span>
                    <a href={tk.url} target="_blank" rel="noopener" className="btn btn--ghost btn--sm">
                      <Icon d={I.link} size={13} />Ver entrada
                    </a>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ---- Control de acceso ---- */}
        {tab === "acceso" && (
          <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: 240 }}>
                <Field label="Código o URL de la entrada">
                  <input
                    ref={checkInputRef}
                    className="input input--mono"
                    value={checkCode}
                    onChange={(e) => setCheckCode(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") void handleCheckin(checkCode); }}
                    placeholder="Escribí, pegá o escaneá…"
                    autoFocus={tab === "acceso"}
                  />
                </Field>
              </div>
              <button className="btn btn--crimson" onClick={() => void handleCheckin(checkCode)} disabled={!checkCode.trim()}>
                <Icon d={I.check} />Validar
              </button>
              <button
                className={`btn ${cameraActive ? "btn--danger" : "btn--ghost"} btn--sm`}
                onClick={() => setCameraActive((v) => !v)}
              >
                {cameraActive ? "Apagar cámara" : "Activar cámara QR"}
              </button>
            </div>

            {/* Camera */}
            {cameraActive && (
              <div style={{ maxWidth: 380 }}>
                <video
                  ref={videoRef}
                  muted
                  playsInline
                  style={{ width: "100%", borderRadius: "var(--r-lg)", border: "2px solid var(--crimson)", background: "#000" }}
                />
              </div>
            )}
            {cameraError && (
              <div style={{ padding: "12px 14px", borderRadius: "var(--r)", background: "var(--warn-soft)", color: "var(--warn)", fontSize: 13 }}>
                {cameraError}
              </div>
            )}
            {!getBarcodeDetector() && !cameraActive && (
              <p className="muted" style={{ fontSize: 12 }}>
                Tu navegador no incluye BarcodeDetector nativo. Podés escribir el código manualmente o usar un lector USB/Bluetooth.
              </p>
            )}

            {/* Result panel */}
            {checkinResult && (
              <div style={{
                padding: "20px 22px", borderRadius: "var(--r-lg)",
                background: checkinResult.ok ? "var(--ok-soft)" : "var(--bad-soft)",
                border: `2px solid ${checkinResult.ok ? "var(--ok)" : "var(--bad)"}`,
              }}>
                <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 10 }}>
                  <div style={{
                    width: 44, height: 44, borderRadius: "50%", display: "grid", placeItems: "center",
                    background: checkinResult.ok ? "var(--ok)" : "var(--bad)", color: "#fff", flexShrink: 0,
                  }}>
                    <Icon d={checkinResult.ok ? I.check : I.x} size={22} />
                  </div>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 17, color: checkinResult.ok ? "var(--ok)" : "var(--bad)" }}>
                      {checkinResult.ok ? "¡Acceso permitido!" : "Acceso denegado"}
                    </div>
                    <div className="muted" style={{ fontSize: 13 }}>{checkinResult.reason}</div>
                  </div>
                </div>
                {checkinResult.holder_name && (
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 8 }}>
                    <div><div className="meta">Titular</div><b style={{ fontSize: 14 }}>{checkinResult.holder_name}</b></div>
                    <div><div className="meta">Tipo</div><b style={{ fontSize: 14 }}>{checkinResult.type}</b></div>
                    <div style={{ gridColumn: "1/-1" }}><div className="meta">Código</div><span className="mono" style={{ fontSize: 12 }}>{checkinResult.code}</span></div>
                  </div>
                )}
                <button className="btn btn--ghost btn--sm" style={{ marginTop: 10 }} onClick={() => { setCheckinResult(null); checkInputRef.current?.focus(); }}>
                  Siguiente
                </button>
              </div>
            )}

            {/* Recent scans */}
            {recentScans.length > 0 && (
              <div>
                <div className="meta" style={{ marginBottom: 8 }}>Escaneos recientes</div>
                <table className="table">
                  <thead><tr><th>Código</th><th>Titular</th><th>Resultado</th><th className="muted">Hora</th></tr></thead>
                  <tbody>
                    {recentScans.map((s, i) => (
                      <tr key={i}>
                        <td className="mono" style={{ fontSize: 12 }}>{s.code}</td>
                        <td style={{ fontSize: 13 }}>{s.result.holder_name || "—"}</td>
                        <td>
                          <span className={`badge badge--${s.result.ok ? "ok" : "bad"}`}>
                            {s.result.ok ? "OK" : "Denegado"}
                          </span>
                        </td>
                        <td className="muted" style={{ fontSize: 12 }}>
                          {new Date(s.ts).toLocaleTimeString("es-CR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </Card>

      {/* Editor modal */}
      {editing && (
        <EventEditor
          event={event}
          onDone={() => { setEditing(false); loadEvent(); }}
          onClose={() => setEditing(false)}
        />
      )}
    </>
  );
}

