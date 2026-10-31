/* Mi suscripción (28:01-28:22): el plan de la plataforma, si está al día o vencida (con monto y días) y las
   facturas de la mensualidad con su PDF, su estado real y el pago en línea (plan 1.4). Funciona aunque el acceso esté
   suspendido: es para poder pagar. */
import { useEffect, useState } from "react";
import { api, downloadFile, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Empty, I, Icon } from "../../ui/components";
import { errorText } from "./shared";

type Estado = "por_pagar" | "vencida" | "abonada" | "pagada";
type Fact = { id: number; number: string; issue_date: string; due_date: string | null; total: string; balance: string; status: string; estado?: Estado; pagable?: boolean; currency: string };
type Servicio = { descripcion: string; aceptado: boolean; aceptado_at: string | null; aceptado_por: string | null; monto: string | null; moneda: string; modo: "fija" | "por_usuario" };
type Sus = {
  acceso_basico?: string; servicio?: Servicio | null;
  plan: { monto: string; moneda: string; modo: "fija" | "por_usuario"; cantidad: string | null; frecuencia: string; proxima_fecha: string; activa: boolean } | null;
  estado: "al_dia" | "vencida"; vencido: { monto: string; moneda: string; dias: number } | null; facturas: Fact[]; seats: { used: number; max: number | null };
};

const ESTADO: Record<Estado, { label: string; tone: string }> = {
  por_pagar: { label: "Por pagar", tone: "warn" },
  vencida: { label: "Vencida", tone: "bad" },
  abonada: { label: "Abonada", tone: "warn" },
  pagada: { label: "Pagada", tone: "ok" },
};

