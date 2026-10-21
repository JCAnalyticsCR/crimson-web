/* Bandeja de Aprobaciones. Andres (13/10, 21:05-21:42) no sabia quien recibia lo enviado ni donde:
   "tendria que ser como otra categoria aqui… aprobaciones". Junta lo que ya espera un visto
   bueno (services/approvals.py en la API). Casi nada se aprueba aqui: cada elemento abre la pantalla donde se aprueba,
   viendo lo que se aprueba. La excepcion son los "Precios por revisar" del guardian de margen (plan 1.12): el precio
   sugerido se aplica con un clic (nunca se reprecia solo), y "Aprobar y enviar" de las mensualidades (plan 1.4).
   Lo mas antiguo primero. */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { api, ApiError, fmtMoney, parseTs } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, I, Icon, Loading } from "../../ui/components";
import type { ApprovalItem, ApprovalSection, ApprovalsFull } from "./useApprovalCount";
import "./approvals.css";

const LATE_H = 48; // mas de dos dias esperando: se marca

const sinceDate = (iso: string) => (iso.length === 10 ? new Date(`${iso}T12:00:00`) : parseTs(iso));

const ago = (iso: string | null) => {
  if (!iso) return "";
  const d = sinceDate(iso);
  const m = Math.max(1, Math.round((Date.now() - d.getTime()) / 60000));
  if (m < 60) return `hace ${m} min`;
  const h = Math.round(m / 60);
  if (h < 24) return `hace ${h} h`;
  const n = Math.round(h / 24);
  return `hace ${n} día${n !== 1 ? "s" : ""}`;
};

const lateDays = (iso: string | null) => {
  if (!iso) return 0;
  const h = (Date.now() - sinceDate(iso).getTime()) / 3_600_000;
  return h > LATE_H ? Math.floor(h / 24) : 0;
};

/* los chips llevan a su seccion sin ensuciar la URL (y sin animacion si la persona la pidio reducida) */
const goTo = (key: string) => {
  const smooth = !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  document.getElementById(key)?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
};

/* "Aprobar y enviar" (mensualidades, plan 1.4): la unica accion que se hace desde la bandeja. La persona puede abrir
   la factura antes (el elemento la abre) y el boton pide confirmacion: sale el correo al cliente. */
