/* "¿Olvidaste tu contraseña?" (publico, /recuperar). La respuesta es SIEMPRE la misma, exista o no la cuenta, para
   no revelar quien tiene acceso. Mientras el correo de la plataforma no este activo no llega nada: por eso el texto
   manda tambien al administrador, que la restablece desde Ajustes → Usuarios. */
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../../lib/api";
import { I, Icon } from "../../ui/components";

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(null);
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) { setErr("Escribí tu correo completo (ejemplo: nombre@empresa.com)."); return; }
    setBusy(true);
    try { await api("/auth/password-reset/request", { method: "POST", json: { email: email.trim() } }); setSent(true); }
    catch (ex) { setErr(ex instanceof ApiError && ex.status === 429 ? "Demasiados intentos desde esta conexión. Esperá unos minutos." : ex instanceof Error ? ex.message : "No se pudo enviar"); }
    finally { setBusy(false); }
  };

  return (
    <div className="login" style={{ gridTemplateColumns: "1fr" }}>
      <section className="login__form">
        <form className="login__box" onSubmit={submit}>
          <img className="login__logo" src="/logo.png" alt="Crimson" />
          <div>
            <h2 className="h1">¿Olvidaste tu contraseña?</h2>
            <p className="muted" style={{ marginTop: 6 }}>{sent ? "Revisá tu correo." : "Escribí el correo con el que entrás a la plataforma."}</p>
          </div>
          {sent ? (
            <p style={{ fontSize: 14, margin: 0 }} role="status">Si ese correo tiene acceso, en unos minutos te llega un enlace para crear una contraseña nueva (vence en 1 hora). Si no te llega, pedile a tu administrador que te la restablezca desde Ajustes → Usuarios.</p>
          ) : (
            <>
              <div className="field"><label>Correo</label><input className="input" type="email" inputMode="email" autoComplete="username" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} placeholder="nombre@empresa.com" required /></div>
              {err && <p style={{ color: "var(--bad)", fontSize: 13 }} role="alert">{err}</p>}
              <button className="btn btn--crimson" disabled={busy || !email}><Icon d={I.check} />{busy ? "Enviando…" : "Enviarme el enlace"}</button>
            </>
          )}
          <Link to="/login" className="meta">← Volver a iniciar sesión</Link>
        </form>
      </section>
    </div>
  );
}
