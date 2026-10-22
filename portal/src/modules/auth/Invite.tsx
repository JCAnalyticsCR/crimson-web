/* Aceptar invitacion (publico). Primero se revisa el enlace: a que empresa y con que rol, o si vencio / fue revocado.
   Cuenta nueva: nombre y contrasena fuerte. Cuenta que ya existe (otra empresa): se acepta confirmando con su
   contrasena de siempre (el enlace solo no basta: quien invita tambien lo tiene); la invitacion nunca cambia la
   contrasena de una cuenta existente.
   Reunion 2, pedido 27: con otra sesion abierta en el navegador se entraba con la cuenta vieja (cargo "Ventas" en vez
   de la nueva). Si hay una sesion de otra persona, se avisa y se cierra antes de aceptar; al aceptar, la API cierra la
   sesion de este navegador y aqui se limpia la del portal, para entrar con la cuenta de la invitacion. */
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, parseTs } from "../../lib/api";
import { roleMeta } from "../../app/roles";
import { useSession } from "../../app/session";
import { I, Icon } from "../../ui/components";

type Info = { email: string; tenant: string; role: string; existing_account: boolean; expires_at: string };

export default function Invite() {
  const { token } = useParams();
  const nav = useNavigate();
  const { me, loading, logout } = useSession();
  const [info, setInfo] = useState<Info | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [form, setForm] = useState({ full_name: "", password: "", password2: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Info>("/settings/invitations/check", { method: "POST", json: { token } }).then(setInfo).catch(() => setInvalid(true));
  }, [token]);

  // sesión abierta de OTRA cuenta en este navegador: hay que cerrarla antes de aceptar
  const otraSesion = !!me && !!info && me.user.email.toLowerCase() !== info.email.toLowerCase();
  const cerrarSesion = async () => {
    setBusy(true); setErr(null);
    try { await logout(); }
    catch { setErr("No se pudo cerrar la sesión anterior. Cierre la sesión desde el panel e intente de nuevo."); }
    finally { setBusy(false); }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(null);
    if (!info || otraSesion) return;
    if (info.existing_account) {
      if (!form.password) { setErr("Escriba la contraseña de su cuenta."); return; }
    } else {
      if (form.full_name.trim().length < 2) { setErr("Escriba su nombre completo."); return; }
      if (form.password.length < 10 || !/\d/.test(form.password) || !/\p{L}/u.test(form.password)) { setErr("Mínimo 10 caracteres con letras y números."); return; }
      if (form.password !== form.password2) { setErr("Las dos contraseñas no coinciden."); return; }
    }
    setBusy(true);
    try {
      const body = info.existing_account ? { token, password: form.password } : { token, full_name: form.full_name.trim(), password: form.password };
      await api("/settings/invitations/accept", { method: "POST", json: body });
      // la API ya cerró la sesión de este navegador; aquí se limpia la del portal para no volver a entrar con ella
      if (me) { try { await logout(); } catch { /* ya estaba cerrada */ } }
      const notice = info.existing_account ? `Listo: ya tiene acceso a ${info.tenant}. Entre con su contraseña de siempre.` : "Cuenta creada. Entre con su correo y su contraseña.";
      nav("/login", { replace: true, state: { notice, email: info.email } });
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
              <p className="muted" style={{ marginTop: 6 }}>Esta invitación venció o fue revocada. Pídale a quien lo invitó que se la reenvíe.</p>
            </div>
          ) : !info || loading ? (
            <span className="spinner" />
          ) : (
            <>
              <div>
                <h2 className="h1">Aceptar invitación</h2>
                <p className="muted" style={{ marginTop: 6 }}>Lo invitaron a <b>{info.tenant}</b> como <b>{roleMeta(info.role).label}</b> · {info.email}</p>
                <p className="muted" style={{ marginTop: 4, fontSize: 12.5 }}>El enlace vence el {parseTs(info.expires_at).toLocaleDateString("es-CR", { day: "2-digit", month: "short", year: "numeric" })} y sirve una sola vez.</p>
              </div>
              {otraSesion && me ? (
                <div role="alert" style={{ border: "1px solid var(--warn)", borderRadius: 10, padding: "10px 12px", fontSize: 14, display: "flex", flexDirection: "column", gap: 8 }}>
                  <span>En este navegador hay una sesión abierta como <b>{me.user.full_name}</b> ({me.user.email}). Esta invitación es para <b>{info.email}</b>.</span>
                  <span className="muted" style={{ fontSize: 13 }}>Para aceptarla hay que cerrar esa sesión; si no, se entraría con la cuenta anterior. Si esta invitación no es suya, no continúe.</span>
                  <button type="button" className="btn btn--crimson" disabled={busy} onClick={cerrarSesion}>{busy ? "Cerrando…" : "Cerrar esa sesión y continuar"}</button>
                </div>
              ) : (
                <>
                  {me && <p className="muted" style={{ fontSize: 13, margin: 0 }}>Al aceptar se cierra la sesión abierta en este navegador; después entre de nuevo para ver {info.tenant}.</p>}
                  {info.existing_account ? (
                    <>
                      <p style={{ fontSize: 14, margin: 0 }}>Ya tiene una cuenta con este correo. Para aceptar, confirme con su contraseña de siempre; después entra con esa misma. Si no la recuerda, pídale al administrador de la empresa donde ya entra que la restablezca.</p>
                      <div className="field"><label>Su contraseña de siempre</label><input className="input" type="password" autoComplete="current-password" maxLength={200} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required /></div>
                    </>
                  ) : (
                    <>
                      <div className="field"><label>Nombre completo</label><input className="input" autoComplete="name" maxLength={160} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required /></div>
                      <div className="field"><label>Contraseña</label><input className="input" type="password" autoComplete="new-password" maxLength={200} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required /><small>Mínimo 10 caracteres con letras y números.</small></div>
                      <div className="field"><label>Repita la contraseña</label><input className="input" type="password" autoComplete="new-password" maxLength={200} value={form.password2} onChange={(e) => setForm({ ...form, password2: e.target.value })} required /></div>
                    </>
                  )}
                  <button className="btn btn--crimson" disabled={busy}><Icon d={I.check} />{info.existing_account ? "Aceptar invitación" : "Crear cuenta"}</button>
                </>
              )}
              {err && <p style={{ color: "var(--bad)", fontSize: 13 }} role="alert">{err}</p>}
            </>
          )}
        </form>
      </section>
    </div>
  );
}
