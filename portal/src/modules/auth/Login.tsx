import { useState, type FormEvent } from "react";
import { ApiError } from "../../lib/api";
import { useSession } from "../../app/session";
import { I, Icon } from "../../ui/components";

const NODES = [[120,140],[380,90],[700,160],[1050,110],[1320,190],[200,420],[520,470],[900,430],[1250,470],[80,680],[420,700],[760,660],[1120,690],[1380,640]];
const LINKS = [[0,1],[1,2],[2,3],[3,4],[0,5],[1,6],[2,6],[3,7],[4,8],[5,6],[6,7],[7,8],[5,9],[6,10],[7,11],[8,12],[8,13],[10,11],[11,12]];

export default function Login() {
  const { login } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [need2fa, setNeed2fa] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(null); setBusy(true);
    try { await login(email, password, totp || undefined); }
    catch (ex) {
      if (ex instanceof ApiError && ex.headers?.get("X-2FA") === "required") setNeed2fa(true);
      else setErr(ex instanceof Error ? ex.message : "No se pudo iniciar sesión");
    } finally { setBusy(false); }
  };

  return (
    <div className="login">
      <section className="login__art">
        <svg className="login__net" viewBox="0 0 1440 810" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          {LINKS.map(([a, b], i) => <line key={i} x1={NODES[a][0]} y1={NODES[a][1]} x2={NODES[b][0]} y2={NODES[b][1]} stroke="rgba(255,255,255,.12)" strokeWidth="1.5" />)}
          {LINKS.filter((_, i) => i % 3 === 0).map(([a, b], i) => (
            <circle key={i} r="4" fill="#e2233a"><animateMotion dur={`${6 + i}s`} repeatCount="indefinite" path={`M${NODES[a][0]} ${NODES[a][1]} L${NODES[b][0]} ${NODES[b][1]}`} /></circle>
          ))}
          {NODES.map(([x, y], i) => <circle key={i} cx={x} cy={y} r="5" fill="#f4f1ee" />)}
        </svg>
        <div className="login__hud"><i className="rec-dot" />REC · PANEL CRIMSON · 01 / 08</div>
        <div style={{ position: "relative" }}>
          <h1 className="display">Control financiero que <em>protege</em> tu operación.</h1>
          <p style={{ marginTop: 16, maxWidth: "44ch", color: "rgba(244,241,238,.75)" }}>Cotizaciones, facturas, cobros por WhatsApp y factura electrónica de Costa Rica, en un solo panel.</p>
        </div>
        <div className="login__hud">Crimson Consulting × JC Analytics · v0.1</div>
      </section>
      <section className="login__form">
        <form className="login__box" onSubmit={submit}>
          <img className="login__logo" src="/logo.png" alt="Crimson" style={{ filter: "var(--logo-filter, none)" }} />
          <div>
            <h2 className="h1">Iniciar sesión</h2>
            <p className="muted" style={{ marginTop: 6 }}>Acceso al panel de tu empresa.</p>
          </div>
          <div className="field"><label>Correo</label><input className="input" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></div>
          <div className="field"><label>Contraseña</label><input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></div>
          {need2fa && <div className="field"><label>Código 2FA</label><input className="input input--mono" inputMode="numeric" maxLength={6} value={totp} onChange={(e) => setTotp(e.target.value)} autoFocus /><small>Abrí tu app de autenticación.</small></div>}
          {err && <p style={{ color: "var(--bad)", fontSize: 13 }}>{err}</p>}
          <button className="btn btn--crimson" disabled={busy}>{busy ? <span className="spinner" /> : <Icon d={I.arrow} />}Entrar</button>
          <p className="meta" style={{ textAlign: "center" }}>Sesión cifrada · 2FA disponible</p>
        </form>
      </section>
    </div>
  );
}