function ActionButton({ it, onDone }: { it: ApprovalItem; onDone: () => void }) {
  const { toast, allows } = useSession();
  const [busy, setBusy] = useState(false);
  if (!it.action || !allows("sales.enviar")) return null;
  const run = async () => {
    if (!it.action || !confirm(`${it.action.label}: le llega al cliente ${it.title} con su PDF y enlace de pago. ¿Continuar?`)) return;
    setBusy(true);
    try {
      const r = await api<{ sent: boolean; note: string | null; number: string }>(it.action.path, { method: "POST" });
      toast(r.sent ? `${r.number} enviada al cliente` : r.note || "No salió el correo", r.sent ? undefined : "bad");
      onDone();
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo", "bad"); }
    finally { setBusy(false); }
  };
  return <button className="btn btn--crimson btn--sm aprob-item__action" disabled={busy} onClick={run}>{it.action.label}</button>;
}

function Row({ it, onDone }: { it: ApprovalItem; onDone: () => void }) {
  const { toast } = useSession();
  const [busy, setBusy] = useState(false);
  const late = lateDays(it.since);
  const when = late ? `esperando hace ${late} día${late !== 1 ? "s" : ""}` : ago(it.since);
  const act = async () => {
    if (!it.action || (it.action.confirm && !confirm(it.action.confirm))) return;
    setBusy(true);
    try { await api(it.action.path, { method: "POST", json: it.action.body }); toast("Precio aplicado: el producto vuelve a la venta"); onDone(); }
    catch (e) {
      toast(e instanceof Error ? e.message : "No se pudo aplicar", "bad");
      // 409: ya salio de la revision o el sugerido de hoy es otro; la bandeja se recarga con el dato al dia
      if (e instanceof ApiError && e.status === 409) onDone();
    }
    finally { setBusy(false); }
  };
  return (
    <li className={it.action ? "has-action" : undefined}>
      <Link className={`aprob-item${late ? " is-late" : ""}`} to={it.to}>
        <span className="aprob-item__ref mono">{it.ref}</span>
        <span className="aprob-item__main">
          <b>{it.title}</b>
          {it.subtitle && <small>{it.subtitle}</small>}
          {(it.who || when) && <span className="aprob-item__meta">{[it.who, when].filter(Boolean).join(" · ")}</span>}
        </span>
        <span className="aprob-item__side">
          {it.badge && <span className="badge badge--warn">{it.badge}</span>}
          {it.amount !== null && it.amount !== undefined && <b className="money">{fmtMoney(it.amount, it.currency || "CRC")}</b>}
        </span>
        <em className="aprob-item__go">Abrir →</em>
      </Link>
      {it.action && (it.action.body === undefined
        ? <div className="aprob-item__act"><ActionButton it={it} onDone={onDone} /></div>
        : <div className="aprob-item__act"><button className="btn btn--crimson btn--sm" onClick={act} disabled={busy}>{busy ? "Aplicando…" : it.action.label}</button></div>)}
    </li>
  );
}

/* "Precios por revisar" frenada: la revision parecio un error de datos (tipo de cambio que salto, costos dudosos) y dejo
   productos bajo el minimo a la venta. Una persona lo destraba despues de verificar (o corrige el dato y se libera solo). */
function Brake({ b, onDone }: { b: NonNullable<ApprovalSection["brake"]>; onDone: () => void }) {
  const { toast } = useSession();
  const [busy, setBusy] = useState(false);
  const go = async () => {
    if (!confirm(`¿Verificaste el tipo de cambio y los costos? Se sacan de venta los ${b.frenados} producto(s) frenados (pasan a «Consultar»). No cambia ningún precio.`)) return;
    setBusy(true);
    try {
      const r = await api<{ nuevos_retenidos: number }>("/approvals/prices/run", { method: "POST", json: { confirmar: true } });
      toast(`${r.nuevos_retenidos} producto(s) fuera de venta hasta que apliqués su precio`); onDone();
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo revisar", "bad"); }
    finally { setBusy(false); }
  };
  return (
    <div className="aprob-brake">
      <p><b>Revisión frenada:</b> {b.reason}. {b.frenados} producto(s) bajo el mínimo siguen a la venta por si es un error del dato. Si el dato está mal, corregilo (Ajustes → Facturación) y se libera solo.</p>
      <button className="btn btn--crimson btn--sm" onClick={go} disabled={busy}>{busy ? "Revisando…" : "Sacar de venta todo"}</button>
    </div>
  );
}

function Section({ s, onDone, whole }: { s: ApprovalSection; onDone: () => void; whole: boolean }) {
  const rest = s.count - s.items.length;
  return (
    <div id={s.key} className="aprob-sec">
      <Card title={`${s.label} · ${s.count}`} extra={whole ? undefined : <Link className="btn btn--ghost btn--sm" to={s.to}>Ver todos →</Link>} flush>
        <p className="muted aprob-hint">{s.hint}{s.reviewers?.length ? ` Avisamos a: ${s.reviewers.join(", ")}.` : ""}</p>
        {s.brake && <Brake b={s.brake} onDone={onDone} />}
        <ul className="aprob-list">{s.items.map((it) => <Row key={it.id} it={it} onDone={onDone} />)}</ul>
        {rest > 0 && (whole
          ? <p className="aprob-more">y {rest} más: aplicá o revisá estos y tocá Actualizar.</p>
          : <p className="aprob-more">y {rest} más · <Link to={s.to}>Ver todos</Link></p>)}
      </Card>
    </div>
  );
}

export default function Approvals() {
  const { allows, toast } = useSession();
  const [params] = useSearchParams();
  const { hash } = useLocation();
  /* ?seccion=clave: "Ver todos" de una seccion (la bandeja entera corta en 50 por seccion) */
  const only = params.get("seccion");
  const [data, setData] = useState<ApprovalsFull | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const scrolled = useRef(false);

  const load = useCallback(async () => {
    setBusy(true);
    const path = only ? `/approvals?section=${encodeURIComponent(only)}&limit=500` : "/approvals?limit=50";
    try { setData(await api<ApprovalsFull>(path)); setFailed(false); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo cargar la bandeja", "bad"); setFailed(true); }
    finally { setBusy(false); }
  }, [toast, only]);
  useEffect(() => { load(); }, [load]);
  /* los enlaces del correo, de Productos y de Ajustes traen #seccion: se baja a ella cuando ya cargo (una sola vez) */
  useEffect(() => {
    if (!data || scrolled.current || !hash) return;
    scrolled.current = true;
    requestAnimationFrame(() => goTo(decodeURIComponent(hash.slice(1))));
  }, [data, hash]);

  // en vista previa, solo lo que ese rol aprobaria (la API responde con la cuenta real)
  const secs = (data?.sections || []).filter((s) => s.needs.every((n) => allows(n)));
  const conPend = secs.filter((s) => s.count > 0);
  const alDia = secs.filter((s) => s.count === 0);

  return (
    <>
      <div className="page-head">
        <div><div className="meta">Bandeja</div><h1 className="h1">Aprobaciones</h1></div>
        <div className="page-head__actions">
          {only && <Link className="btn btn--ghost" to="/aprobaciones">← Toda la bandeja</Link>}
          <button className="btn btn--ghost" onClick={load} disabled={busy}><Icon d={I.refresh} />Actualizar</button>
        </div>
      </div>
      <p className="muted aprob-intro">Lo que espera tu visto bueno, en un solo lugar. Tocá un elemento para abrirlo donde se aprueba.</p>

      {data === null ? (
        failed ? <Card><Empty title="No se pudo cargar la bandeja" hint="Revisá la conexión y tocá Actualizar." /></Card> : <Loading />
      ) : conPend.length === 0 ? (
        <Card><Empty title="Nada pendiente de aprobar" hint="Cuando un técnico envíe un levantamiento a revisión, o una cotización, reserva, planilla, comisión, mensualidad o precio de la tienda necesite tu visto bueno, aparece aquí." /></Card>
      ) : (
        <>
          <nav className="aprob-chips" aria-label="Secciones con pendientes">
            {conPend.map((s) => (
              <a key={s.key} href={`#${s.key}`} onClick={(e) => { e.preventDefault(); goTo(s.key); }}>{s.label} <b>{s.count}</b></a>
            ))}
          </nav>
          {conPend.map((s) => <Section key={s.key} s={s} onDone={load} whole={!!only} />)}
          {alDia.length > 0 && <p className="muted aprob-ok">✓ Al día: {alDia.map((s) => s.label).join(" · ")}</p>}
        </>
      )}
    </>
  );
}
