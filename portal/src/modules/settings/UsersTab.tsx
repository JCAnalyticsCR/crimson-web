/* Ajustes → Usuarios (Andrés 13/10, 01:56–02:26 y 11:48): "no me permite hacer nada", "no le llegó el correo",
   "ver bien las contraseñas… podría ser un montón de usuarios". Aquí se gestiona cada usuario interno (datos, rol,
   contraseña, sesiones, activo) y cada invitación (reenviar, revocar, ver si el correo salió).
   Las contraseñas NO se ven nunca, ni el administrador: se guardan cifradas de una sola vía; se restablecen.
   Los secretos (contraseña temporal, enlaces) viven solo en el estado de React y se borran al cerrar la ventana:
   nada en localStorage ni sessionStorage, y nunca en una URL de terceros (sin wa.me): solo portapapeles. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { api, parseTs } from "../../lib/api";
import { useSession } from "../../app/session";
import { registerRoles, roleMeta } from "../../app/roles";
import RoleMatrix from "../../app/RoleMatrix";
import RolesEditor from "./RolesEditor";
import AllyConvert, { AllyMatchChip, type AllyMatch } from "./AllyConvert";
import PortfolioTransfer from "../crm/PortfolioTransfer";
import { Card, Empty, Field, I, Icon, Loading, Modal } from "../../ui/components";

type U = { id: number; email: string; name: string; role: string; active: boolean; totp: boolean; last_login: string | null; is_self: boolean; shared: boolean; must_change_password: boolean; temp_password_expires_at: string | null; locked_until: string | null; login_failures_until?: string | null; reset_link_expires_at: string | null; password_changed_at: string | null; member_since: string | null; notify_email?: string | null; ally_match?: AllyMatch | null; discount_limit_pct?: string | number | null; clientes_cartera?: number };
type Inv = { id: number; email: string; role: string; expires_at: string; status: "pendiente" | "vencida"; sent_count: number; last_sent_at: string | null; invited_by: string | null; email_status: string | null; email_error: string | null };
type UsersResp = { users: U[]; invitations: Inv[]; roles: string[]; role_labels?: Record<string, string>; reauth_roles?: string[]; mail_configured: boolean; support_members: number; cost_roles: string[]; cost_role_choices: string[]; twofa_roles?: string[]; twofa_grace_days?: number; twofa_role_choices?: string[]; max_discount_pct?: string | number };
type InviteOut = { id: number; link: string; emailed: boolean; email_error: string | null; expires_at: string };
type ResetOut =
  | { mode: "temporal"; temp_password: string; expires_at: string; email: string; name: string; login_url: string }
  | { mode: "enlace"; emailed: boolean; email_error: string | null; link: string | null; expires_at: string };
type Filtro = "todos" | "activos" | "inactivos" | "invitaciones";

const fecha = (s: string | null | undefined) => (s ? parseTs(s).toLocaleDateString("es-CR", { day: "2-digit", month: "short", year: "numeric" }) : "—");
const fechaHora = (s: string | null | undefined) => (s ? parseTs(s).toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "numeric", minute: "2-digit" }) : "—");
const hora = (s: string | null | undefined) => (s ? parseTs(s).toLocaleTimeString("es-CR", { hour: "numeric", minute: "2-digit" }) : "—");
const errMsg = (e: unknown) => (e instanceof Error ? e.message : "Error");

/** El rol actual siempre aparece como opción (p. ej. un rol personalizado): si no, el navegador mostraría otro. */
const roleOptions = (roles: string[], current: string, canAdmin: boolean) => {
  const base = roles.includes(current) ? roles : [current, ...roles];
  return canAdmin ? base : base.filter((r) => r !== "admin" || r === current);
};

function Chip({ tone, title, children }: { tone: "ok" | "warn" | "bad" | "info" | "muted"; title?: string; children: ReactNode }) {
  return <span className={`badge badge--${tone}`} title={title} style={{ whiteSpace: "normal", lineHeight: 1.35, borderRadius: 8, padding: "3px 8px" }}>{children}</span>;
}

