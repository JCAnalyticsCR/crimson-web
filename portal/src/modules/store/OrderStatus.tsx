/* "Mi pedido": estado, productos, totales, instrucciones de pago y subida del comprobante. Se abre con el enlace
   firmado del correo (?pedido=<token>) o buscando numero + correo. Nunca muestra otros pedidos: el API valida el
   token contra la tienda y la busqueda responde igual exista o no el pedido.
   Reunion 21/10: linea que une los pasos cumplidos (41), historial con las notas que la empresa pone para el cliente
   (36), el comprobante que subio el cliente se puede volver a abrir (34), "Rastrear mi pedido" en la confirmacion
   (40) y una caida pasajera del API ya no se presenta como "No encontramos ese pedido" (39). */
import { useCallback, useEffect, useRef, useState, type FormEvent, type MouseEvent } from "react";
import { Field, IC, Ico, fmtWhen, local, money, session, wa, type ApiFn, type StoreInfo } from "./shopkit";
import "./checkout.css";

type Pub = {
  number: string; status: string; status_label: string; created_at: string; paid_at: string | null; expires_at: string | null; currency: string;
  subtotal: number; discount_total: number; shipping: number; tax_total: number; total: number; coupon_code: string | null;
  lines: { name: string; quantity: number; unit_price: number; tax_rate: number; total: number; shipping?: boolean }[];
  contact: { name: string | null; email: string | null }; shipping_method: string | null;
  delivery: { type: "envio" | "retiro"; province?: string; canton?: string; district?: string } | null; pickup: { address?: string; hours?: string } | null;
  payment_method: string | null; payment_kind: string | null; instructions: string | null; needs_proof: boolean; proofs: { n?: number; filename: string; at: string; pdf?: boolean; open?: boolean }[];
  card: { pending: boolean; simulated: boolean } | null; tracking_number: string | null; carrier: string | null; cancel_reason: string | null;
  /* cambios de estado + notas que la empresa marco para el cliente (kind "nota"); las internas no llegan */
  history: { status: string; label: string; at: string; note?: string | null; kind?: string }[]; whatsapp: string; store_name: string;
};
type Placed = { email: { to: string | null; status: string } | null; pay: { checkout_url?: string | null; simulated?: boolean; error?: string } | null };

const MAX = 5 * 1024 * 1024;

/* Pedidos comprados en este navegador (los ultimos 5): "Mi pedido" los ofrece sin buscar el correo. El enlace es la
   unica llave del pedido, asi que se guarda SOLO al confirmar la compra (no cada vez que alguien abre un enlace, p. ej.
   el equipo desde el panel), vence a los RECENT_DAYS y sale de la lista cuando el pedido se entrega o se cierra. */
export type Recent = { number: string; token: string; at: string };
const RECENT_DAYS = 30;
const recentKey = (slug: string) => `crimson-shop:pedidos:${slug}`;
export function recentOrders(slug: string): Recent[] {
  try {
    const v = JSON.parse(local.get(recentKey(slug)) || "[]");
    const limit = Date.now() - RECENT_DAYS * 86_400_000;
    return Array.isArray(v) ? v.filter((x) => x && typeof x.number === "string" && typeof x.token === "string" && Date.parse(x.at) > limit) : [];
  } catch { return []; }
}
export function rememberOrder(slug: string, r: { number: string | null; token: string }) {
  if (!r.number || !r.token) return;
  const list = [{ number: r.number, token: r.token, at: new Date().toISOString() }, ...recentOrders(slug).filter((x) => x.number !== r.number)].slice(0, 5);
  local.set(recentKey(slug), JSON.stringify(list));
}
function forgetOrder(slug: string, number: string) {
  const all = recentOrders(slug);
  const list = all.filter((x) => x.number !== number);
  if (list.length === all.length) return;
  if (list.length) local.set(recentKey(slug), JSON.stringify(list)); else local.del(recentKey(slug));
}
const CLOSED = ["entregado", "cancelado", "reembolsado"];
const ORDER = ["nuevo", "en_revision", "pagado", "preparando", "listo", "enviado", "entregado"];

