/* Mi equipo: el aliado invita a sus técnicos (siempre como Técnico de aliado), revoca invitaciones y activa o
   desactiva técnicos. Cupos usados contra el tope del plan; si la mensualidad es por usuario, lo avisa.
   La invitación le llega a la persona por correo: el portal no muestra el enlace (con él, cualquiera aceptaría por
   ella) ni confirma si el correo ya tiene otro acceso. Si no le llega, el enlace lo entrega Crimson. */
import { useCallback, useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, I, Icon } from "../../ui/components";
import { errorText, useAlly } from "./shared";

type U = { id: number; name: string; email: string; role: string; active: boolean; last_login: string | null; me?: boolean };
type Data = { users: U[]; invitations: { id: number; email: string; role: string; expires_at: string }[]; seats: { used: number; max: number | null }; plan_por_usuario: boolean };

export default function AllyUsers() {
  const { toast } = useSession();
  const { readOnly } = useAlly();
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [aviso, setAviso] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { api<Data>("/aliado/usuarios").then(setD).catch((e) => setErr(errorText(e))); }, []);
  useEffect(load, [load]);
  if (err) return <p className="muted">{err}</p>;
  if (!d) return <div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  const invitar = async () => {
    setBusy(true);
    try { const r = await api<{ detail: string }>("/aliado/usuarios", { method: "POST", json: { email } }); setAviso(r.detail); setEmail(""); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo invitar", "bad"); }
    finally { setBusy(false); }
  };
  const revocar = async (id: number) => { try { await api(`/aliado/usuarios/invitaciones/${id}/revocar`, { method: "POST" }); load(); } catch (e) { toast(e instanceof Error ? e.message : "No se pudo revocar", "bad"); } };
  const activo = async (u: U) => { try { await api(`/aliado/usuarios/${u.id}`, { method: "PATCH", json: { active: !u.active } }); load(); } catch (e) { toast(e instanceof Error ? e.message : "No se pudo cambiar", "bad"); } };
  const lleno = d.seats.max !== null && d.seats.used >= d.seats.max;
  return (
    <>
      <div className="cp-h"><div><div className="meta">Cupos: {d.seats.used}{d.seats.max !== null ? ` de ${d.seats.max}` : ""}</div><h1 className="h1">Mi equipo</h1></div></div>
      {d.plan_por_usuario && <p className="muted">Su mensualidad se cobra por usuario activo: activar o desactivar técnicos cambia el próximo cobro.</p>}
      <div className="cp-list">
        {d.users.map((u) => (
          <div key={u.id} className="cp-row">
            <div className="cp-row__main"><b>{u.name}{u.me ? " (usted)" : ""}</b><div className="cp-row__meta">{u.email} · {u.role === "aliado" ? "Aliado comercial" : "Técnico"}{u.last_login ? ` · último ingreso ${fmtDate(u.last_login.slice(0, 10))}` : ""}</div></div>
            <div className="cp-row__side">
              <span className={`badge badge--${u.active ? "ok" : "muted"}`}>{u.active ? "Activo" : "Inactivo"}</span>
              {!readOnly && !u.me && u.role === "tecnico_aliado" && <button className="btn btn--ghost btn--sm" onClick={() => activo(u)}>{u.active ? "Desactivar" : "Activar"}</button>}
            </div>
          </div>
        ))}
        {d.invitations.map((i) => (
          <div key={i.id} className="cp-row">
            <div className="cp-row__main"><b>{i.email}</b><div className="cp-row__meta">Invitación pendiente · vence {fmtDate(i.expires_at.slice(0, 10))}</div></div>
            {!readOnly && <div className="cp-row__side"><button className="btn btn--ghost btn--sm" onClick={() => revocar(i.id)}>Revocar</button></div>}
          </div>
        ))}
      </div>
      {!readOnly && (
        <div className="cp-card cp-form" style={{ marginTop: 16, maxWidth: 520 }}>
          <b>Invitar a un técnico</b>
          {lleno && <span className="badge badge--warn">Su plan permite {d.seats.max} usuarios. Desactive a alguien o escríbale a Crimson.</span>}
          <Field label="Correo"><input className="input" type="email" value={email} onChange={(e) => { setEmail(e.target.value); setAviso(null); }} /></Field>
          <button className="btn btn--crimson" style={{ alignSelf: "flex-start" }} disabled={busy || lleno || !email.includes("@")} onClick={invitar}><Icon d={I.plus} />Invitar</button>
          {aviso && <p className="muted" role="status" style={{ margin: 0 }}>{aviso}</p>}
        </div>
      )}
    </>
  );
}
