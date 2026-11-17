/* Restablecer la contraseña de un usuario del portal de aliados (Administración → Aliados → ficha → Usuarios).
   Lo hace el administrador: enlace por correo, contraseña temporal (se muestra una sola vez) o la contraseña que él
   mismo define. Las dos últimas obligan al usuario a crear una propia al entrar. */
import { useState } from "react";
import { api, parseTs } from "../../lib/api";
import { Field, I, Icon, Modal } from "../../ui/components";

type Result =
  | { mode: "enlace"; emailed: boolean; email_error: string | null; link: string | null; expires_at: string }
  | { mode: "temporal"; temp_password: string; expires_at: string; email: string; name: string; login_url: string }
  | { mode: "definir"; expires_at: string; email: string; name: string; login_url: string };

export type AllyResetTarget = { id: number; name: string; email: string };

const fecha = (s: string) => parseTs(s).toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "numeric", minute: "2-digit" });
const errMsg = (e: unknown) => (e instanceof Error ? e.message : "No se pudo restablecer");
/* Mismo criterio que el servidor (core/security.password_is_strong): 10 o más, con letras y números. */
const fuerte = (s: string) => s.length >= 10 && /\d/.test(s) && /[A-Za-z]/.test(s);

export default function AllyResetModal({ allyId, user, onClose }: { allyId: number; user: AllyResetTarget; onClose: () => void }) {
  const [mode, setMode] = useState<"enlace" | "temporal" | "definir">("enlace");
  const [nueva, setNueva] = useState("");
  const [repite, setRepite] = useState("");
  const [adminPw, setAdminPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<Result | null>(null);
  const [copiado, setCopiado] = useState<string | null>(null);

  const definirOk = mode !== "definir" || (fuerte(nueva) && nueva === repite);
  const listo = adminPw.length > 0 && definirOk;

  const go = async () => {
    setBusy(true); setErr(null);
    try {
      const body: Record<string, string> = { mode, admin_password: adminPw };
      if (mode === "definir") body.new_password = nueva;
      setRes(await api<Result>(`/allies/${allyId}/users/${user.id}/reset-password`, { method: "POST", json: body }));
      setNueva(""); setRepite(""); setAdminPw("");
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const copy = (texto: string, msg: string) => {
    navigator.clipboard.writeText(texto).then(() => setCopiado(msg)).catch(() => setCopiado("No se pudo copiar: seleccionalo y copialo a mano"));
  };

  if (res) return <Resultado r={res} nombre={user.name} email={user.email} copy={copy} copiado={copiado} onClose={onClose} />;

  const opt = (k: "enlace" | "temporal" | "definir", titulo: string, texto: string) => (
    <label className="pay__method" style={{ cursor: "pointer", borderColor: mode === k ? "var(--crimson)" : undefined, boxShadow: mode === k ? "0 0 0 1px var(--crimson)" : undefined }}>
      <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input type="radio" name="modo-reset-aliado" checked={mode === k} onChange={() => setMode(k)} />
        <b style={{ color: "var(--text)" }}>{titulo}</b>
      </span>
      <span>{texto}</span>
    </label>
  );

  return (
    <Modal
      title={`Restablecer contraseña · ${user.name}`}
      onClose={onClose}
      foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" onClick={go} disabled={busy || !listo}>{busy ? "Restableciendo…" : "Restablecer"}</button></>}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {opt("enlace", "Enviarle un enlace por correo", "Él mismo crea su contraseña. El enlace vence en 24 horas y sirve una sola vez. Su contraseña actual sigue sirviendo hasta que use el enlace.")}
        {opt("temporal", "Generar una contraseña temporal", "Se muestra una sola vez para que se la pases. Vence en 48 horas; al entrar tendrá que crear una propia. Se cierran todas sus sesiones.")}
        {opt("definir", "Definir yo la contraseña", "La escribís vos y se la pasás. Vence en 48 horas; al entrar tendrá que crear una propia. Se cierran todas sus sesiones.")}
      </div>
      {mode === "definir" && (
        <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
          <Field label="Contraseña nueva" hint="Mínimo 10 caracteres, con letras y números.">
            <input className="input" type="password" autoComplete="new-password" value={nueva} onChange={(e) => setNueva(e.target.value)} />
          </Field>
          <Field label="Repetila" hint={repite && nueva !== repite ? "No coinciden" : undefined}>
            <input className="input" type="password" autoComplete="new-password" value={repite} onChange={(e) => setRepite(e.target.value)} />
          </Field>
        </div>
      )}
      <Field label="Confirmá con tu contraseña" hint="Tu contraseña de administrador, no la del aliado.">
        <input
          className="input"
          type="password"
          autoComplete="current-password"
          value={adminPw}
          onChange={(e) => setAdminPw(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && listo && !busy) go(); }}
        />
      </Field>
      {err && <p style={{ color: "var(--bad)", fontSize: 13, margin: 0 }} role="alert">{err}</p>}
    </Modal>
  );
}

function Resultado({ r, nombre, email, copy, copiado, onClose }: { r: Result; nombre: string; email: string; copy: (t: string, m: string) => void; copiado: string | null; onClose: () => void }) {
  if (r.mode === "temporal") {
    const msg = `Hola ${r.name}, te restablecí el acceso a Crimson.\nEntrá a ${r.login_url}\nCorreo: ${r.email}\nContraseña temporal: ${r.temp_password}\nVence el ${fecha(r.expires_at)}. Al entrar te va a pedir crear una nueva.`;
    return (
      <Modal title="Contraseña temporal" onClose={onClose} foot={<button className="btn btn--crimson" onClick={onClose}>Listo, ya la copié</button>}>
        <p style={{ fontSize: 13.5, margin: 0 }}>Pasale esta contraseña a <b>{r.name}</b> ({r.email}). Vence el {fecha(r.expires_at)}; al entrar va a tener que crear una propia. Se cerraron todas sus sesiones.</p>
        <code className="mono" style={{ padding: "14px 12px", background: "var(--bg-2)", borderRadius: 10, textAlign: "center", fontSize: 24, letterSpacing: ".08em", userSelect: "all" }}>{r.temp_password}</code>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn btn--soft" onClick={() => copy(r.temp_password, "Contraseña copiada")}><Icon d={I.copy} />Copiar</button>
          <button className="btn btn--ghost" onClick={() => copy(msg, "Mensaje copiado")}><Icon d={I.whatsapp} />Copiar mensaje para WhatsApp</button>
        </div>
        {copiado && <p className="muted" style={{ margin: 0, fontSize: 13 }}>{copiado}</p>}
        <p style={{ color: "var(--bad)", fontSize: 13, fontWeight: 600, margin: 0 }}>Al cerrar esta ventana no se puede volver a ver.</p>
      </Modal>
    );
  }
  if (r.mode === "definir") {
    return (
      <Modal title="Contraseña definida" onClose={onClose} foot={<button className="btn btn--crimson" onClick={onClose}>Listo</button>}>
        <p style={{ fontSize: 13.5, margin: 0 }}>Se cambió la contraseña de <b>{r.name}</b> ({r.email}) y se cerraron sus sesiones. Pasásela por un canal de confianza. Vence el {fecha(r.expires_at)}; al entrar le va a pedir crear una propia.</p>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>Por seguridad la contraseña no se vuelve a mostrar aquí.</p>
      </Modal>
    );
  }
  // enlace
  const msg = r.link ? `Hola ${nombre}, cree su nueva contraseña de Crimson con este enlace (vence en 24 horas, sirve una sola vez):\n${r.link}` : "";
  return (
    <Modal title="Enlace para crear la contraseña" onClose={onClose} foot={<button className="btn btn--crimson" onClick={onClose}>Listo</button>}>
      {r.emailed
        ? <p style={{ fontSize: 13.5, margin: 0 }}>Se envió el enlace a <b>{email}</b>. Vence el {fecha(r.expires_at)}. Su contraseña actual sigue sirviendo hasta que la cambie.</p>
        : <>
            <p style={{ fontSize: 13.5, margin: 0 }}>{r.email_error ? "El correo no salió" : "El correo no está activo"}: mandale este enlace por WhatsApp. Vence el {fecha(r.expires_at)}.</p>
            <code className="mono" style={{ padding: "10px 12px", background: "var(--bg-2)", borderRadius: 10, fontSize: 12, wordBreak: "break-all", userSelect: "all" }}>{r.link}</code>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {r.link && <button className="btn btn--soft" onClick={() => copy(r.link!, "Enlace copiado")}><Icon d={I.copy} />Copiar enlace</button>}
              <button className="btn btn--ghost" onClick={() => copy(msg, "Mensaje copiado")}><Icon d={I.whatsapp} />Copiar mensaje para WhatsApp</button>
            </div>
          </>}
      {copiado && <p className="muted" style={{ margin: 0, fontSize: 13 }}>{copiado}</p>}
    </Modal>
  );
}