export default function OrderStatus({ api, token, store, slug, trackHref, onBack, onLookup }: {
  api: ApiFn; token: string; store: StoreInfo; slug: string; trackHref: string; onBack: () => void; onLookup?: () => void;
}) {
  const [o, setO] = useState<Pub | null>(null);
  const [missing, setMissing] = useState<{ notFound: boolean; text: string } | null>(null);
  const hasO = useRef(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const path = `/orders/${encodeURIComponent(token)}`;
  /* Solo un 404 es "no encontramos ese pedido". Red caida, 502 mientras se publica una version o un 429: se reintenta
     solo (hasta 3 veces) y, si sigue, se dice eso y se ofrece reintentar. Con el pedido ya en pantalla, un refresco que
     falla no lo borra. */
  const load = useCallback(async (retry = false) => {
    for (let i = 0; ; i++) {
      try {
        const { data } = await api<Pub>(path);
        hasO.current = true; setO(data); setMissing(null);
        if (CLOSED.includes(data.status)) forgetOrder(slug, data.number);
        return;
      } catch (e) {
        const notFound = (e as { status?: number }).status === 404;
        if (!notFound && retry && i < 2) { await new Promise((r) => window.setTimeout(r, 1200 * (i + 1))); continue; }
        if (!notFound && hasO.current) return;
        setMissing({ notFound, text: (e as Error).message });
        return;
      }
    }
  }, [api, path, slug]);
  useEffect(() => { load(true); }, [load]);
  // al volver de ONVO el webhook puede tardar: se llama card/sync de inmediato y cada 10 s hasta 12 veces.
  // Depende de un booleano (no de `o`): cada respuesta hace setO y, atado a `o`, el efecto se relanzaria sin fin.
  const cardPending = !!o && o.payment_kind === "tarjeta" && o.status === "nuevo" && !o.card?.simulated;
  useEffect(() => {
    if (!cardPending) return;
    let n = 0;
    const syncOrFallback = () =>
      api<Pub>(`${path}/card/sync`, { method: "POST" })
        .then(({ data }) => setO(data))
        .catch(() => load()); // red o 429: recaer en load silenciosamente
    syncOrFallback(); // llamada inmediata al montar con pago pendiente
    const t = window.setInterval(() => { if (++n > 12) window.clearInterval(t); else syncOrFallback(); }, 10_000);
    return () => window.clearInterval(t);
  }, [cardPending, api, path, load]);

  const placed: Placed | null = o ? (() => { try { return JSON.parse(session.get(`crimson-shop:placed:${o.number}`) || "null"); } catch { return null; } })() : null;

  if (missing) return missing.notFound ? (
    <div className="sh-wrap sh-empty"><b>No encontramos ese pedido.</b><span>El enlace puede estar incompleto. Buscalo con tu número de pedido y correo.</span>
      <div className="ck-row" style={{ justifyContent: "center" }}>{onLookup && <button className="sh-btn sh-btn--crimson" onClick={onLookup}>Buscar mi pedido</button>}<button className="sh-btn sh-btn--ghost" onClick={onBack}>Volver a la tienda</button></div></div>
  ) : (
    <div className="sh-wrap sh-empty" role="alert"><b>No pudimos cargar tu pedido en este momento.</b><span>Tu pedido está bien: es la conexión. Probá de nuevo en unos segundos.</span>
      <div className="ck-row" style={{ justifyContent: "center" }}><button className="sh-btn sh-btn--crimson" onClick={() => { setMissing(null); load(true); }}>Reintentar</button><button className="sh-btn sh-btn--ghost" onClick={onBack}>Volver a la tienda</button></div></div>
  );
  if (!o) return <div className="sh-wrap" style={{ paddingTop: 24 }}><div className="sh-skel" style={{ height: 360 }} /></div>;

  const cur = o.currency;
  const wsp = o.whatsapp || store.whatsapp;
  const pickup = o.delivery?.type === "retiro";
  const closed = o.status === "cancelado" || o.status === "reembolsado";
  const steps = [
    { k: "nuevo", t: "Pedido recibido" },
    { k: "pagado", t: o.payment_kind === "efectivo" ? "Pago al recibir" : "Pago confirmado" },
    { k: "preparando", t: "Preparando" },
    { k: pickup ? "listo" : "enviado", t: pickup ? "Listo para retiro" : "Enviado" },
    { k: "entregado", t: "Entregado" },
  ];
  const reached = ORDER.indexOf(o.status);
  const doneStep = (k: string) => {
    if (k === "nuevo") return true;
    if (k === "pagado") return !!o.paid_at || (reached >= ORDER.indexOf("pagado") && o.payment_kind !== "efectivo");
    return reached >= ORDER.indexOf(k);
  };

  const proofHref = (n: number) => `/api/public/store/${encodeURIComponent(slug)}${path}/proofs/${n}`;
  const proofList = o.proofs.length > 0 && (
    <ul className="ck-files">{o.proofs.map((p, i) => (
      <li key={p.at + i}><Ico d={IC.check} size={14} />{p.open === false
        ? <span title="Por seguridad el archivo ya no se abre desde este enlace">{p.filename}</span>
        : <a href={proofHref(p.n ?? i)} target="_blank" rel="noopener noreferrer" title="Ver el comprobante que subiste">{p.filename}</a>} · {fmtWhen(p.at)}</li>
    ))}</ul>
  );
  const payBox = !!o.instructions;
  const proofInPay = payBox && (o.needs_proof || o.status === "en_revision") && o.payment_kind !== "tarjeta" && o.payment_kind !== "efectivo";
  const goTrack = (e: MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    const el = document.getElementById("ck-seguimiento");
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
    el?.focus({ preventScroll: true });
  };
  const copy = async (text: string, what: string) => { try { await navigator.clipboard.writeText(text); setCopied(what); window.setTimeout(() => setCopied(null), 1800); } catch { /* sin permiso */ } };
  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > MAX) { setMsg({ ok: false, text: "El archivo pesa más de 5 MB. Probá con una captura de pantalla." }); return; }
    if (!/^(image\/(jpeg|png|webp)|application\/pdf)$/.test(file.type)) { setMsg({ ok: false, text: "Subí una foto (JPG, PNG o WEBP) o un PDF." }); return; }
    setBusy("proof"); setMsg(null);
    const fd = new FormData();
    fd.append("file", file);
    try { const { data } = await api<Pub>(`${path}/proof`, { method: "POST", body: fd }); setO(data); setMsg({ ok: true, text: "Recibimos tu comprobante. Te avisamos al confirmar el pago." }); }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    finally { setBusy(null); if (fileRef.current) fileRef.current.value = ""; }
  };
  const payCard = async () => {
    setBusy("card"); setMsg(null);
    try {
      const { data } = await api<{ checkout_url: string | null; simulated: boolean }>(`${path}/card`, { method: "POST" });
      if (data.checkout_url) window.location.assign(data.checkout_url);
      else if (data.simulated) load();
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); } finally { setBusy(null); }
  };
  const simulate = async () => {
    setBusy("sim"); setMsg(null);
    try { const { data } = await api<Pub>(`${path}/simulate-payment`, { method: "POST" }); setO(data); setMsg({ ok: true, text: "Pago de prueba aprobado (simulado). No se cobró nada." }); }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }); } finally { setBusy(null); }
  };
  const waText = `Hola ${o.store_name || store.name}, consulto por mi pedido ${o.number} (${money(o.total, cur)}).${o.needs_proof ? " Adjunto el comprobante de pago." : ""}`;
  const mailNote = placed?.email
    ? placed.email.status === "enviado" ? `Te enviamos la confirmación a ${placed.email.to}.` : "No pudimos enviarte el correo de confirmación. Guardá esta página o anotá tu número de pedido."
    : placed ? "No dejaste correo: guardá esta página para ver el estado de tu pedido." : null;

  return (
    <div className="sh-wrap ck ck-order">
      <nav className="sh-crumbs" aria-label="Ruta"><button onClick={onBack}><span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><Ico d={IC.back} size={14} />Seguir comprando</span></button></nav>
      {placed && !closed && (
        <div className="ck-thanks" role="status">
          <span className="sh-eyebrow">¡Gracias{o.contact.name ? `, ${o.contact.name.split(" ")[0]}` : ""}!</span>
          <b>Recibimos tu pedido.</b>
          {mailNote && <span>{mailNote}</span>}
          <div className="ck-row">
            <a className="sh-btn sh-btn--crimson sh-btn--sm" href={trackHref} onClick={goTrack}><Ico d={IC.truck} size={16} />Rastrear mi pedido</a>
            <button type="button" className="sh-btn sh-btn--ghost sh-btn--sm" onClick={() => copy(new URL(trackHref, window.location.href).href, "link")}><Ico d={IC.copy} size={16} />{copied === "link" ? "Enlace copiado" : "Copiar enlace de seguimiento"}</button>
          </div>
          <small>Guardá el enlace: ahí ves el estado de tu pedido sin buscar el correo. Desde este navegador también lo encontrás en «Mi pedido» durante {RECENT_DAYS} días.</small>
        </div>
      )}
      <header className="ck-ohead">
        <div><span className="sh-meta">Pedido · {fmtWhen(o.created_at)}</span><h1>{o.number}</h1></div>
        <span className="ck-badge" data-s={o.status}>{o.status_label}</span>
      </header>

      {closed ? (
        <div className="ck-closed" role="status"><b>{o.status === "cancelado" ? "Pedido cancelado" : "Reembolso registrado"}</b>{o.cancel_reason && <span>Motivo: {o.cancel_reason}</span>}</div>
      ) : (
        <ol className="ck-track" id="ck-seguimiento" tabIndex={-1} aria-label="Avance del pedido">
          {steps.map((s, i) => {
            const done = doneStep(s.k);
            const next = steps[i + 1];
            const now = done && !(next && doneStep(next.k));
            const when = o.history.find((h) => h.status === s.k && h.kind !== "nota")?.at;
            // linea hacia el paso siguiente: llena si los dos se cumplieron
            return <li key={s.k} data-done={done} data-line={next ? (done && doneStep(next.k) ? "done" : "todo") : undefined} aria-current={now ? "step" : undefined}><i aria-hidden="true">{done ? <Ico d={IC.check} size={12} /> : null}</i><span>{s.t}{o.status === "en_revision" && s.k === "pagado" ? <small>Comprobante en revisión</small> : when ? <small>{fmtWhen(when)}</small> : null}</span></li>;
          })}
        </ol>
      )}

      <div className="ck-ogrid">
        <div className="ck-ocol">
          {o.instructions && (
            <section className="sh-panel ck-pay" aria-labelledby="ck-pay-h">
              <h2 id="ck-pay-h">Cómo pagar · {o.payment_method}</h2>
              <p className="ck-instr">{o.instructions}</p>
              <div className="ck-row">
                <button type="button" className="sh-btn sh-btn--ghost sh-btn--sm" onClick={() => copy(o.number, "num")}><Ico d={IC.copy} size={16} />{copied === "num" ? "Copiado" : "Copiar n.º de pedido"}</button>
                <button type="button" className="sh-btn sh-btn--ghost sh-btn--sm" onClick={() => copy(String(Number(o.total).toFixed(cur === "CRC" ? 0 : 2)), "amt")}><Ico d={IC.copy} size={16} />{copied === "amt" ? "Copiado" : `Copiar monto`}</button>
              </div>
              {o.expires_at && o.status === "nuevo" && <p className="sh-note" style={{ margin: 0 }}>Pagá antes del {fmtWhen(o.expires_at)} para confirmar tu pedido</p>}
              {(o.needs_proof || o.status === "en_revision") && o.payment_kind !== "tarjeta" && o.payment_kind !== "efectivo" && (
                <div className="ck-proof">
                  <input ref={fileRef} id="ck-proof" type="file" accept="image/jpeg,image/png,image/webp,application/pdf" onChange={(e) => upload(e.target.files?.[0])} hidden />
                  <label htmlFor="ck-proof" className="sh-btn sh-btn--crimson" aria-disabled={busy === "proof"}><Ico d={IC.upload} size={16} />{busy === "proof" ? "Subiendo…" : o.proofs.length ? "Subir otro comprobante" : "Subir comprobante"}</label>
                  <small>Foto o PDF, máximo 5 MB. Lo ven solo vos (con este enlace) y el equipo de {o.store_name || store.name}.</small>
                  {proofList}
                </div>
              )}
              {o.card?.pending && !o.card.simulated && <button className="sh-btn sh-btn--crimson sh-btn--block" onClick={payCard} disabled={busy === "card"}><Ico d={IC.card} />{busy === "card" ? "Abriendo…" : "Pagar con tarjeta"}</button>}
              {o.card?.pending && o.card.simulated && (
                <div className="ck-sim" role="note"><b>Modo de prueba · pago simulado</b><span>Esta tienda todavía no tiene ONVO conectado. Este botón aprueba un pago de prueba: no se cobra nada ni se piden datos de tarjeta.</span>
                  <button className="sh-btn sh-btn--block" onClick={simulate} disabled={busy === "sim"}>{busy === "sim" ? "Procesando…" : "Simular pago aprobado"}</button></div>
              )}
            </section>
          )}
          {msg && <p className={msg.ok ? "ck-ok" : "sh-err"} role={msg.ok ? "status" : "alert"}>{msg.text}</p>}
          {!proofInPay && proofList && (
            <section className="sh-panel ck-proofs" aria-labelledby="ck-proofs-h">
              <h2 id="ck-proofs-h">{o.proofs.length > 1 ? "Tus comprobantes" : "Tu comprobante"}</h2>
              {proofList}
              <small>Lo ven solo vos (con este enlace) y el equipo de {o.store_name || store.name}.</small>
            </section>
          )}

          <section className="sh-panel ck-info">
            <div><span className="sh-meta">Entrega</span>
              {pickup ? <><b>Retiro en sitio</b>{o.pickup?.address && <span>{o.pickup.address}{o.pickup.hours ? ` · ${o.pickup.hours}` : ""}</span>}</>
                : <><b>{o.shipping_method || "Envío"}</b>{o.delivery && <span>{[o.delivery.district, o.delivery.canton, o.delivery.province].filter(Boolean).join(", ")}</span>}</>}
              {o.tracking_number && <span>Guía: <b className="ck-mono">{o.tracking_number}</b>{o.carrier ? ` · ${o.carrier}` : ""}</span>}
            </div>
            <div><span className="sh-meta">Pago</span><b>{o.payment_method || "A coordinar"}</b><span>{o.paid_at ? `Confirmado el ${fmtWhen(o.paid_at)}` : o.status === "en_revision" ? "Comprobante en revisión" : "Pendiente"}</span></div>
          </section>

          {o.history.length > 0 && (
            <section className="sh-panel ck-hist" aria-labelledby="ck-hist-h">
              <h2 id="ck-hist-h">Historial del pedido</h2>
              <ol>{[...o.history].reverse().map((h, i) => (
                <li key={`${h.at}-${i}`} data-kind={h.kind === "nota" ? "nota" : "estado"}>
                  <time dateTime={h.at}>{fmtWhen(h.at)}</time>
                  <div><b>{h.kind === "nota" ? `Mensaje de ${o.store_name || store.name}` : h.label}</b>{h.note && <p>{h.note}</p>}</div>
                </li>
              ))}</ol>
            </section>
          )}

          <div className="ck-row">
            {wsp && <a className="sh-btn sh-btn--wa" href={wa(wsp, waText)} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />Escribir por WhatsApp</a>}
            <button className="sh-btn sh-btn--ghost" onClick={onBack}>Seguir comprando</button>
          </div>
        </div>

        <aside className="sh-panel ck-ocol" aria-label="Productos y totales">
          <span className="sh-meta">Productos</span>
          <ul className="ck-olines">{o.lines.filter((l) => !l.shipping).map((l, i) => <li key={i}><span>{Number(l.quantity)} × {l.name}</span><b>{money(l.total, cur)}</b></li>)}</ul>
          <div className="ck-totals">
            <div className="sh-totrow"><span>Subtotal (sin IVA)</span><b>{money(o.subtotal, cur)}</b></div>
            {Number(o.discount_total) > 0 && <div className="sh-totrow"><span>Descuento{o.coupon_code ? ` (${o.coupon_code})` : ""}</span><b>−{money(o.discount_total, cur)}</b></div>}
            <div className="sh-totrow"><span>Envío (sin IVA)</span><b>{Number(o.shipping) > 0 ? money(o.shipping, cur) : o.delivery?.type === "envio" ? "Gratis" : "—"}</b></div>
            <div className="sh-totrow"><span>IVA</span><b>{money(o.tax_total, cur)}</b></div>
            <div className="sh-totrow sh-totrow--big"><span>Total</span><b>{money(o.total, cur)}</b></div>
          </div>
        </aside>
      </div>
    </div>
  );
}

