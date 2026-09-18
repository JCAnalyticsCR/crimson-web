/* Aceptar invitacion (publico): crea la cuenta con contrasena fuerte y redirige al login. */
import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../../lib/api";
import { I, Icon } from "../../ui/components";

export default function Invite() {
  const { token } = useParams();
  const nav = useNavigate();
  const [form, setForm] = useState({ full_name: "", password: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try { await api("/settings/invitations/accept", { method: "POST", json: { token, ...form } }); nav("/", { replace: true }); }
    catch (ex) { setErr(ex instanceof Error ? ex.message : "No se pudo aceptar la invitación"); }
    finally { setBusy(false); }
  };
  return (
    <div className="login" style={{ gridTemplateColumns: "1fr" }}>
      <section className="login__form">
        <form className="login__box" onSubmit={submit}>
          <img className="login__logo" src="/logo.png" alt="Crimson" />
          <div><h2 className="h1">Aceptar invitación</h2><p className="muted" style={{ marginTop: 6 }}>Creá tu contraseña para entrar al panel.</p></div>
          <div className="field"><label>Nombre completo</label><input className="input" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required /></div>
          <div className="field"><label>Contraseña</label><input className="input" type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required /><small>Mínimo 10 caracteres con letras y números.</small></div>
          {err && <p style={{ color: "var(--bad)", fontSize: 13 }}>{err}</p>}
          <button className="btn btn--crimson" disabled={busy}><Icon d={I.check} />Crear cuenta</button>
        </form>
      </section>
    </div>
  );
}
