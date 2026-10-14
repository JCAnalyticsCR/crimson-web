/* Crear contrasena nueva con un enlace de un solo uso (publico, /restablecer/:token). Lo manda el administrador
   (Ajustes → Usuarios → Restablecer) o la persona misma ("¿Olvidaste tu contraseña?"). El token viaja en el cuerpo
   del POST, nunca en la URL de la API. Al usarlo se cierran todas las sesiones abiertas de esa cuenta. */
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../../lib/api";
import { I, Icon } from "../../ui/components";

export default function ResetPassword() {
  const { token } = useParams();
  const nav = useNavigate();
  const [email, setEmail] = useState<string | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [done, setDone] = useState(false);
  const [pw, setPw] = useState({ a: "", b: "" });
  const [show, setShow] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ email: string; expires_at: string }>("/auth/password-reset/check", { method: "POST", json: { token } }).then((r) => setEmail(r.email)).catch(() => setInvalid(true));
  }, [token]);

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(null);
    if (pw.a.length < 10 || !/\d/.test(pw.a) || !/\p{L}/u.test(pw.a)) { setErr("Mínimo 10 caracteres con letras y números."); return; }
    if (pw.a !== pw.b) { setErr("Las dos contraseñas no coinciden."); return; }
    setBusy(true);
    try { await api("/auth/password-reset/confirm", { method: "POST", json: { token, new_password: pw.a } }); setDone(true); setPw({ a: "", b: "" }); }
    catch (ex) {
      const msg = ex instanceof Error ? ex.message : "No se pudo guardar";
      if (/venció|ya se usó/.test(msg)) setInvalid(true); else setErr(msg);
    } finally { setBusy(false); }
  };

  return (
    <div className="login" style={{ gridTemplateColumns: "1fr" }}>
      <section className="login__form">
        <form className="login__box" onSubmit={submit}>
          <img className="login__logo" src="/logo.png" alt="Crimson" />
          {done ? (
            <>
              <div>
                <h2 className="h1">Listo</h2>
                <p className="muted" style={{ marginTop: 6 }}>Ya podés entrar con tu contraseña nueva.</p>
                <p className="muted" style={{ marginTop: 4, fontSize: 12.5 }}>Por seguridad se cerraron todas tus sesiones abiertas.</p>
              </div>
              <button type="button" className="btn btn--crimson" onClick={() => nav("/login", { replace: true, state: { notice: "Contraseña actualizada. Entrá con la nueva." } })}><Icon d={I.arrow} />Ir a iniciar sesión</button>
            </>
          ) : invalid ? (
            <>
              <div>
                <h2 className="h1">Enlace no disponible</h2>
                <p className="muted" style={{ marginTop: 6 }}>Este enlace venció o ya se usó. Pedile al administrador uno nuevo.</p>
              </div>
              <Link to="/recuperar" className="meta">¿Olvidaste tu contraseña?</Link>
            </>
          ) : !email ? (
            <span className="spinner" />
          ) : (
            <>
              <div>
                <h2 className="h1">Contraseña nueva</h2>
                <p className="muted" style={{ marginTop: 6 }}>Creá tu contraseña nueva para <b>{email}</b>.</p>
              </div>
              <div className="field">
                <label>Contraseña nueva</label>
                <div style={{ display: "flex", gap: 8 }}>
                  <input className="input" style={{ flex: 1, minWidth: 0 }} type={show ? "text" : "password"} autoComplete="new-password" maxLength={200} value={pw.a} onChange={(e) => setPw({ ...pw, a: e.target.value })} required autoFocus />
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => setShow((s) => !s)} aria-label={show ? "Ocultar contraseña" : "Mostrar contraseña"}>{show ? "Ocultar" : "Ver"}</button>
                </div>
                <small>Mínimo 10 caracteres con letras y números. Distinta de la anterior.</small>
              </div>
              <div className="field"><label>Repetila</label><input className="input" type={show ? "text" : "password"} autoComplete="new-password" maxLength={200} value={pw.b} onChange={(e) => setPw({ ...pw, b: e.target.value })} required /></div>
              {err && <p style={{ color: "var(--bad)", fontSize: 13 }} role="alert">{err}</p>}
              <button className="btn btn--crimson" disabled={busy || !pw.a || !pw.b}><Icon d={I.check} />{busy ? "Guardando…" : "Guardar contraseña"}</button>
              <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Al guardarla se cierran todas tus sesiones abiertas.</p>
            </>
          )}
        </form>
      </section>
    </div>
  );
}
