/* Aceptar invitacion (publico). Primero se revisa el enlace: a que empresa y con que rol, o si vencio / fue revocado.
   Cuenta nueva: nombre y contrasena fuerte. Cuenta que ya existe (otra empresa): se acepta confirmando con su
   contrasena de siempre (el enlace solo no basta: quien invita tambien lo tiene); la invitacion nunca cambia la
   contrasena de una cuenta existente. */
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, parseTs } from "../../lib/api";
import { roleMeta } from "../../app/roles";
import { I, Icon } from "../../ui/components";

type Info = { email: string; tenant: string; role: string; existing_account: boolean; expires_at: string };

export default function Invite() {
  const { token } = useParams();
  const nav = useNavigate();
  const [info, setInfo] = useState<Info | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [form, setForm] = useState({ full_name: "", password: "", password2: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Info>("/settings/invitations/check", { method: "POST", json: { token } }).then(setInfo).catch(() => setInvalid(true));
  }, [token]);

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(null);
    if (!info) return;
    if (info.existing_account) {
      if (!form.password) { setErr("Escribí la contraseña de tu cuenta."); return; }
    } else {
      if (form.full_name.trim().length < 2) { setErr("Escribí tu nombre completo."); return; }
      if (form.password.length < 10 || !/\d/.test(form.password) || !/\p{L}/u.test(form.password)) { setErr("Mínimo 10 caracteres con letras y números."); return; }
      if (form.password !== form.password2) { setErr("Las dos contraseñas no coinciden."); return; }
    }
    setBusy(true);
    try {
      const body = info.existing_account ? { token, password: form.password } : { token, full_name: form.full_name.trim(), password: form.password };
      await api("/settings/invitations/accept", { method: "POST", json: body });
      const notice = info.existing_account ? `Listo: ya tenés acceso a ${info.tenant}. Entrá con tu contraseña de siempre.` : "Cuenta creada. Entrá con tu correo y tu contraseña.";
      nav("/login", { replace: true, state: { notice } });
    } catch (ex) { setErr(ex instanceof Error ? ex.message : "No se pudo aceptar la invitación"); }
    finally { setBusy(false); }
  };

  return (
    <div className="login" style={{ gridTemplateColumns: "1fr" }}>
      <section className="login__form">
        <form className="login__box" onSubmit={submit}>
          <img className="login__logo" src="/logo.png" alt="Crimson" />
          {invalid ? (
            <div>
              <h2 className="h1">Invitación no disponible</h2>
              <p className="muted" style={{ marginTop: 6 }}>Esta invitación venció o fue revocada. Pedile a quien te invitó que te la reenvíe.</p>
            </div>
          ) : !info ? (
            <span className="spinner" />
          ) : (
            <>
              <div>
                <h2 className="h1">Aceptar invitación</h2>
                <p className="muted" style={{ marginTop: 6 }}>Te invitaron a <b>{info.tenant}</b> como <b>{roleMeta(info.role).label}</b> · {info.email}</p>
                <p className="muted" style={{ marginTop: 4, fontSize: 12.5 }}>El enlace vence el {parseTs(info.expires_at).toLocaleDateString("es-CR", { day: "2-digit", month: "short", year: "numeric" })} y sirve una sola vez.</p>
              </div>
              {info.existing_account ? (
                <>
                  <p style={{ fontSize: 14, margin: 0 }}>Ya tenés una cuenta con este correo. Para aceptar, confirmá con tu contraseña de siempre; después entrás con esa misma. Si no la recordás, pedile al administrador de la empresa donde ya entrás que la restablezca.</p>
                  <div className="field"><label>Tu contraseña de siempre</label><input className="input" type="password" autoComplete="current-password" maxLength={200} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required /></div>
                </>
              ) : (
                <>
                  <div className="field"><label>Nombre completo</label><input className="input" autoComplete="name" maxLength={160} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required /></div>
                  <div className="field"><label>Contraseña</label><input className="input" type="password" autoComplete="new-password" maxLength={200} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required /><small>Mínimo 10 caracteres con letras y números.</small></div>
                  <div className="field"><label>Repetí la contraseña</label><input className="input" type="password" autoComplete="new-password" maxLength={200} value={form.password2} onChange={(e) => setForm({ ...form, password2: e.target.value })} required /></div>
                </>
              )}
              {err && <p style={{ color: "var(--bad)", fontSize: 13 }} role="alert">{err}</p>}
              <button className="btn btn--crimson" disabled={busy}><Icon d={I.check} />{info.existing_account ? "Aceptar invitación" : "Crear cuenta"}</button>
            </>
          )}
        </form>
      </section>
    </div>
  );
}
