/* Ajustes (plan 3.8): empresa, facturacion, pagos (metodos manuales, cuentas, pasarelas), usuarios, cuenta/2FA, correo saliente. */
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, fmtDate } from "../lib/api";
import { useSession } from "../app/session";
import { Badge, Card, Empty, Field, I, Icon, Modal } from "../ui/components";

type Group = { id: number; doc_type: string; prefix: string; branch: string; terminal: string; current: number; is_default: boolean };
type Cfg = { invoice_valid_days: number; quote_valid_days: number; invoice_footer: string; quote_footer: string; notify_due: boolean; remind_days_before: number; daily_close_email: boolean; bcc: string[]; manual_payment_methods: { name: string; instructions: string; active: boolean }[]; activity_codes: string[]; einvoice_provider: string; phones: { number: string; kind: string; main: boolean }[]; social: Record<string, string> };
type Bank = { id: number; name: string; bank: string | null; currency: string; number: string | null; active: boolean };
type Users = { users: { id: number; email: string; name: string; role: string; active: boolean; totp: boolean; last_login: string | null }[]; invitations: { id: number; email: string; role: string; expires_at: string }[]; roles: string[] };
type Gw = { id: number; provider: string; client_id: string | null; secret_mask: string | null; is_primary: boolean; active: boolean; mode: string };
type Mail = { id: number; to: string; subject: string; status: string; entity: string | null; entity_id: number | null; created_at: string; error: string | null };

const TABS = [["empresa", "Empresa"], ["facturacion", "Facturación"], ["pagos", "Pagos y cobros"], ["usuarios", "Usuarios"], ["cuenta", "Mi cuenta"], ["correo", "Correo"]];

