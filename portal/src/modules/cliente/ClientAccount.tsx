/* Mi cuenta del cliente: cambiar la contrasena. (El panel interno tiene su propia pantalla en Ajustes.) */
import { useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { I, Icon } from "../../ui/components";

export default function ClientAccount() {
  const { me, toast } = useSession();
  const [f, setF] = useState({ current_password: "", new_password: "" });
  const [busy, setBusy] = useState(false);
  const guardar = async () => {
    setBusy(true);
    try { await api("/auth/password", { method: "POST", json: f }); setF({ current_password: "", new_password: "" }); toast("Contraseña actualizada"); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo cambiar", "bad"); }
    finally { setBusy(false); }
  };
  return (
    <>
      <div className="cp-h"><div><div className="meta">{me?.user.email}</div><h1 className="h1">Mi cuenta</h1></div></div>
      <div className="cp-card cp-form" style={{ maxWidth: 460 }}>
        <b>Cambiar contraseña</b>
        <input className="input" type="password" autoComplete="current-password" placeholder="Contraseña actual" value={f.current_password} onChange={(e) => setF({ ...f, current_password: e.target.value })} />
        <input className="input" type="password" autoComplete="new-password" placeholder="Contraseña nueva" value={f.new_password} onChange={(e) => setF({ ...f, new_password: e.target.value })} />
        <span className="meta">Mínimo 10 caracteres, con letras y números.</span>
        <button className="btn btn--crimson" disabled={busy || !f.current_password || f.new_password.length < 10} onClick={guardar} style={{ alignSelf: "flex-start" }}><Icon d={I.check} />Guardar</button>
      </div>
    </>
  );
}