export default function UsersTab() {
  const { me, role, toast, allows } = useSession();
  const [params, setParams] = useSearchParams();
  const canEdit = allows("settings.configurar");
  const viewerIsAdmin = role === "admin";
  const [data, setData] = useState<UsersResp | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [filtro, setFiltro] = useState<Filtro>("todos");
  const [invite, setInvite] = useState<{ email: string; role: string; admin_password: string } | null>(null);
  const [inviteBusy, setInviteBusy] = useState(false);
  const [inviteOut, setInviteOut] = useState<(InviteOut & { email: string; role: string; resent: boolean }) | null>(null);
  const [manageId, setManageId] = useState<number | null>(null);
  const [resetFor, setResetFor] = useState<U | null>(null);
  const [resetOut, setResetOut] = useState<(ResetOut & { user: U }) | null>(null);
  /* Cambios que involucran al rol admin (o desactivar a un admin): piden la contraseña de quien actúa. */
  const [toAlly, setToAlly] = useState<U | null>(null); // reunión 2, pedido 20
  const [pwAsk, setPwAsk] = useState<{ title: string; text: string; cta: string; run: (pw: string) => Promise<void> } | null>(null);
  /* rol que se tocó en «Qué ve cada rol»: se abre en el editor (n fuerza el cambio aunque sea el mismo) */
  const [focusRole, setFocusRole] = useState<{ code: string; n: number } | null>(null);
  const editorRef = useRef<HTMLDivElement>(null);
  /* ventas etapa 2: traspaso de cartera (también se ofrece solo al desactivar a un vendedor que todavía tiene cartera) */
  const [transferFor, setTransferFor] = useState<{ id: number; intro?: string } | null>(null);

  /* los roles que creó el administrador llegan con su nombre: se registran antes de dibujar la lista */
  const load = () => api<UsersResp>("/settings/users").then((d) => { registerRoles({ labels: d.role_labels }); setData(d); setLoadErr(null); }).catch((e) => setLoadErr(errMsg(e)));
  useEffect(() => { load(); }, []);

  const users = data?.users ?? [];
  const invs = data?.invitations ?? [];
  /* pedido 20: cualquier usuario interno se puede pasar al portal de aliados (no uno mismo; a un admin, solo otro admin) */
  const canConvert = (u: U) => canEdit && !u.is_self && u.role !== "soporte" && (u.role !== "admin" || viewerIsAdmin) && allows("aliados.usuarios");
  const manage = manageId != null ? users.find((u) => u.id === manageId) ?? null : null;
  const needle = q.trim().toLowerCase();
  const match = (...xs: (string | null | undefined)[]) => !needle || xs.some((x) => (x || "").toLowerCase().includes(needle));
  const shownUsers = users.filter((u) => (filtro === "activos" ? u.active : filtro === "inactivos" ? !u.active : filtro === "todos") && match(u.name, u.email, roleMeta(u.role).label, u.role));
  const shownInvs = invs.filter((i) => (filtro === "todos" || filtro === "invitaciones") && match(i.email, roleMeta(i.role).label, i.role));
  const activos = users.filter((u) => u.active).length;

  const patch = (uid: number, body: Record<string, unknown>) => api<{ ok: boolean; cartera?: { clientes: number; oportunidades: number; cotizaciones: number } }>(`/settings/users/${uid}`, { method: "PATCH", json: body });
  const copy = (text: string, msg = "Copiado") => { navigator.clipboard.writeText(text).then(() => toast(msg)).catch(() => toast("No se pudo copiar: seleccionalo y copialo a mano", "bad")); };

  /* roles creados con acceso a contabilidad, planillas, Ajustes o usuarios: asignarlos pide la contraseña, como el de administrador */
  const reauth = data?.reauth_roles ?? [];
  const changeRole = (u: U, next: string) => {
    if (next === u.role) return;
    const label = roleMeta(next).label;
    if (u.role === "admin" || next === "admin" || reauth.includes(next)) {
      setPwAsk({
        title: "Confirmá con tu contraseña",
        text: next === "admin" ? `${u.name} va a pasar a Administrador: verá y podrá cambiar todo, incluidos costos, usuarios y pasarelas.` : reauth.includes(next) ? `${u.name} pasa a ${label}, un rol con acceso a contabilidad, planillas, Ajustes o usuarios.` : `${u.name} deja de ser Administrador y pasa a ${label}.`,
        cta: "Cambiar rol",
        run: async (pw) => { await patch(u.id, { role: next, admin_password: pw }); toast(`Rol actualizado: ${label}`); load(); },
      });
      return;
    }
    patch(u.id, { role: next }).then(() => { toast(`Rol actualizado: ${label}`); load(); }).catch((e) => { toast(errMsg(e), "bad"); load(); });
  };

  const sendInvite = async () => {
    if (!invite) return;
    setInviteBusy(true);
    try {
      /* invitar como Administrador es crear uno: pide la contraseña de quien invita (igual que ascender a alguien) */
      const json: Record<string, unknown> = { email: invite.email.trim(), role: invite.role };
      if (invite.role === "admin" || reauth.includes(invite.role)) json.admin_password = invite.admin_password;
      const r = await api<InviteOut>("/settings/invitations", { method: "POST", json });
      setInviteOut({ ...r, email: invite.email.trim().toLowerCase(), role: invite.role, resent: false });
      setInvite(null);
      load();
    } catch (e) { toast(errMsg(e), "bad"); }
    finally { setInviteBusy(false); }
  };
  const doResend = async (i: Inv, adminPassword?: string) => {
    const r = await api<InviteOut>(`/settings/invitations/${i.id}/resend`, { method: "POST", json: adminPassword ? { admin_password: adminPassword } : undefined });
    setInviteOut({ ...r, email: i.email, role: i.role, resent: true });
    load();
  };
  const resend = async (i: Inv) => {
    if (i.role === "admin" || reauth.includes(i.role)) {
      /* reenviar devuelve un enlace vivo para entrar como administrador (o con un rol con contabilidad, planillas, Ajustes o usuarios): pide la contraseña */
      setPwAsk({ title: "Confirmá con tu contraseña", text: `Vas a reenviar la invitación de ${i.email} como ${roleMeta(i.role).label}. El enlace anterior deja de servir.`, cta: "Reenviar", run: (pw) => doResend(i, pw) });
      return;
    }
    try { await doResend(i); } catch (e) { toast(errMsg(e), "bad"); load(); }
  };
  const revoke = async (i: Inv) => {
    if (!confirm(`¿Revocar la invitación de ${i.email}? El enlace deja de servir.`)) return;
    try { await api(`/settings/invitations/${i.id}/revoke`, { method: "POST" }); toast("Invitación revocada"); load(); }
    catch (e) { toast(errMsg(e), "bad"); load(); }
  };

  if (loadErr && !data) return <Card title="Usuarios y roles"><Empty title="No se pudo cargar" hint={loadErr} /></Card>;
  if (!data) return <Card title="Usuarios y roles"><Loading /></Card>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
      <Card title="Usuarios y roles" flush extra={
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", minWidth: 0 }}>
          <div className="search" style={{ height: 32, maxWidth: 260 }}><Icon d={I.search} size={15} /><input placeholder="Buscar nombre, correo o rol" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar usuarios" /></div>
          {canEdit && <button className="btn btn--crimson btn--sm" onClick={() => setInvite({ email: "", role: data.roles.includes("ventas") ? "ventas" : data.roles[0], admin_password: "" })}><Icon d={I.plus} />Invitar</button>}
        </div>
      }>
        <div style={{ padding: "12px 18px 4px", display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="chips" role="group" aria-label="Filtrar usuarios">
            {([["todos", `Todos (${users.length})`], ["activos", `Activos (${activos})`], ["inactivos", `Inactivos (${users.length - activos})`], ["invitaciones", `Invitaciones (${invs.length})`]] as [Filtro, string][]).map(([k, l]) => (
              <button key={k} type="button" className={`chip ${filtro === k ? "is-on" : ""}`} onClick={() => setFiltro(k)} aria-pressed={filtro === k}>{l}</button>
            ))}
          </div>
          {canEdit && users.some((u) => u.ally_match) && (
            /* reunión 2, pedido 20: una persona de un aliado no debe tener acceso al panel de Crimson */
            <div style={{ padding: "10px 12px", borderRadius: 10, background: "var(--warn-soft)", color: "var(--warn)", fontSize: 13 }}>
              <b>{users.filter((u) => u.ally_match).length === 1 ? "Un usuario parece" : `${users.filter((u) => u.ally_match).length} usuarios parecen`} de una empresa aliada.</b> Las personas de un aliado entran solo al portal de aliados (ven lo que su empresa tiene en común con Crimson), no al panel. Revíselos y use «Pasar al portal de aliados».
            </div>
          )}
          {!data.mail_configured && (
            <div style={{ padding: "10px 12px", borderRadius: 10, background: "var(--warn-soft)", color: "var(--warn)", fontSize: 13 }}>
              <b>El envío de correos todavía no está activo.</b> Las invitaciones y los enlaces de contraseña no llegan solos: al crearlos te damos el enlace para mandarlo por WhatsApp.{" "}
              <button type="button" className="btn btn--ghost btn--sm" style={{ height: 26 }} onClick={() => setParams({ ...Object.fromEntries(params), tab: "correo" })}>Ver estado del correo</button>
            </div>
          )}
        </div>
        {filtro !== "invitaciones" && (
          shownUsers.length === 0 ? <Empty title="Sin usuarios" hint={needle ? "Nadie coincide con la búsqueda." : "No hay usuarios en este filtro."} /> : (
            <div style={{ overflowX: "auto" }}>
              <table className="table table--stack">
                <thead><tr><th>Usuario</th><th>Rol</th><th>Seguridad</th><th>Último acceso</th><th>Estado</th><th /></tr></thead>
                <tbody>
                  {shownUsers.map((u) => {
                    const lockedAdmin = u.role === "admin" && !viewerIsAdmin;
                    return (
                      <tr key={u.id}>
                        <td className="stack-full"><b>{u.name}</b>{u.is_self && <> <Chip tone="info">Vos</Chip></>}<div className="meta" style={{ textTransform: "none" }}>{u.email}</div>{u.notify_email && <div className="meta" style={{ textTransform: "none" }} title="Las notificaciones internas le llegan a este correo">avisos: {u.notify_email}</div>}{u.ally_match && <div style={{ marginTop: 4 }}><AllyMatchChip m={u.ally_match} /></div>}</td>
                        <td data-label="Rol">
                          <select className="select" style={{ height: 32, width: 165, maxWidth: "100%" }} value={u.role} disabled={u.is_self || !canEdit || lockedAdmin} title={u.is_self ? "Tu propio rol no se cambia desde aquí" : lockedAdmin ? "Solo un administrador puede cambiar a otro administrador" : undefined} onChange={(e) => changeRole(u, e.target.value)}>
                            {roleOptions(data.roles, u.role, viewerIsAdmin).map((r) => <option key={r} value={r}>{roleMeta(r).label}</option>)}
                          </select>
                        </td>
                        <td style={{ minWidth: 160 }} data-label="Seguridad">
                          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, maxWidth: 240 }}>
                            {u.totp && <Chip tone="ok" title="Verificación en dos pasos activa">2FA</Chip>}
                            {!u.totp && u.active && data.twofa_roles?.includes(u.role) && <Chip tone="warn" title="Su rol exige la verificación en dos pasos: al vencer la gracia, el panel solo le deja configurarla">Sin 2FA · obligatoria</Chip>}
                            {u.must_change_password && <Chip tone="warn">Debe cambiar la contraseña · vence {fecha(u.temp_password_expires_at)}</Chip>}
                            {u.locked_until && <Chip tone="bad" title="Por contraseñas equivocadas al aceptar una invitación. Se desbloquea solo o al restablecer la contraseña">Bloqueado hasta {hora(u.locked_until)}</Chip>}
                            {u.login_failures_until && <Chip tone="warn" title="Muchos intentos fallidos de entrar con esta cuenta. Desde un equipo donde ya entró no espera; desde otro, espera hasta un minuto entre intentos. Se normaliza sola o al restablecer la contraseña">Intentos fallidos · hasta {hora(u.login_failures_until)}</Chip>}
                            {u.reset_link_expires_at && <Chip tone="info">Enlace de contraseña enviado · vence {fecha(u.reset_link_expires_at)}</Chip>}
                            {!u.totp && !(u.active && data.twofa_roles?.includes(u.role)) && !u.must_change_password && !u.locked_until && !u.login_failures_until && !u.reset_link_expires_at && <span className="muted">—</span>}
                          </div>
                        </td>
                        <td className="muted" style={{ whiteSpace: "nowrap" }} data-label="Último acceso">{u.last_login ? fechaHora(u.last_login) : "nunca"}</td>
                        <td data-label="Estado">{u.active ? <Chip tone="ok">Activo</Chip> : <Chip tone="muted">Inactivo</Chip>}</td>
                        <td className="num stack-actions">{canConvert(u) && u.ally_match && <button className="btn btn--soft btn--sm" onClick={() => setToAlly(u)}>Pasar al portal de aliados</button>}{canEdit && <button className="btn btn--ghost btn--sm" onClick={() => setManageId(u.id)}>Gestionar</button>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        )}
        <div style={{ padding: "12px 18px", display: "flex", flexDirection: "column", gap: 8, fontSize: 12.5 }}>
          <p className="muted" style={{ margin: 0 }}><Icon d={I.shield} size={14} /> Las contraseñas no se pueden ver, ni siquiera el administrador: se guardan cifradas de una sola vía y no se pueden descifrar. Si alguien la olvidó, usá <b>Gestionar → Restablecer contraseña</b>.</p>
          <details>
            <summary style={{ cursor: "pointer", fontWeight: 600 }}>¿Qué puede hacer cada rol?</summary>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12, marginTop: 10 }}>
              {data.roles.map((r) => <RoleSummary key={r} code={r} />)}
            </div>
            <p className="muted" style={{ margin: "10px 0 0" }}>Cada rol ve solo sus secciones del menú y la API aplica los mismos permisos. Los usuarios de aliados se manejan en Administración → Aliados (desde la ficha de cada empresa aliada) y los de clientes desde Accesos de clientes.</p>
          </details>
          {data.support_members > 0 && (
            /* la pestaña Acceso de soporte solo existe con settings.configurar: sin ese permiso, solo el aviso */
            <p className="muted" style={{ margin: 0 }}>Hay {data.support_members} {data.support_members === 1 ? "acceso de soporte técnico activo" : "accesos de soporte técnico activos"}: se gestionan en {canEdit ? <button type="button" className="btn btn--ghost btn--sm" style={{ height: 24 }} onClick={() => setParams({ tab: "soporte" })}>Acceso de soporte</button> : <b>Acceso de soporte</b>}.</p>
          )}
        </div>
      </Card>

      {invs.length > 0 && filtro !== "activos" && filtro !== "inactivos" && (
        <Card title="Invitaciones" flush extra={<span className="meta">pendientes y vencidas (30 días)</span>}>
          {shownInvs.length === 0 ? <Empty title="Sin invitaciones" hint="Nadie coincide con la búsqueda." /> : (
            <div style={{ overflowX: "auto" }}>
              <table className="table table--stack">
                <thead><tr><th>Correo</th><th>Rol</th><th>Estado</th><th>Envío</th><th /></tr></thead>
                <tbody>
                  {shownInvs.map((i) => (
                    <tr key={i.id}>
                      <td className="stack-full"><b>{i.email}</b>{i.invited_by && <div className="meta" style={{ textTransform: "none" }}>invitó {i.invited_by}</div>}</td>
                      <td className="muted" data-label="Rol">{roleMeta(i.role).label}</td>
                      <td data-label="Estado">
                        {i.status === "pendiente" ? <span>Pendiente · vence {fecha(i.expires_at)}</span> : <span style={{ color: "var(--bad)" }}>Vencida el {fecha(i.expires_at)}</span>}
                        {i.sent_count > 1 && <div className="meta" style={{ textTransform: "none" }}>enviada {i.sent_count} veces</div>}
                      </td>
                      <td style={{ fontSize: 12.5 }} data-label="Envío">
                        {i.email_status === "enviado" ? <span style={{ color: "var(--ok)" }}>Enviado</span>
                          : i.email_status === "simulado" ? <span className="muted">No salió: el correo no está activo</span>
                          : i.email_status === "error" ? <span style={{ color: "var(--bad)" }}>No salió: {i.email_error}</span>
                          : <span className="muted">—</span>}
                      </td>
                      <td className="num stack-actions" style={{ whiteSpace: "nowrap" }}>
                        {canEdit && (() => {
                          /* una invitación de Administrador solo la toca otro administrador (la API responde 403) */
                          const bloqueada = i.role === "admin" && !viewerIsAdmin;
                          const t = bloqueada ? "Solo un administrador puede reenviar o revocar la invitación de otro administrador" : undefined;
                          return <><button className="btn btn--ghost btn--sm" onClick={() => resend(i)} disabled={bloqueada} title={t}>Reenviar</button><button className="btn btn--danger btn--sm" onClick={() => revoke(i)} disabled={bloqueada} title={t}>Revocar</button></>;
                        })()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {canEdit && <CostRoles key={data.cost_roles.join(",")} current={data.cost_roles} choices={data.cost_role_choices} onSaved={load} />}
      {canEdit && data.twofa_roles && data.twofa_role_choices && (
        <TwoFactorPolicy key={`${data.twofa_roles.join(",")}|${data.twofa_grace_days}`} current={data.twofa_roles} grace={data.twofa_grace_days ?? 7} choices={data.twofa_role_choices} onSaved={load} askPassword={setPwAsk} />
      )}
      {me?.role === "admin" && <div ref={editorRef}><RolesEditor onChanged={load} focus={focusRole} /></div>}
      {me?.role === "admin" && (
        <Card title="Qué ve cada rol" extra={<span className="meta">se edita en «Roles y permisos»</span>}>
          {/* reunión 2, pedido 29: la matriz se edita desde aquí. Tocar el nombre de un rol lo abre en «Roles y permisos». */}
          <p className="muted" style={{ fontSize: 12.5, marginTop: 0 }}>Operación, Técnico, Catálogo y Administración son grupos del menú, no roles. Para cambiar lo que ve un rol, toque su nombre: se abre en «Roles y permisos», arriba. El Administrador siempre ve todo y no se recorta. Para ver el panel exactamente como lo ve una persona, use «Ver como» arriba a la derecha.</p>
          <RoleMatrix onPickRole={(code) => { setFocusRole({ code, n: Date.now() }); editorRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }} />
        </Card>
      )}

      {toAlly && <AllyConvert user={toAlly} onClose={() => setToAlly(null)} onDone={() => { setToAlly(null); load(); }} />}
      {invite && (
        <Modal title="Invitar usuario" onClose={() => setInvite(null)} foot={<><button className="btn btn--ghost" onClick={() => setInvite(null)}>Cancelar</button><button className="btn btn--crimson" onClick={sendInvite} disabled={inviteBusy || !invite.email.includes("@") || ((invite.role === "admin" || reauth.includes(invite.role)) && !invite.admin_password)}>{inviteBusy ? "Enviando…" : "Enviar invitación"}</button></>}>
          <Field label="Correo"><input className="input" type="email" autoFocus value={invite.email} onChange={(e) => setInvite({ ...invite, email: e.target.value })} placeholder="nombre@empresa.com" /></Field>
          <Field label="Rol"><select className="select" value={invite.role} onChange={(e) => setInvite({ ...invite, role: e.target.value })}>{roleOptions(data.roles, invite.role, viewerIsAdmin).map((r) => <option key={r} value={r}>{roleMeta(r).label}</option>)}</select></Field>
          <RoleSummary code={invite.role} />
          {(invite.role === "admin" || reauth.includes(invite.role)) && (
            <Field label="Confirmá con tu contraseña" hint={invite.role === "admin" ? "Invitar a un Administrador pide tu contraseña: va a ver y poder cambiar todo, incluidos costos, usuarios y pasarelas." : "Este rol tiene acceso a contabilidad, planillas, Ajustes o usuarios: invitar con él pide tu contraseña."}>
              <input className="input" type="password" autoComplete="current-password" value={invite.admin_password} onChange={(e) => setInvite({ ...invite, admin_password: e.target.value })} />
            </Field>
          )}
          <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Recibe un enlace para crear su contraseña; vence en 7 días y sirve una sola vez.</p>
        </Modal>
      )}
      {inviteOut && (
        <Modal title={inviteOut.resent ? "Invitación reenviada" : "Invitación creada"} onClose={() => setInviteOut(null)} foot={<button className="btn btn--ghost" onClick={() => setInviteOut(null)}>Cerrar</button>}>
          {inviteOut.resent && <p style={{ fontSize: 13, margin: 0, fontWeight: 600 }}>El enlace anterior dejó de servir.</p>}
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>
            {inviteOut.emailed
              ? <>Le enviamos un correo a <b>{inviteOut.email}</b>. También puede mandarle el enlace por WhatsApp; vence el {fecha(inviteOut.expires_at)}.</>
              : <>El correo no salió ({inviteOut.email_error || "el envío de correos todavía no está activo"}). Mandale este enlace a <b>{inviteOut.email}</b> por WhatsApp; vence el {fecha(inviteOut.expires_at)} y sirve una sola vez.</>}
          </p>
          <div className="search" style={{ maxWidth: "none" }}><Icon d={I.link} size={16} /><input readOnly value={inviteOut.link} onFocus={(e) => e.currentTarget.select()} aria-label="Enlace de invitación" /></div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn btn--soft" onClick={() => copy(inviteOut.link, "Enlace copiado")}><Icon d={I.copy} />Copiar</button>
            <button className="btn btn--ghost" onClick={() => copy(`Hola, te invité a ${me?.tenant.name || "la empresa"} en la plataforma de Crimson como ${roleMeta(inviteOut.role).label}. Entrá a este enlace para crear tu contraseña (vence el ${fecha(inviteOut.expires_at)}): ${inviteOut.link}`, "Mensaje copiado")}><Icon d={I.whatsapp} />Copiar mensaje para WhatsApp</button>
          </div>
        </Modal>
      )}

      {manage && (
        <ManageModal
          u={manage}
          roles={data.roles}
          reauthRoles={reauth}
          viewerIsAdmin={viewerIsAdmin}
          onClose={() => setManageId(null)}
          onChanged={load}
          onReset={() => setResetFor(manage)}
          onDeleted={() => { setManageId(null); load(); }}
          askPassword={setPwAsk}
          patch={patch}
          onToAlly={canConvert(manage) ? () => { setManageId(null); setToAlly(manage); } : undefined}
          maxDiscount={data.max_discount_pct}
          onTransfer={(intro) => setTransferFor({ id: manage.id, intro })}
        />
      )}
      {transferFor && <PortfolioTransfer userId={transferFor.id} intro={transferFor.intro} onClose={() => setTransferFor(null)} onDone={load} />}
      {resetFor && (
        <ResetModal u={resetFor} mailConfigured={data.mail_configured} onClose={() => setResetFor(null)} onDone={(r) => { setResetFor(null); setResetOut({ ...r, user: resetFor }); load(); }} />
      )}
      {resetOut && <ResetResult r={resetOut} mailConfigured={data.mail_configured} copy={copy} onClose={() => setResetOut(null)} />}
      {pwAsk && <PasswordAsk {...pwAsk} onClose={() => setPwAsk(null)} />}
    </div>
  );
}

/* Resumen de un rol (etiqueta, qué puede y qué no) desde roles.ts: queda alineado con la matriz de roles. */
function RoleSummary({ code }: { code: string }) {
  const m = roleMeta(code);
  return (
    <div style={{ padding: 12, borderRadius: 10, background: "var(--bg-2)", fontSize: 12.5, display: "flex", flexDirection: "column", gap: 6 }}>
      <b style={{ fontSize: 13.5 }}><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 99, background: m.tone, marginRight: 6 }} />{m.label}</b>
      {m.summary && <span className="muted">{m.summary}</span>}
      {m.can.length > 0 && <ul style={{ margin: 0, paddingLeft: 18 }}>{m.can.map((c) => <li key={c}>{c}</li>)}</ul>}
      {m.cannot.length > 0 && <ul style={{ margin: 0, paddingLeft: 18, color: "var(--text-2)" }}>{m.cannot.map((c) => <li key={c}>No: {c.charAt(0).toLowerCase() + c.slice(1)}</li>)}</ul>}
    </div>
  );
}

type Ask = { title: string; text: string; cta: string; run: (pw: string) => Promise<void> };

/* Confirmar con la contraseña de quien actúa. Si es incorrecta la API responde 403 (no 401): la sesión sigue. */
function PasswordAsk({ title, text, cta, run, onClose }: Ask & { onClose: () => void }) {
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const go = async () => {
    setBusy(true); setErr(null);
    try { await run(pw); onClose(); } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };
  return (
    <Modal title={title} onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" onClick={go} disabled={busy || !pw}>{busy ? "Guardando…" : cta}</button></>}>
      <p style={{ fontSize: 13.5, marginTop: 0 }}>{text}</p>
      <Field label="Confirmá con tu contraseña"><input className="input" type="password" autoComplete="current-password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && pw && !busy) go(); }} /></Field>
      {err && <p style={{ color: "var(--bad)", fontSize: 13, margin: 0 }} role="alert">{err}</p>}
    </Modal>
  );
}

function ManageModal({ u, roles, reauthRoles, viewerIsAdmin, onClose, onChanged, onReset, onDeleted, askPassword, patch, onToAlly, maxDiscount, onTransfer }: {
  u: U; roles: string[]; reauthRoles: string[]; viewerIsAdmin: boolean; onClose: () => void; onChanged: () => void; onReset: () => void; onDeleted: () => void;
  askPassword: (a: Ask) => void; patch: (uid: number, body: Record<string, unknown>) => Promise<{ ok: boolean; cartera?: { clientes: number; oportunidades: number; cotizaciones: number } }>; onToAlly?: () => void;
  maxDiscount?: string | number; onTransfer: (intro?: string) => void;
}) {
  const { toast } = useSession();
  const blank = { full_name: u.name, email: u.email, notify_email: u.notify_email || "", role: u.role, reason: "", admin_password: "" };
  const [f, setF] = useState(blank);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setF({ full_name: u.name, email: u.email, notify_email: u.notify_email || "", role: u.role, reason: "", admin_password: "" }); }, [u.id, u.name, u.email, u.role, u.notify_email]);
  const lockedAdmin = u.role === "admin" && !viewerIsAdmin;
  const nameChange = f.full_name.trim() !== u.name;
  const emailChange = f.email.trim().toLowerCase() !== u.email.toLowerCase();
  const notifyChange = f.notify_email.trim().toLowerCase() !== (u.notify_email || "").toLowerCase();
  const roleChange = f.role !== u.role;
  /* desviar los avisos a otro buzón pide tu contraseña (quitarlo no): nadie se lleva las notificaciones callado */
  const needPw = emailChange || (notifyChange && !!f.notify_email.trim()) || (roleChange && (u.role === "admin" || f.role === "admin" || reauthRoles.includes(f.role)));
  const dirty = nameChange || emailChange || notifyChange || roleChange;

  const save = async () => {
    const body: Record<string, unknown> = {};
    if (nameChange) body.full_name = f.full_name.trim();
    if (emailChange) body.email = f.email.trim();
    if (notifyChange) body.notify_email = f.notify_email.trim();
    if (roleChange) body.role = f.role;
    if (f.reason.trim()) body.reason = f.reason.trim();
    if (needPw) body.admin_password = f.admin_password;
    setBusy(true); setErr(null);
    try {
      await patch(u.id, body);
      toast(roleChange && !nameChange && !emailChange ? `Rol actualizado: ${roleMeta(f.role).label}` : "Cambios guardados");
      setF((x) => ({ ...x, admin_password: "", reason: "" }));
      onChanged();
    } catch (e) { setErr(errMsg(e)); }
    finally { setBusy(false); }
  };

  const closeSessions = async () => {
    if (!confirm("Se cerrará su sesión en todos los equipos y celulares. Tendrá que volver a entrar.")) return;
    try { const r = await api<{ revoked: number }>(`/settings/users/${u.id}/sessions/revoke`, { method: "POST" }); toast(r.revoked ? `Sesiones cerradas (${r.revoked})` : "Sesiones cerradas"); onChanged(); }
    catch (e) { toast(errMsg(e), "bad"); }
  };
  const toggleActive = async () => {
    const next = !u.active;
    const texto = next ? `${u.name} vuelve a entrar a la empresa con su contraseña de siempre.` : "Ya no podrá entrar a la empresa. Lo que hizo (cotizaciones, levantamientos, bitácora) se conserva y puede reactivarlo cuando quieras. No se borran usuarios.";
    if (u.role === "admin") {
      askPassword({ title: next ? "Activar administrador" : "Desactivar administrador", text: texto, cta: next ? "Activar" : "Desactivar", run: async (pw) => { await patch(u.id, { active: next, admin_password: pw }); toast(next ? "Usuario activado" : "Usuario desactivado"); onChanged(); } });
      return;
    }
    if (!confirm(texto)) return;
    try {
      const r = await patch(u.id, { active: next });
      toast(next ? "Usuario activado" : "Usuario desactivado"); onChanged();
      /* desactivar a un vendedor que todavía tiene cartera: se ofrece el traspaso de un clic */
      if (!next && r.cartera) onTransfer(`${u.name} quedó desactivado y todavía tiene cartera. ¿A quién se la pasa?`);
    }
    catch (e) { toast(errMsg(e), "bad"); }
  };
  /* ventas etapa 2: tope de descuento propio (vacío = el general de la empresa) */
  const vendeRol = u.role === "ventas" || u.role === "jefe_ventas" || (u.clientes_cartera ?? 0) > 0;
  const topeActual = u.discount_limit_pct == null ? "" : String(Number(u.discount_limit_pct));
  const [tope, setTope] = useState(topeActual);
  useEffect(() => { setTope(topeActual); }, [topeActual]);
  const saveTope = async () => {
    const v = tope.trim();
    if (v !== "" && (!(Number(v) >= 0) || Number(v) > 100)) { toast("El tope debe estar entre 0 y 100 %", "bad"); return; }
    try { await patch(u.id, { discount_limit_pct: v === "" ? "" : Number(v) }); toast(v === "" ? "Usa el tope general de la empresa" : `Tope de descuento: ${Number(v)} %`); onChanged(); }
    catch (e) { toast(errMsg(e), "bad"); }
  };

  const m = roleMeta(f.role);
  const accesoBloqueado = u.is_self || lockedAdmin;
  return (
    <Modal title={`Gestionar · ${u.name}`} wide onClose={onClose} foot={<button className="btn btn--ghost" onClick={onClose}>Cerrar</button>}>
      {lockedAdmin && <p style={{ color: "var(--warn)", fontSize: 13, margin: 0 }}>Solo un administrador puede cambiar a otro administrador.</p>}
      <div className="grid-2" style={{ alignItems: "start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="meta">Datos</div>
          <Field label="Nombre"><input className="input" value={f.full_name} disabled={u.shared || lockedAdmin} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
          <Field label="Correo" hint={u.shared ? undefined : "Con este correo entra a la plataforma. Le avisamos al correo viejo y al nuevo."}><input className="input" type="email" value={f.email} disabled={u.shared || lockedAdmin} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          <Field label="Correo de avisos (opcional)" hint="Si lo llena, las notificaciones internas (pedidos, cotizaciones, tickets…) le llegan aquí en vez de a su correo de entrada. Lo de su cuenta (contraseña y seguridad) sigue yendo al de entrada.">
            <input className="input" type="email" placeholder={u.email} value={f.notify_email} disabled={lockedAdmin} onChange={(e) => setF({ ...f, notify_email: e.target.value })} />
          </Field>
          {u.shared && <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Este usuario también entra a otra empresa: su nombre, correo y contraseña no se cambian desde aquí. El rol y el estado sí (son de esta empresa).</p>}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="meta">Rol</div>
          <Field label="Rol en la empresa"><select className="select" value={f.role} disabled={u.is_self || lockedAdmin} onChange={(e) => setF({ ...f, role: e.target.value })}>{roleOptions(roles, u.role, viewerIsAdmin).map((r) => <option key={r} value={r}>{roleMeta(r).label}</option>)}</select></Field>
          {u.is_self && <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Tu propio rol no se cambia desde aquí.</p>}
          {m.summary && <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>{m.summary}</p>}
          {(m.can.length > 0 || m.cannot.length > 0) && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontSize: 12 }}>
              <div><b style={{ color: "var(--ok)" }}>Puede</b><ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>{m.can.map((c) => <li key={c}>{c}</li>)}</ul></div>
              <div><b style={{ color: "var(--bad)" }}>No puede</b><ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>{m.cannot.length ? m.cannot.map((c) => <li key={c}>{c}</li>) : <li className="muted">—</li>}</ul></div>
            </div>
          )}
          <Field label="Motivo (queda en la bitácora)"><input className="input" maxLength={500} value={f.reason} disabled={lockedAdmin} onChange={(e) => setF({ ...f, reason: e.target.value })} placeholder="Opcional" /></Field>
        </div>
      </div>
      {dirty && (
        <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
          {needPw && <div style={{ flex: "1 1 220px" }}><Field label="Confirmá con tu contraseña" hint={emailChange ? "Cambiar el correo con el que entra pide tu contraseña." : notifyChange && f.notify_email.trim() ? "Cambiar a dónde le llegan los avisos pide tu contraseña." : "Todo cambio que involucre al rol Administrador pide tu contraseña."}><input className="input" type="password" autoComplete="current-password" value={f.admin_password} onChange={(e) => setF({ ...f, admin_password: e.target.value })} /></Field></div>}
          <button className="btn btn--crimson" onClick={save} disabled={busy || (needPw && !f.admin_password) || f.full_name.trim().length < 2}>{busy ? "Guardando…" : "Guardar cambios"}</button>
          <button className="btn btn--ghost" onClick={() => setF(blank)} disabled={busy}>Deshacer</button>
        </div>
      )}
      {err && <p style={{ color: "var(--bad)", fontSize: 13, margin: 0 }} role="alert">{err}</p>}

      <div style={{ borderTop: "1px solid var(--hair)", paddingTop: 12, display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="meta">Contraseña y acceso</div>
        <p className="muted" style={{ fontSize: 13, margin: 0 }}>Nadie puede ver la contraseña de otra persona; se restablece.{u.last_login ? ` Último acceso: ${fechaHora(u.last_login)}.` : " Todavía no ha entrado."}{u.password_changed_at ? ` Contraseña cambiada el ${fecha(u.password_changed_at)}.` : ""}</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn btn--soft btn--sm" onClick={onReset} disabled={accesoBloqueado || u.shared || !u.active} title={!u.active ? "El usuario está desactivado: activalo primero" : u.shared ? "Su contraseña se gestiona en la otra empresa" : undefined}><Icon d={I.shield} size={14} />Restablecer contraseña</button>
          <button className="btn btn--ghost btn--sm" onClick={closeSessions} disabled={accesoBloqueado}><Icon d={I.logout} size={14} />Cerrar todas sus sesiones</button>
          <button className={`btn btn--sm ${u.active ? "btn--danger" : "btn--crimson"}`} onClick={toggleActive} disabled={accesoBloqueado}>{u.active ? "Desactivar" : "Activar"}</button>
        </div>
        {u.is_self && <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Para tu cuenta usá Mi cuenta: cambiar contraseña, verificación en dos pasos y cerrar en todos los dispositivos.</p>}
      </div>
      {vendeRol && (
        <div style={{ borderTop: "1px solid var(--hair)", paddingTop: 12, display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="meta">Ventas</div>
          <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
            <div style={{ flex: "1 1 220px" }}>
              <Field label="Tope de descuento propio (%)" hint={`Vacío = el tope general de la empresa${maxDiscount != null ? ` (${Number(maxDiscount)} %)` : ""}. Lo que pase de aquí pide aprobación.`}>
                <input className="input" type="number" min={0} max={100} step="0.5" inputMode="decimal" value={tope} onChange={(e) => setTope(e.target.value)} placeholder={maxDiscount != null ? String(Number(maxDiscount)) : ""} />
              </Field>
            </div>
            <button className="btn btn--soft btn--sm" onClick={saveTope} disabled={tope.trim() === topeActual}>Guardar tope</button>
          </div>
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>{u.clientes_cartera ? `Tiene ${u.clientes_cartera} cliente(s) en su cartera.` : "No tiene clientes en su cartera."}</p>
          <div><button className="btn btn--ghost btn--sm" onClick={() => onTransfer()}>Traspasar su cartera a otro vendedor</button></div>
        </div>
      )}
      {onToAlly && (
        /* reunión 2, pedido 20: también para quien el sistema no detecta (p. ej. un técnico del aliado con correo gratuito) */
        <div style={{ borderTop: "1px solid var(--hair)", paddingTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="meta">¿Trabaja para una empresa aliada?</div>
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>Las personas de un aliado no entran al panel de Crimson: entran al portal de aliados de su empresa y ven solo lo que tienen en común.{u.ally_match ? ` Parece del aliado ${u.ally_match.company}.` : ""}</p>
          <div><button className="btn btn--soft btn--sm" onClick={onToAlly}>Pasar al portal de aliados</button></div>
        </div>
      )}
      {viewerIsAdmin && !u.is_self && <DeleteUser u={u} onDeleted={onDeleted} />}
    </Modal>
  );
}

function ResetModal({ u, mailConfigured, onClose, onDone }: { u: U; mailConfigured: boolean; onClose: () => void; onDone: (r: ResetOut) => void }) {
  const [mode, setMode] = useState<"enlace" | "temporal">(mailConfigured ? "enlace" : "temporal");
  const [reset2fa, setReset2fa] = useState(false);
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const go = async () => {
    setBusy(true); setErr(null);
    try { onDone(await api<ResetOut>(`/settings/users/${u.id}/reset-password`, { method: "POST", json: { mode, reset_2fa: reset2fa, admin_password: pw } })); }
    catch (e) { setErr(errMsg(e)); }
    finally { setBusy(false); }
  };
  const opt = (k: "enlace" | "temporal", title: string, text: string, extra?: string) => (
    <label className="pay__method" style={{ cursor: "pointer", borderColor: mode === k ? "var(--crimson)" : undefined, boxShadow: mode === k ? "0 0 0 1px var(--crimson)" : undefined }}>
      <span style={{ display: "flex", gap: 8, alignItems: "center" }}><input type="radio" name="modo-reset" checked={mode === k} onChange={() => setMode(k)} /><b style={{ color: "var(--text)" }}>{title}</b></span>
      <span>{text}</span>
      {extra && <span style={{ color: "var(--warn)" }}>{extra}</span>}
    </label>
  );
  return (
    <Modal title={`Restablecer contraseña · ${u.name}`} onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" onClick={go} disabled={busy || !pw}>{busy ? "Restableciendo…" : "Restablecer"}</button></>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {opt("enlace", "Enviarle un enlace por correo", "Él mismo crea su contraseña. El enlace vence en 24 horas y sirve una sola vez. Su contraseña actual sigue sirviendo hasta que use el enlace.", mailConfigured ? undefined : "El correo no está activo: te damos el enlace para mandarlo por WhatsApp.")}
        {opt("temporal", "Generar una contraseña temporal", "Se muestra una sola vez para que se la pases. Vence en 48 horas; al entrar tendrá que crear una propia. Se cierran todas sus sesiones.")}
      </div>
      {u.totp && (
        <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 13 }}>
          <input type="checkbox" checked={reset2fa} onChange={(e) => setReset2fa(e.target.checked)} style={{ marginTop: 3 }} />
          <span><b>También quitar la verificación en dos pasos (perdió el teléfono)</b><br /><span className="muted">{mode === "enlace" ? "Se quita cuando use el enlace (mientras tanto su contraseña actual sigue pidiendo el código). " : ""}Si no la marcás, al entrar le va a pedir también el código de su app.</span></span>
        </label>
      )}
      <Field label="Confirmá con tu contraseña"><input className="input" type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && pw && !busy) go(); }} /></Field>
      {err && <p style={{ color: "var(--bad)", fontSize: 13, margin: 0 }} role="alert">{err}</p>}
    </Modal>
  );
}

/* El secreto se muestra aquí una sola vez; al cerrar se descarta el estado y no se puede volver a ver. */
function ResetResult({ r, mailConfigured, copy, onClose }: { r: ResetOut & { user: U }; mailConfigured: boolean; copy: (t: string, msg?: string) => void; onClose: () => void }) {
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
        <p style={{ color: "var(--bad)", fontSize: 13, fontWeight: 600, margin: 0 }}>Al cerrar esta ventana no se puede volver a ver.</p>
      </Modal>
    );
  }
  if (r.emailed || !r.link) {
    return (
      <Modal title="Enlace enviado" onClose={onClose} foot={<button className="btn btn--crimson" onClick={onClose}>Listo</button>}>
        <p style={{ fontSize: 13.5, margin: 0 }}>Le enviamos un correo a <b>{r.user.email}</b> con un enlace para crear su contraseña (vence el {fecha(r.expires_at)}).</p>
      </Modal>
    );
  }
  const link = r.link;
  const motivo = r.email_error || (mailConfigured ? "no se pudo enviar" : "el envío de correos todavía no está activo");
  const msg = `Hola ${r.user.name}, este es tu enlace para crear tu nueva contraseña de Crimson: ${link}\nVence el ${fecha(r.expires_at)} y sirve una sola vez.`;
  return (
    <Modal title="Enlace para crear su contraseña" onClose={onClose} foot={<button className="btn btn--crimson" onClick={onClose}>Listo, ya lo copié</button>}>
      <p style={{ fontSize: 13.5, margin: 0 }}>El correo no salió ({motivo}). Mandale este enlace a <b>{r.user.name}</b> por WhatsApp; vence el {fecha(r.expires_at)} y sirve una sola vez.</p>
      <div className="search" style={{ maxWidth: "none" }}><Icon d={I.link} size={16} /><input readOnly value={link} onFocus={(e) => e.currentTarget.select()} aria-label="Enlace para crear la contraseña" /></div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn btn--soft" onClick={() => copy(link, "Enlace copiado")}><Icon d={I.copy} />Copiar</button>
        <button className="btn btn--ghost" onClick={() => copy(msg, "Mensaje copiado")}><Icon d={I.whatsapp} />Copiar mensaje para WhatsApp</button>
      </div>
      <p style={{ color: "var(--bad)", fontSize: 13, fontWeight: 600, margin: 0 }}>Al cerrar esta ventana no se puede volver a ver.</p>
    </Modal>
  );
}

/* Verificación en dos pasos obligatoria (plan 1.10). Por defecto administración y contabilidad, con 7 días de gracia
   desde que la persona entra por primera vez con ese rol sin 2FA. Vencida la gracia, el panel solo le deja activarla.
   El cambio queda en la bitácora. Aflojarla (quitar un rol o dar más días) pide la contraseña de quien actúa y avisa por
   correo a los administradores. El acceso de soporte temporal la exige siempre, sin gracia (no se configura aquí). */
function TwoFactorPolicy({ current, grace, choices, onSaved, askPassword }: { current: string[]; grace: number; choices: string[]; onSaved: () => void; askPassword: (a: Ask) => void }) {
  const { toast } = useSession();
  const [sel, setSel] = useState<string[]>(current);
  const [dias, setDias] = useState(grace);
  const [busy, setBusy] = useState(false);
  const changed = dias !== grace || sel.length !== current.length || sel.some((r) => !current.includes(r));
  const quita = current.filter((r) => !sel.includes(r));
  const listo = () => toast(sel.length ? `Listo: la verificación en dos pasos es obligatoria para ${sel.map((r) => roleMeta(r).label).join(", ")}` : "Listo: la verificación en dos pasos queda opcional para todos");
  const save = async () => {
    if (quita.length || dias > grace) {
      const texto = [
        quita.length ? `${quita.map((r) => roleMeta(r).label).join(", ")} deja de tener la verificación en dos pasos obligatoria: esas cuentas quedan protegidas solo con la contraseña.` : "",
        dias > grace ? `Los días de gracia pasan de ${grace} a ${dias}.` : "",
        "Los administradores reciben un correo con el cambio.",
      ].filter(Boolean).join(" ");
      askPassword({ title: "Aflojar la verificación en dos pasos", text: texto, cta: "Guardar", run: async (pw) => { await api("/settings", { method: "PUT", json: { twofa_roles: sel, twofa_grace_days: dias, admin_password: pw } }); listo(); onSaved(); } });
      return;
    }
    setBusy(true);
    try {
      await api("/settings", { method: "PUT", json: { twofa_roles: sel, twofa_grace_days: dias } });
      listo();
      onSaved();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  return (
    <Card title="Verificación en dos pasos obligatoria">
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Los roles marcados tienen que activar la verificación en dos pasos (código del teléfono además de la contraseña). Cada persona tiene unos días de gracia desde que entra por primera vez; después el panel solo le deja configurarla.</p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "8px 18px", marginBottom: 12 }}>
        {choices.map((r) => (
          <label key={r} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13.5 }}>
            <input type="checkbox" checked={sel.includes(r)} onChange={(e) => setSel(e.target.checked ? [...sel, r] : sel.filter((x) => x !== r))} />
            {roleMeta(r).label}
          </label>
        ))}
      </div>
      <p className="muted" style={{ fontSize: 12.5, margin: "0 0 10px" }}>El acceso de soporte temporal siempre la exige, sin días de gracia.</p>
      <Field label="Días de gracia" hint="De 0 a 30. Recomendado: 7.">
        <input className="input input--mono" type="number" min={0} max={30} value={dias} onChange={(e) => setDias(Math.max(0, Math.min(30, Number(e.target.value) || 0)))} style={{ width: 100 }} />
      </Field>
      <button className="btn btn--crimson btn--sm" style={{ marginTop: 12 }} onClick={save} disabled={busy || !changed}>{busy ? "Guardando…" : "Guardar"}</button>
    </Card>
  );
}

/* "Quién ve costos" (13/10, 08:07: "eso solo lo puede ver el administrador"). Por defecto nadie más; el admin puede
   habilitar a supervisor, contador, bodega o solo lectura sin desplegar. Ventas, caja, técnicos y aliados: nunca. */
function CostRoles({ current, choices, onSaved }: { current: string[]; choices: string[]; onSaved: () => void }) {
  const { toast, reloadMatrix } = useSession();
  const [sel, setSel] = useState<string[]>(current);
  const [busy, setBusy] = useState(false);
  const changed = sel.length !== current.length || sel.some((r) => !current.includes(r));
  const save = async () => {
    setBusy(true);
    try {
      await api("/settings", { method: "PUT", json: { cost_roles: sel } });
      toast(sel.length ? `Listo: además del administrador, ven costos: ${sel.map((r) => roleMeta(r).label).join(", ")}` : "Listo: los costos los ve solo el administrador");
      onSaved(); reloadMatrix();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  return (
    <Card title="Quién ve costos y márgenes">
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>El costo del proveedor, el margen y la utilidad los ve solo el administrador. Si querés que otro rol también los vea, marcalo. Gastos, facturas de proveedor y planillas no cuentan aquí: son de Contabilidad.</p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "8px 18px", marginBottom: 12 }}>
        {choices.map((r) => (
          <label key={r} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13.5 }}>
            <input type="checkbox" checked={sel.includes(r)} onChange={(e) => setSel(e.target.checked ? [...sel, r] : sel.filter((x) => x !== r))} />
            {roleMeta(r).label}
          </label>
        ))}
      </div>
      <p className="muted" style={{ fontSize: 12, margin: "0 0 12px" }}>Aplica en la siguiente acción de cada persona (al recargar la página); no hace falta que cierre sesión.</p>
      <button className="btn btn--crimson btn--sm" onClick={save} disabled={busy || !changed}>{busy ? "Guardando…" : "Guardar"}</button>
    </Card>
  );
}

/* Eliminar un usuario (reunión 21/10, pedido 21; reunión 2, pedido 28). Solo el administrador, con su contraseña y
   escribiendo ELIMINAR. Sin actividad se borra de verdad; con actividad se anonimiza y desactiva: sus documentos y la
   bitácora quedan a nombre de «Usuario eliminado #id» y sale de la lista. Nunca a sí mismo ni al último administrador. */
type Activity = { can_delete: boolean; reason: string | null; activity: { what: string; count: number }[]; mode?: "borrar" | "anonimizar" };
function DeleteUser({ u, onDeleted }: { u: U; onDeleted: () => void }) {
  const { toast } = useSession();
  const [act, setAct] = useState<Activity | null>(null);
  const [pw, setPw] = useState("");
  const [confirmTxt, setConfirmTxt] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const check = async () => {
    setErr(null);
    try { setAct(await api<Activity>(`/settings/users/${u.id}/activity`)); }
    catch (e) { toast(errMsg(e), "bad"); }
  };
  const anonimizar = (act?.mode ?? (act?.activity.length ? "anonimizar" : "borrar")) === "anonimizar";
  const listo = !!pw && confirmTxt.trim().toUpperCase() === "ELIMINAR";
  const go = async () => {
    if (!listo) return;
    setBusy(true); setErr(null);
    try {
      const r = await api<{ mode: string }>(`/settings/users/${u.id}/delete`, { method: "POST", json: { admin_password: pw } });
      toast(r.mode === "anonimizada" ? `${u.name} fue eliminado: lo que hizo queda a nombre de «Usuario eliminado»` : `${u.name} fue eliminado`);
      onDeleted();
    }
    catch (e) { setErr(errMsg(e)); }
    finally { setBusy(false); }
  };
  const cancelar = () => { setAct(null); setPw(""); setConfirmTxt(""); setErr(null); };
  return (
    <div style={{ borderTop: "1px solid var(--hair)", paddingTop: 12, display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="meta">Eliminar</div>
      {!act && (
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button className="btn btn--ghost btn--sm" onClick={check}>Eliminar usuario…</button>
          <span className="muted" style={{ fontSize: 12.5 }}>Sin actividad se borra por completo (p. ej. una cuenta de prueba). Con actividad se anonimiza: deja de entrar, sale de la lista y lo que hizo se conserva sin su nombre ni su correo.</span>
        </div>
      )}
      {act && !act.can_delete && (
        <div style={{ fontSize: 13 }}>
          <p style={{ margin: 0, color: "var(--warn)" }}><b>No se puede eliminar:</b> {act.reason}.{u.active ? " Use «Desactivar»: deja de entrar y lo que hizo se conserva." : ""}</p>
          <button className="btn btn--ghost btn--sm" style={{ marginTop: 8 }} onClick={cancelar}>Cerrar</button>
        </div>
      )}
      {act && act.can_delete && (
        <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
          {anonimizar ? (
            <div style={{ fontSize: 13, margin: 0, flexBasis: "100%" }}>
              <p style={{ margin: 0 }}><b>{u.name}</b> tiene actividad a su nombre, así que no se borra del todo: se <b>anonimiza y desactiva</b>. Sus documentos y la bitácora quedan a nombre de «Usuario eliminado», se borran su nombre, su correo, su contraseña y su verificación en dos pasos, y sale de esta lista. No se puede deshacer (se puede volver a invitar con el mismo correo).</p>
              {act.activity.length > 0 && <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{act.activity.slice(0, 8).map((a) => <li key={a.what}>{a.what}: {a.count}</li>)}</ul>}
            </div>
          ) : (
            <p style={{ fontSize: 13, margin: 0, flexBasis: "100%" }}><b>{u.name}</b> no tiene actividad a su nombre. Se borran su acceso y su cuenta; no se puede deshacer (se puede volver a invitar con el mismo correo).</p>
          )}
          <div style={{ flex: "1 1 160px" }}><Field label="Escriba ELIMINAR"><input className="input" value={confirmTxt} onChange={(e) => setConfirmTxt(e.target.value)} autoComplete="off" /></Field></div>
          <div style={{ flex: "1 1 220px" }}><Field label="Confirme con su contraseña"><input className="input" type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && listo && !busy) go(); }} /></Field></div>
          <button className="btn btn--danger" onClick={go} disabled={busy || !listo}>{busy ? "Eliminando…" : anonimizar ? "Anonimizar y eliminar" : "Eliminar definitivamente"}</button>
          <button className="btn btn--ghost" onClick={cancelar} disabled={busy}>Cancelar</button>
          {err && <p style={{ color: "var(--bad)", fontSize: 13, margin: 0, flexBasis: "100%" }} role="alert">{err}</p>}
        </div>
      )}
    </div>
  );
}
