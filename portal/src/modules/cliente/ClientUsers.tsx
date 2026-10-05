/* Usuarios de mi empresa (solo el encargado): invitar compañeros como usuarios y desactivarlos.
   El rol y la empresa los pone el servidor; aqui solo se escribe el correo. */
import { useCallback, useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { useSession } from "../../app/session";
import { I, Icon } from "../../ui/components";
import { InviteLink, type InviteOut } from "./InviteLink";

type U = { id: number; name: string; email: string; role: string; active: boolean; last_login: string | null; me: boolean };
type Data = { users: U[]; invitations: { id: number; email: string; role: string; expires_at: string }[] };
const ROL: Record<string, string> = { cliente_admin: "Encargado", cliente_usuario: "Usuario" };

export default function ClientUsers() {
  const { toast } = useSession();
  const [d, setD] = useState<Data | null>(null);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [inv, setInv] = useState<{ inv: InviteOut; email: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api<Data>("/cliente/usuarios").then(setD).catch((e) => toast(e.message, "bad")), [toast]);
  useEffect(() => { load(); }, [load]);

  const invitar = async () => {
    setBusy(true);
    try {
      const r = await api<InviteOut>("/cliente/usuarios", { method: "POST", json: { email, name: name || null } });
      setInv({ inv: r, email }); setEmail(""); setName(""); load();
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo invitar", "bad"); }
    finally { setBusy(false); }
  };
  const activar = async (u: U, active: boolean) => {
    try { await api(`/cliente/usuarios/${u.id}`, { method: "PATCH", json: { active } }); toast(active ? "Usuario reactivado" : "Usuario desactivado: ya no puede entrar"); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  return (
    <>
      <div className="cp-h"><div><div className="meta">Su empresa</div><h1 className="h1">Usuarios</h1></div></div>
      <div className="cp-grid cp-grid--2" style={{ alignItems: "start" }}>
        <div className="cp-card cp-form">
          <b>Invitar a alguien de su empresa</b>
          <span className="meta">Entra como usuario: reporta fallas y ve los tickets y equipos. No ve facturas ni cotizaciones.</span>
          <input className="input" type="email" autoComplete="off" placeholder="correo@suempresa.com" value={email} onChange={(e) => setEmail(e.target.value)} />
          <input className="input" placeholder="Nombre (opcional)" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
          <button className="btn btn--crimson" disabled={busy || !/^\S+@\S+\.\S+$/.test(email)} onClick={invitar} style={{ alignSelf: "flex-start" }}><Icon d={I.plus} />Invitar</button>
          {inv && <InviteLink inv={inv.inv} email={inv.email} />}
        </div>
        <div>
          {!d ? <span className="spinner" /> : (
            <div className="cp-list">
              {d.users.map((u) => (
                <div key={u.id} className="cp-row">
                  <div className="cp-row__main"><b>{u.name}{u.me ? " (usted)" : ""}</b><div className="cp-row__meta">{u.email} · {ROL[u.role] || u.role}{u.last_login ? ` · entró ${fmtDate(u.last_login.slice(0, 10))}` : " · no ha entrado"}</div></div>
                  <div className="cp-row__side">
                    {!u.active && <span className="badge badge--muted">Desactivado</span>}
                    {u.role === "cliente_usuario" && !u.me && <button className="btn btn--ghost btn--sm" onClick={() => activar(u, !u.active)}>{u.active ? "Desactivar" : "Reactivar"}</button>}
                  </div>
                </div>
              ))}
              {d.invitations.map((i) => (
                <div key={`i${i.id}`} className="cp-row">
                  <div className="cp-row__main"><b>{i.email}</b><div className="cp-row__meta">Invitación pendiente · vence {fmtDate(i.expires_at.slice(0, 10))}</div></div>
                  <span className="badge badge--warn">Pendiente</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