/** Buscar un pedido con numero + correo. Misma respuesta si no existe o si el correo no coincide. */
export function OrderLookup({ api, slug, onFound, onBack }: { api: ApiFn; slug: string; onFound: (token: string) => void; onBack: () => void }) {
  const [f, setF] = useState({ number: "", email: "", website: "" });
  const [recent, setRecent] = useState<Recent[]>(() => recentOrders(slug));
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      const { data } = await api<{ token: string }>("/orders/lookup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ number: f.number.trim(), email: f.email.trim(), website: f.website || null }) });
      onFound(data.token);
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="sh-wrap ck">
      <nav className="sh-crumbs" aria-label="Ruta"><button onClick={onBack}><span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><Ico d={IC.back} size={14} />Volver a la tienda</span></button></nav>
      <form className="sh-panel ck-lookup" onSubmit={submit} noValidate>
        <h1>Mi pedido</h1>
        {recent.length > 0 && (
          <div className="ck-recent" aria-label="Pedidos abiertos en este navegador">
            <span className="sh-meta">Tus pedidos en este navegador</span>
            {recent.map((r) => <button key={r.number} type="button" className="sh-btn sh-btn--ghost sh-btn--block" onClick={() => onFound(r.token)}><Ico d={IC.truck} size={16} />Rastrear {r.number}</button>)}
            <button type="button" className="ck-link" onClick={() => { local.del(recentKey(slug)); setRecent([]); }}>Olvidar estos pedidos</button>
          </div>
        )}
        <p className="sh-note" style={{ margin: 0, fontSize: 14 }}>Escribí el número de pedido (está en el correo de confirmación) y el correo con el que compraste.</p>
        <div className="ck-hp" aria-hidden="true"><label>Sitio web<input tabIndex={-1} autoComplete="off" value={f.website} onChange={(e) => setF({ ...f, website: e.target.value })} /></label></div>
        <Field label="Número de pedido"><input value={f.number} onChange={(e) => setF({ ...f, number: e.target.value.toUpperCase() })} placeholder="ORD-000123" autoCapitalize="characters" /></Field>
        <Field label="Correo"><input type="email" inputMode="email" autoComplete="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        {err && <p className="sh-err" role="alert" style={{ margin: 0 }}>{err}</p>}
        <button className="sh-btn sh-btn--crimson sh-btn--block" type="submit" disabled={busy || f.number.trim().length < 3 || !/.+@.+\..+/.test(f.email)}>{busy ? "Buscando…" : "Ver mi pedido"}</button>
      </form>
    </div>
  );
}