export default function Settings() {
  const { me, reload, toast } = useSession();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "empresa";
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [banks, setBanks] = useState<Bank[]>([]);
  const [users, setUsers] = useState<Users | null>(null);
  const [gws, setGws] = useState<Gw[]>([]);
  const [mails, setMails] = useState<Mail[]>([]);
  const [company, setCompany] = useState({ name: "", legal_name: "", tax_id: "", default_currency: "CRC", sector: "", logo_url: "" });
  const [totp, setTotp] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [invite, setInvite] = useState<{ email: string; role: string } | null>(null);
  const [inviteLink, setInviteLink] = useState<string | null>(null);
  const [bank, setBank] = useState<{ id?: number; name: string; bank: string; currency: string; number: string; active: boolean } | null>(null);
  const [gw, setGw] = useState<{ provider: string; client_id: string; secret: string; is_primary: boolean; active: boolean; mode: string } | null>(null);
  const [pw, setPw] = useState({ current_password: "", new_password: "" });

  const load = () => {
    api<Cfg>("/settings").then(setCfg); api<Group[]>("/billing-groups").then(setGroups); api<Bank[]>("/settings/bank-accounts").then(setBanks);
    api<Users>("/settings/users").then(setUsers); api<Gw[]>("/settings/gateways").then(setGws); api<Mail[]>("/settings/outbox").then(setMails);
  };
  useEffect(load, []);
  useEffect(() => { if (me) setCompany({ name: me.tenant.name, legal_name: me.tenant.legal_name || "", tax_id: me.tenant.tax_id || "", default_currency: me.tenant.default_currency, sector: "", logo_url: me.tenant.logo_url || "" }); }, [me]);

  const ok = (m: string) => { toast(m); load(); };
  const err = (e: unknown) => toast(e instanceof Error ? e.message : "Error", "bad");
  const saveCompany = () => api("/tenant", { method: "PATCH", json: { ...company, logo_url: company.logo_url || null, sector: company.sector || null } }).then(async () => { await reload(); ok("Empresa actualizada"); }).catch(err);
  const saveCfg = (patch: Partial<Cfg>) => api<Cfg>("/settings", { method: "PUT", json: patch }).then((c) => { setCfg(c); toast("Ajustes guardados"); }).catch(err);
  const saveGroup = (g: Group) => api(`/settings/billing-groups/${g.id}`, { method: "PUT", json: { prefix: g.prefix, branch: g.branch, terminal: g.terminal, current: g.current, is_default: g.is_default } }).then(() => ok("Grupo actualizado")).catch(err);
  const saveBank = () => { if (!bank) return; const { id, ...b } = bank; api(id ? `/settings/bank-accounts/${id}` : "/settings/bank-accounts", { method: id ? "PUT" : "POST", json: b }).then(() => { setBank(null); ok("Cuenta guardada"); }).catch(err); };
  const saveGw = () => { if (!gw) return; api("/settings/gateways", { method: "PUT", json: { ...gw, secret: gw.secret || null } }).then(() => { setGw(null); ok("Pasarela guardada"); }).catch(err); };
  const sendInvite = () => { if (!invite) return; api<{ link: string }>("/settings/invitations", { method: "POST", json: invite }).then((r) => { setInvite(null); setInviteLink(r.link); ok("Invitación creada"); }).catch(err); };
  const setRole = (uid: number, role: string) => api(`/settings/users/${uid}`, { method: "PATCH", json: { role } }).then(() => ok("Rol actualizado")).catch(err);
  const setActive = (uid: number, active: boolean) => api(`/settings/users/${uid}`, { method: "PATCH", json: { active } }).then(() => ok(active ? "Usuario activado" : "Usuario desactivado")).catch(err);
  const setup2fa = () => api<{ secret: string; otpauth_uri: string }>("/auth/2fa/setup", { method: "POST" }).then(setTotp).catch(err);
  const verify2fa = () => api("/auth/2fa/verify", { method: "POST", json: { code } }).then(async () => { await reload(); setTotp(null); toast("2FA activado"); }).catch(() => toast("Código inválido", "bad"));
  const changePw = () => api("/auth/password", { method: "POST", json: pw }).then(() => { setPw({ current_password: "", new_password: "" }); toast("Contraseña actualizada"); }).catch(err);

  return (
    <>
      <div className="page-head"><div><div className="meta">09 · Ajustes</div><h1 className="h1">Ajustes</h1></div></div>
      <div className="tabs" style={{ alignSelf: "flex-start" }}>{TABS.map(([k, l]) => <button key={k} className={tab === k ? "is-active" : ""} onClick={() => setParams({ tab: k })}>{l}</button>)}</div>

      {tab === "empresa" && (
        <div className="grid-2">
          <Card title="Ajustes generales de empresa">
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <Field label="Empresa"><input className="input" value={company.name} onChange={(e) => setCompany({ ...company, name: e.target.value })} /></Field>
              <Field label="Razón social"><input className="input" value={company.legal_name} onChange={(e) => setCompany({ ...company, legal_name: e.target.value })} /></Field>
              <div className="grid-2">
                <Field label="Cédula jurídica"><input className="input input--mono" style={{ textAlign: "left" }} value={company.tax_id} onChange={(e) => setCompany({ ...company, tax_id: e.target.value })} /></Field>
                <Field label="Divisa predeterminada"><select className="select" value={company.default_currency} onChange={(e) => setCompany({ ...company, default_currency: e.target.value })}><option>CRC</option><option>USD</option></select></Field>
              </div>
              <Field label="Logo (URL)" hint="Aparece en cotizaciones, facturas y la página de pago."><input className="input" value={company.logo_url} onChange={(e) => setCompany({ ...company, logo_url: e.target.value })} /></Field>
              <button className="btn btn--crimson" style={{ alignSelf: "flex-start" }} onClick={saveCompany}><Icon d={I.check} />Guardar</button>
            </div>
          </Card>
          {cfg && (
            <Card title="Teléfonos, actividad y redes">
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <Field label="Códigos de actividad económica (Hacienda)" hint="Separados por coma. El primero es el predeterminado."><input className="input" defaultValue={cfg.activity_codes.join(", ")} onBlur={(e) => saveCfg({ activity_codes: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} /></Field>
                <Field label="Teléfono principal (WhatsApp)"><input className="input" defaultValue={cfg.phones[0]?.number || ""} onBlur={(e) => saveCfg({ phones: [{ number: e.target.value, kind: "whatsapp", main: true }] })} /></Field>
                <div className="grid-3">
                  {(["facebook", "instagram", "linkedin"] as const).map((k) => <Field key={k} label={k[0].toUpperCase() + k.slice(1)}><input className="input" defaultValue={cfg.social[k] || ""} onBlur={(e) => saveCfg({ social: { ...cfg.social, [k]: e.target.value } })} /></Field>)}
                </div>
              </div>
            </Card>
          )}
        </div>
      )}

      {tab === "facturacion" && cfg && (
        <div className="grid-2">
          <Card title="Vigencias y mensajes">
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div className="grid-2">
                <Field label="Días de vigencia de factura"><input className="input input--mono" type="number" defaultValue={cfg.invoice_valid_days} onBlur={(e) => saveCfg({ invoice_valid_days: Number(e.target.value) })} /></Field>
                <Field label="Días de vigencia de cotización"><input className="input input--mono" type="number" defaultValue={cfg.quote_valid_days} onBlur={(e) => saveCfg({ quote_valid_days: Number(e.target.value) })} /></Field>
              </div>
              <Field label="Mensaje al pie de toda factura"><textarea className="textarea" defaultValue={cfg.invoice_footer} onBlur={(e) => saveCfg({ invoice_footer: e.target.value })} /></Field>
              <Field label="Mensaje al pie de cotizaciones" hint="Ideal para instrucciones de pago y cuentas bancarias."><textarea className="textarea" defaultValue={cfg.quote_footer} onBlur={(e) => saveCfg({ quote_footer: e.target.value })} /></Field>
              <Field label="Proveedor de factura electrónica" hint="sandbox simula Hacienda para pruebas; Alanube/GTI requieren credenciales (Fase 2).">
                <select className="select" value={cfg.einvoice_provider} onChange={(e) => saveCfg({ einvoice_provider: e.target.value })}><option value="none">Sin proveedor</option><option value="sandbox">Sandbox (simulación)</option><option value="alanube">Alanube</option><option value="gti">GTI</option></select>
              </Field>
            </div>
          </Card>
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <Card title="Notificaciones">
              <div style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 13 }}>
                <label style={{ display: "flex", gap: 10 }}><input type="checkbox" checked={cfg.notify_due} onChange={(e) => saveCfg({ notify_due: e.target.checked })} />Notificar vencimiento al cliente</label>
                <Field label="¿Cuántos días antes recordar?"><input className="input input--mono" type="number" defaultValue={cfg.remind_days_before} onBlur={(e) => saveCfg({ remind_days_before: Number(e.target.value) })} style={{ width: 120 }} /></Field>
                <label style={{ display: "flex", gap: 10 }}><input type="checkbox" checked={cfg.daily_close_email} onChange={(e) => saveCfg({ daily_close_email: e.target.checked })} />Enviar reporte de cierre de caja diario</label>
                <Field label="Correos en copia (BCC) de toda factura" hint="Separados por coma."><input className="input" defaultValue={cfg.bcc.join(", ")} onBlur={(e) => saveCfg({ bcc: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} /></Field>
              </div>
            </Card>
            <Card title="Grupos de facturación y consecutivos" flush>
              <table className="table"><thead><tr><th>Tipo</th><th>Prefijo</th><th>Sucursal</th><th>Terminal</th><th className="num">Actual</th><th /></tr></thead>
                <tbody>{groups.map((g) => <tr key={g.id}><td style={{ fontWeight: 600 }}>{g.doc_type}</td><td><input className="input" style={{ height: 32, width: 80 }} value={g.prefix} onChange={(e) => setGroups(groups.map((x) => (x.id === g.id ? { ...x, prefix: e.target.value } : x)))} /></td><td><input className="input input--mono" style={{ height: 32, width: 64 }} value={g.branch} onChange={(e) => setGroups(groups.map((x) => (x.id === g.id ? { ...x, branch: e.target.value } : x)))} /></td><td><input className="input input--mono" style={{ height: 32, width: 84 }} value={g.terminal} onChange={(e) => setGroups(groups.map((x) => (x.id === g.id ? { ...x, terminal: e.target.value } : x)))} /></td><td className="num"><input className="input input--mono" style={{ height: 32, width: 90 }} value={g.current} onChange={(e) => setGroups(groups.map((x) => (x.id === g.id ? { ...x, current: Number(e.target.value) } : x)))} /></td><td className="num"><button className="btn btn--ghost btn--sm" onClick={() => saveGroup(g)}>Guardar</button></td></tr>)}</tbody></table>
              <p className="muted" style={{ padding: "10px 18px", fontSize: 12 }}>Consecutivo v4.4 = sucursal (3) + terminal (5) + tipo (2) + secuencia (10). El valor actual nunca retrocede.</p>
            </Card>
          </div>
        </div>
      )}

      {tab === "pagos" && cfg && (
        <div className="grid-2">
          <Card title="Métodos de pago personalizados" extra={<button className="btn btn--ghost btn--sm" onClick={() => saveCfg({ manual_payment_methods: [...cfg.manual_payment_methods, { name: "Nuevo método", instructions: "", active: true }] })}><Icon d={I.plus} />Agregar</button>}>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {cfg.manual_payment_methods.map((m, i) => (
                <div key={i} className="pay__method" style={{ gap: 8 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}><input className="input" style={{ height: 34, fontWeight: 600 }} defaultValue={m.name} onBlur={(e) => saveCfg({ manual_payment_methods: cfg.manual_payment_methods.map((x, k) => (k === i ? { ...x, name: e.target.value } : x)) })} /><label style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center", whiteSpace: "nowrap" }}><input type="checkbox" checked={m.active} onChange={(e) => saveCfg({ manual_payment_methods: cfg.manual_payment_methods.map((x, k) => (k === i ? { ...x, active: e.target.checked } : x)) })} />Activo</label><button className="x" onClick={() => saveCfg({ manual_payment_methods: cfg.manual_payment_methods.filter((_, k) => k !== i) })}><Icon d={I.x} size={14} /></button></div>
                  <textarea className="textarea" style={{ minHeight: 56 }} defaultValue={m.instructions} placeholder="Instrucciones de pago que verá el cliente" onBlur={(e) => saveCfg({ manual_payment_methods: cfg.manual_payment_methods.map((x, k) => (k === i ? { ...x, instructions: e.target.value } : x)) })} />
                </div>
              ))}
            </div>
          </Card>
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <Card title="Cuentas bancarias" flush extra={<button className="btn btn--ghost btn--sm" onClick={() => setBank({ name: "", bank: "", currency: "CRC", number: "", active: true })}><Icon d={I.plus} />Agregar</button>}>
              {banks.length === 0 ? <Empty hint="Se vinculan a los pagos registrados." /> : <table className="table"><thead><tr><th>Nombre</th><th>Banco</th><th>Divisa</th><th>Número</th><th /></tr></thead><tbody>{banks.map((b) => <tr key={b.id}><td style={{ fontWeight: 600 }}>{b.name}</td><td className="muted">{b.bank || "—"}</td><td>{b.currency}</td><td className="mono muted">{b.number || "—"}</td><td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setBank({ id: b.id, name: b.name, bank: b.bank || "", currency: b.currency, number: b.number || "", active: b.active })}>Ver</button></td></tr>)}</tbody></table>}
            </Card>
            <Card title="Pasarelas de pago" flush extra={<button className="btn btn--ghost btn--sm" onClick={() => setGw({ provider: "onvo", client_id: "", secret: "", is_primary: true, active: false, mode: "test" })}><Icon d={I.plus} />Agregar</button>}>
              {gws.length === 0 ? <Empty hint="ONVO Pay (SINPE Móvil + tarjetas) y PayPal. El secreto se guarda cifrado y nunca vuelve al navegador." /> : <table className="table"><thead><tr><th>Pasarela</th><th>Client ID</th><th>Secreto</th><th>Modo</th><th>Estado</th><th /></tr></thead><tbody>{gws.map((g) => <tr key={g.id}><td style={{ fontWeight: 600, textTransform: "uppercase" }}>{g.provider}{g.is_primary && <span className="meta"> · principal</span>}</td><td className="mono muted">{g.client_id || "—"}</td><td className="mono muted">{g.secret_mask || "—"}</td><td className="muted">{g.mode}</td><td><Badge status={g.active ? "confirmado" : "pendiente"} /></td><td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setGw({ provider: g.provider, client_id: g.client_id || "", secret: "", is_primary: g.is_primary, active: g.active, mode: g.mode })}>Editar</button></td></tr>)}</tbody></table>}
              <p className="muted" style={{ padding: "10px 18px", fontSize: 12 }}>Webhook: <span className="mono">/api/webhooks/onvo/{me?.tenant.id}</span> · firma HMAC, ventana 300 s, idempotente.</p>
            </Card>
          </div>
        </div>
      )}

      {tab === "usuarios" && users && (
        <Card title="Usuarios y roles" flush extra={<button className="btn btn--crimson btn--sm" onClick={() => setInvite({ email: "", role: "ventas" })}><Icon d={I.plus} />Invitar</button>}>
          <table className="table"><thead><tr><th>Usuario</th><th>Rol</th><th>2FA</th><th>Último acceso</th><th>Estado</th><th /></tr></thead>
            <tbody>
              {users.users.map((u) => <tr key={u.id}><td><b>{u.name}</b><div className="meta" style={{ textTransform: "none" }}>{u.email}</div></td><td><select className="select" style={{ height: 32, width: 150 }} value={u.role} onChange={(e) => setRole(u.id, e.target.value)}>{users.roles.map((r) => <option key={r}>{r}</option>)}</select></td><td>{u.totp ? <Badge status="confirmado" /> : <span className="muted">—</span>}</td><td className="muted">{u.last_login ? fmtDate(u.last_login.slice(0, 10)) : "nunca"}</td><td><Badge status={u.active ? "confirmado" : "anulada"} /></td><td className="num">{u.id !== me?.user.id && <button className="btn btn--ghost btn--sm" onClick={() => setActive(u.id, !u.active)}>{u.active ? "Desactivar" : "Activar"}</button>}</td></tr>)}
              {users.invitations.map((i) => <tr key={`i${i.id}`}><td><b>{i.email}</b><div className="meta">invitación pendiente</div></td><td className="muted">{i.role}</td><td>—</td><td className="muted">vence {fmtDate(i.expires_at.slice(0, 10))}</td><td><Badge status="pendiente" /></td><td /></tr>)}
            </tbody></table>
          <p className="muted" style={{ padding: "10px 18px", fontSize: 12 }}>Roles: admin (todo), ventas, caja, inventario, contabilidad, lectura. Los permisos se aplican en el menú y en la API.</p>
        </Card>
      )}

      {tab === "cuenta" && (
        <div className="grid-2">
          <Card title="Autenticación de dos factores">
            {me?.user.totp_enabled ? <p className="muted">2FA <b style={{ color: "var(--ok)" }}>activo</b> para {me.user.email}.</p> : totp ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <p className="muted" style={{ fontSize: 13 }}>Agregá esta clave en tu app de autenticación y confirmá con el código.</p>
                <code className="mono" style={{ padding: 10, background: "var(--bg-2)", borderRadius: 8, wordBreak: "break-all" }}>{totp.secret}</code>
                <div style={{ display: "flex", gap: 8 }}><input className="input input--mono" maxLength={6} placeholder="000000" value={code} onChange={(e) => setCode(e.target.value)} style={{ width: 140 }} /><button className="btn btn--crimson" onClick={verify2fa}>Activar</button></div>
              </div>
            ) : <button className="btn btn--soft" onClick={setup2fa}>Configurar 2FA</button>}
          </Card>
          <Card title="Cambiar contraseña">
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <Field label="Contraseña actual"><input className="input" type="password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} /></Field>
              <Field label="Nueva contraseña" hint="Mínimo 10 caracteres con letras y números."><input className="input" type="password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></Field>
              <button className="btn btn--crimson" style={{ alignSelf: "flex-start" }} onClick={changePw}>Actualizar</button>
            </div>
          </Card>
        </div>
      )}

      {tab === "correo" && (
        <Card title="Correo saliente" flush extra={<span className="meta">Resend · {mails.some((m) => m.status === "simulado") ? "modo simulado (sin API key)" : "activo"}</span>}>
          {mails.length === 0 ? <Empty hint="Cotizaciones, facturas, invitaciones y recordatorios enviados aparecen aquí." /> : <table className="table"><thead><tr><th>Fecha</th><th>Para</th><th>Asunto</th><th>Estado</th></tr></thead><tbody>{mails.map((m) => <tr key={m.id}><td className="muted">{fmtDate(m.created_at.slice(0, 10))}</td><td>{m.to}</td><td>{m.subject}{m.error && <div className="meta" style={{ color: "var(--bad)" }}>{m.error}</div>}</td><td><Badge status={m.status === "enviado" ? "confirmado" : m.status === "error" ? "fallido" : "pendiente"} /></td></tr>)}</tbody></table>}
        </Card>
      )}

      {bank && <Modal title={bank.id ? "Cuenta bancaria" : "Nueva cuenta"} onClose={() => setBank(null)} foot={<><button className="btn btn--ghost" onClick={() => setBank(null)}>Cancelar</button><button className="btn btn--crimson" onClick={saveBank}>Guardar</button></>}>
        <div className="grid-2"><Field label="Nombre"><input className="input" value={bank.name} onChange={(e) => setBank({ ...bank, name: e.target.value })} /></Field><Field label="Banco"><input className="input" value={bank.bank} onChange={(e) => setBank({ ...bank, bank: e.target.value })} /></Field><Field label="Divisa"><select className="select" value={bank.currency} onChange={(e) => setBank({ ...bank, currency: e.target.value })}><option>CRC</option><option>USD</option></select></Field><Field label="Número / IBAN"><input className="input input--mono" style={{ textAlign: "left" }} value={bank.number} onChange={(e) => setBank({ ...bank, number: e.target.value })} /></Field></div>
      </Modal>}
      {gw && <Modal title="Pasarela de pago" onClose={() => setGw(null)} foot={<><button className="btn btn--ghost" onClick={() => setGw(null)}>Cancelar</button><button className="btn btn--crimson" onClick={saveGw}>Guardar</button></>}>
        <div className="grid-2"><Field label="Pasarela"><select className="select" value={gw.provider} onChange={(e) => setGw({ ...gw, provider: e.target.value })}><option value="onvo">ONVO Pay</option><option value="paypal">PayPal</option></select></Field><Field label="Modo"><select className="select" value={gw.mode} onChange={(e) => setGw({ ...gw, mode: e.target.value })}><option value="test">Pruebas</option><option value="live">Producción</option></select></Field><Field label="Client ID / clave pública"><input className="input" value={gw.client_id} onChange={(e) => setGw({ ...gw, client_id: e.target.value })} /></Field><Field label="Client Secret" hint="Se cifra en el servidor. Dejar vacío para no cambiarlo."><input className="input" type="password" value={gw.secret} onChange={(e) => setGw({ ...gw, secret: e.target.value })} /></Field></div>
        <div style={{ display: "flex", gap: 16, fontSize: 13 }}><label style={{ display: "flex", gap: 8 }}><input type="checkbox" checked={gw.is_primary} onChange={(e) => setGw({ ...gw, is_primary: e.target.checked })} />Principal</label><label style={{ display: "flex", gap: 8 }}><input type="checkbox" checked={gw.active} onChange={(e) => setGw({ ...gw, active: e.target.checked })} />Activa</label></div>
      </Modal>}
      {invite && <Modal title="Invitar usuario" onClose={() => setInvite(null)} foot={<><button className="btn btn--ghost" onClick={() => setInvite(null)}>Cancelar</button><button className="btn btn--crimson" onClick={sendInvite}>Enviar invitación</button></>}>
        <Field label="Correo"><input className="input" type="email" value={invite.email} onChange={(e) => setInvite({ ...invite, email: e.target.value })} /></Field>
        <Field label="Rol"><select className="select" value={invite.role} onChange={(e) => setInvite({ ...invite, role: e.target.value })}>{users?.roles.map((r) => <option key={r}>{r}</option>)}</select></Field>
      </Modal>}
      {inviteLink && <Modal title="Invitación creada" onClose={() => setInviteLink(null)} foot={<button className="btn btn--ghost" onClick={() => setInviteLink(null)}>Cerrar</button>}>
        <p className="muted" style={{ fontSize: 13 }}>Se envió por correo (si el correo está configurado). También podés compartir el enlace directamente; vence en 7 días.</p>
        <div className="search" style={{ maxWidth: "none" }}><Icon d={I.link} size={16} /><input readOnly value={inviteLink} onFocus={(e) => e.currentTarget.select()} /></div>
        <button className="btn btn--soft" style={{ alignSelf: "flex-start" }} onClick={() => { navigator.clipboard.writeText(inviteLink); toast("Enlace copiado"); }}><Icon d={I.copy} />Copiar</button>
      </Modal>}
    </>
  );
}
