/* Acceso al panel. Mismo lenguaje que la landing: fondo crema, red de nodos con pulsos carmesi, HUD de camara
   y una tarjeta oscura tipo "pase de acceso". Los nodos cercanos al puntero se encienden. */
import { useEffect, useMemo, useRef, useState, type FormEvent, type PointerEvent } from "react";
import { ApiError } from "../../lib/api";
import { useSession } from "../../app/session";
import { I, Icon } from "../../ui/components";

const LANDING = "https://jcanalyticscr.github.io/crimson-web/";
const NODES = [[120, 140], [380, 90], [700, 160], [1050, 110], [1320, 190], [200, 420], [520, 470], [900, 430], [1250, 470], [80, 680], [420, 700], [760, 660], [1120, 690], [1380, 640]];
const LINKS = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [1, 6], [2, 6], [3, 7], [4, 8], [5, 6], [6, 7], [7, 8], [5, 9], [6, 10], [7, 11], [8, 12], [8, 13], [10, 11], [11, 12]];
const NODES_M = [[120, 160], [520, 110], [700, 340], [90, 520], [380, 600], [720, 760], [160, 980], [560, 1080], [300, 1330]];
const LINKS_M = [[0, 1], [1, 2], [0, 3], [2, 4], [3, 4], [4, 5], [3, 6], [5, 7], [6, 8], [7, 8], [4, 7]];

function Net({ portrait }: { portrait: boolean }) {
  const svg = useRef<SVGSVGElement>(null);
  const [hot, setHot] = useState<Set<number>>(new Set());
  const N = portrait ? NODES_M : NODES;
  const L = portrait ? LINKS_M : LINKS;
  const reduced = useMemo(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches, []);

  useEffect(() => {
    if (reduced) return;
    const move = (e: globalThis.PointerEvent) => {
      const s = svg.current; if (!s) return;
      const pt = s.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
      const m = s.getScreenCTM(); if (!m) return;
      const p = pt.matrixTransform(m.inverse());
      const near = new Set<number>();
      N.forEach(([x, y], i) => { if (Math.hypot(x - p.x, y - p.y) < 230) near.add(i); });
      setHot((prev) => (prev.size === near.size && [...near].every((i) => prev.has(i)) ? prev : near));
    };
    window.addEventListener("pointermove", move, { passive: true });
    return () => window.removeEventListener("pointermove", move);
  }, [N, reduced]);

  return (
    <svg ref={svg} className="lg-net" viewBox={portrait ? "0 0 810 1440" : "0 0 1440 810"} preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      {L.map(([a, b], i) => {
        const d = `M${N[a][0]} ${N[a][1]} L${N[b][0]} ${N[b][1]}`;
        const lit = hot.has(a) && hot.has(b);
        return (
          <g key={i}>
            <path d={d} className={`lg-net__line ${lit ? "is-lit" : ""}`} />
            <path d={d} className="lg-net__dash" style={{ animationDelay: `${-i * 0.7}s` }} />
            {!reduced && i % 2 === 0 && <circle r="4" fill="#e2233a"><animateMotion dur={`${6 + (i % 3)}s`} repeatCount="indefinite" path={d} begin={`${-i * 1.3}s`} /></circle>}
          </g>
        );
      })}
      {N.map(([x, y], i) => (
        <g key={i}>
          <circle cx={x} cy={y} r="14" className={`lg-net__glow ${hot.has(i) ? "is-lit" : ""}`} style={{ animationDuration: `${3 + (i % 4)}s`, animationDelay: `${i * 0.4}s` }} />
          <circle cx={x} cy={y} r={hot.has(i) ? 8 : 5} className="lg-net__node" />
        </g>
      ))}
    </svg>
  );
}