export default function AllySubscription() {
  const { toast, role, allyPreview } = useSession();
  const [aceptando, setAceptando] = useState(false);
  const [s, setS] = useState<Sus | null>(null);
  const [todas, setTodas] = useState<Fact[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [paying, setPaying] = useState<number | null>(null);
  useEffect(() => {
    api<Sus>("/aliado/suscripcion").then(setS).catch((e) => setErr(errorText(e)));
    api<Fact[]>("/aliado/facturas").then(setTodas).catch(() => setTodas([]));
  }, []);
  if (err) return <p className="muted">{err}</p>;
  if (!s) return <div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  const pdf = (f: Fact) => downloadFile(`/aliado/facturas/${f.id}/pdf`, `${f.number}.pdf`).catch((e) => toast(e instanceof Error ? e.message : "No se pudo descargar", "bad"));
  const pagar = async (f: Fact) => {
    setPaying(f.id);
    try {
      const r = await api<{ url: string }>(`/aliado/facturas/${f.id}/pagar`, { method: "POST" });
      window.open(r.url, "_blank", "noopener");
    } catch (e) { toast(errorText(e), "bad"); }
    finally { setPaying(null); }
  };
  const aceptar = async () => {
    if (!s.servicio || !confirm(`¿Aceptar el servicio adicional «${s.servicio.descripcion}»? Desde ahí se activa su mensualidad.`)) return;
    setAceptando(true);
    try { setS(await api<Sus>("/aliado/suscripcion/aceptar", { method: "POST" })); toast("Servicio adicional aceptado"); }
    catch (e) { toast(errorText(e), "bad"); }
    finally { setAceptando(false); }
  };
  const otras = todas.filter((f) => !s.facturas.some((x) => x.id === f.id));
  const lista = (rows: Fact[]) => (
    <div className="cp-list">
      {rows.map((f) => {
        const e = f.estado ? ESTADO[f.estado] : null;
        return (
          <div key={f.id} className="cp-row">
            <div className="cp-row__main"><b>{f.number}</b><div className="cp-row__meta">{fmtDate(f.issue_date)}{f.due_date ? ` · vence ${fmtDate(f.due_date)}` : ""} · total {fmtMoney(f.total, f.currency)} · saldo {fmtMoney(f.balance, f.currency)}</div></div>
            <div className="cp-row__side">
              {e ? <span className={`badge badge--${e.tone}`}>{e.label}</span> : <span className="badge badge--muted">{f.status}</span>}
              <button className="btn btn--ghost btn--sm" onClick={() => pdf(f)}><Icon d={I.invoice} size={14} />PDF</button>
              {f.pagable && <button className="btn btn--crimson btn--sm" disabled={paying === f.id} onClick={() => pagar(f)}>Pagar en línea</button>}
            </div>
          </div>
        );
      })}
    </div>
  );
  return (
    <>
      <div className="cp-h"><h1 className="h1">Mi suscripción</h1></div>
      {/* punto 13: el acceso básico es parte del servicio compartido; solo un servicio adicional aceptado se cobra */}
      <div className="cp-card" style={{ marginBottom: 12 }}>
        <b>Acceso al portal</b>
        <p style={{ margin: "6px 0 0" }}><span className="badge badge--ok">Incluido</span> {s.acceso_basico || "El acceso básico al portal está incluido: no se cobra."}</p>
      </div>
      {s.servicio && (
        <div className="cp-card" style={{ marginBottom: 12 }}>
          <b>Servicio adicional</b>
          <p style={{ margin: "6px 0" }}>{s.servicio.descripcion}</p>
          {s.servicio.monto != null && <p className="meta" style={{ margin: 0 }}>{fmtMoney(s.servicio.monto, s.servicio.moneda)} + IVA al mes{s.servicio.modo === "por_usuario" ? " por usuario" : ""}</p>}
          {s.servicio.aceptado ? (
            <p style={{ margin: "6px 0 0" }}><span className="badge badge--ok">Aceptado</span> <span className="meta">{s.servicio.aceptado_at ? fmtDate(s.servicio.aceptado_at) : ""}{s.servicio.aceptado_por ? ` · ${s.servicio.aceptado_por}` : ""}</span></p>
          ) : (
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 6 }}>
              <span className="badge badge--warn">Propuesto por Crimson</span>
              {role === "aliado" && !allyPreview && <button className="btn btn--crimson btn--sm" disabled={aceptando} onClick={aceptar}>Aceptar servicio adicional</button>}
              <span className="meta">Mientras no lo acepte, no se cobra nada.</span>
            </div>
          )}
        </div>
      )}
      <div className="cp-grid cp-grid--2" style={{ marginBottom: 16 }}>
        <div className="cp-card">
          <b>Mensualidad del servicio adicional</b>
          {s.plan ? (
            <dl className="al-kv" style={{ marginTop: 8 }}>
              <dt>Mensualidad</dt><dd>{fmtMoney(s.plan.monto, s.plan.moneda)} + IVA{s.plan.modo === "por_usuario" ? ` por usuario (× ${Number(s.plan.cantidad || 1)})` : ""}</dd>
              <dt>Próximo cobro</dt><dd>{fmtDate(s.plan.proxima_fecha)}</dd>
              <dt>Usuarios</dt><dd>{s.seats.used}{s.seats.max ? ` de ${s.seats.max}` : ""}</dd>
            </dl>
          ) : <p className="muted">Sin mensualidad: no tiene un servicio adicional contratado.</p>}
        </div>
        <div className="cp-card">
          <b>Estado</b>
          {s.estado === "al_dia" ? <p><span className="badge badge--ok">Al día</span></p> : (
            <p><span className="badge badge--bad">Vencida</span> {s.vencido && <>{fmtMoney(s.vencido.monto, s.vencido.moneda)} con {s.vencido.dias} día(s) de atraso</>}</p>
          )}
        </div>
      </div>
      <h2 className="h3 al-sec">Facturas del servicio adicional</h2>
      {s.facturas.length ? lista(s.facturas) : <Empty title="Todavía no hay facturas del servicio adicional" />}
      {otras.length > 0 && <><h2 className="h3 al-sec">Otras facturas de Crimson</h2>{lista(otras)}</>}
    </>
  );
}