export default function Login() {
  const { login } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [totp, setTotp] = useState("");
  const [need2fa, setNeed2fa] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [shake, setShake] = useState(0);
  const [clock, setClock] = useState("");
  const [portrait, setPortrait] = useState(() => window.matchMedia("(max-width: 767px)").matches);
  const card = useRef<HTMLFormElement>(null);

  useEffect(() => {
    const t = () => setClock(new Date().toLocaleTimeString("es-CR", { hour12: false, timeZone: "America/Costa_Rica" }));
    t(); const id = setInterval(t, 1000);
    const mq = window.matchMedia("(max-width: 767px)"); const on = () => setPortrait(mq.matches); mq.addEventListener("change", on);
    return () => { clearInterval(id); mq.removeEventListener("change", on); };
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(null); setBusy(true);
    try { await login(email.trim(), password, totp || undefined); }
    catch (ex) {
      if (ex instanceof ApiError && ex.headers?.get("X-2FA") === "required") { setNeed2fa(true); setErr(null); }
      else { setErr(ex instanceof Error ? ex.message : "No se pudo iniciar sesión"); setShake((n) => n + 1); }
    } finally { setBusy(false); }
  };

  // brillo que sigue al puntero sobre la tarjeta
  const glow = (e: PointerEvent<HTMLFormElement>) => {
    const r = card.current?.getBoundingClientRect(); if (!r) return;
    card.current!.style.setProperty("--mx", `${e.clientX - r.left}px`);
    card.current!.style.setProperty("--my", `${e.clientY - r.top}px`);
  };

  return (
    <div className="lg">
      <Net portrait={portrait} />
      <div className="lg-grain" aria-hidden="true" />

      <header className="lg-top">
        <a href={LANDING} className="lg-logo" aria-label="Crimson Consulting, volver al sitio"><img src="/logo.png" alt="Crimson Consulting" /></a>
        <span className="lg-hud"><i className="rec-dot" />REC · CAM 00 · ACCESO · {clock}</span>
      </header>

      <main className="lg-main">
        <section className="lg-copy">
          <span className="lg-eyebrow">Panel financiero · Crimson</span>
          <h1 className="lg-title">Tu operación, <em>bajo control</em>.</h1>
          <p className="lg-lead">Cotizaciones, facturación electrónica, cobros por WhatsApp, inventario y contabilidad en un solo panel. Cada persona ve lo que su rol necesita.</p>
          <ul className="lg-roles" aria-label="Perfiles de acceso">
            <li><i style={{ background: "#e2233a" }} />Administrador</li>
            <li><i style={{ background: "#2f6fed" }} />Vendedor</li>
            <li><i style={{ background: "#0e9f6e" }} />Caja</li>
            <li><i style={{ background: "#6b46c1" }} />Contabilidad</li>
          </ul>
        </section>

        <form ref={card} className={`lg-card ${busy ? "is-busy" : ""}`} onSubmit={submit} onPointerMove={glow} key={shake} data-shake={shake > 0 || undefined} noValidate>
          <span className="lg-corners" aria-hidden="true" />
          <div className="lg-card__strip"><span><i className="rec-dot" />Pase de acceso</span><span>{need2fa ? "Paso 2 / 2" : "Panel · CR"}</span></div>
          <div>
            <h2 className="lg-card__title">{need2fa ? "Verificación en dos pasos" : "Iniciar sesión"}</h2>
            <p className="lg-card__sub">{need2fa ? "Escribí el código de 6 dígitos de tu app de autenticación." : "Usá el correo con el que te invitaron."}</p>
          </div>

          {!need2fa ? <>
            <label className="lg-field">
              <span>Correo</span>
              <input type="email" autoComplete="username" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="nombre@empresa.com" required autoFocus />
            </label>
            <label className="lg-field">
              <span>Contraseña</span>
              <div className="lg-pass">
                <input type={show ? "text" : "password"} autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
                <button type="button" onClick={() => setShow((s) => !s)} aria-label={show ? "Ocultar contraseña" : "Mostrar contraseña"}>{show ? "Ocultar" : "Ver"}</button>
              </div>
            </label>
          </> : (
            <label className="lg-field">
              <span>Código 2FA</span>
              <input className="lg-otp" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={totp} onChange={(e) => setTotp(e.target.value.replace(/\D/g, ""))} autoFocus />
            </label>
          )}

          {err && <p className="lg-err" role="alert"><Icon d={I.x} size={14} />{err}</p>}

          <button className="lg-go" disabled={busy || !email || !password || (need2fa && totp.length < 6)}>
            {busy ? <span className="spinner" /> : <>{need2fa ? "Verificar" : "Entrar al panel"}<Icon d={I.arrow} size={18} /></>}
          </button>
          {need2fa && <button type="button" className="lg-link" onClick={() => { setNeed2fa(false); setTotp(""); }}>← Usar otra cuenta</button>}

          <div className="lg-card__foot">
            <span>Sesión cifrada · 2FA · bloqueo por intentos</span>
            <span>¿Sin acceso? Pedile una invitación a tu administrador.</span>
          </div>
        </form>
      </main>

      <footer className="lg-bottom">
        <span>Crimson Consulting × JC Analytics</span>
        <a href={LANDING}>← Volver al sitio</a>
      </footer>
    </div>
  );
}
