import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useSession } from "../app/session";
import { Card, Field, I, Icon } from "../ui/components";

type Group = { id: number; doc_type: string; prefix: string; branch: string; terminal: string; current: number; is_default: boolean };

export default function Settings() {
  const { me, reload, toast } = useSession();
  const [groups, setGroups] = useState<Group[]>([]);
  const [form, setForm] = useState({ name: "", legal_name: "", tax_id: "", default_currency: "CRC" });
  const [totp, setTotp] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  useEffect(() => { api<Group[]>("/billing-groups").then(setGroups).catch(() => {}); }, []);
  useEffect(() => { if (me) setForm({ name: me.tenant.name, legal_name: me.tenant.legal_name || "", tax_id: me.tenant.tax_id || "", default_currency: me.tenant.default_currency }); }, [me]);

  const save = async () => { try { await api("/tenant", { method: "PATCH", json: form }); await reload(); toast("Empresa actualizada"); } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } };
  const setup2fa = async () => setTotp(await api("/auth/2fa/setup", { method: "POST" }));
  const verify2fa = async () => { try { await api("/auth/2fa/verify", { method: "POST", json: { code } }); await reload(); setTotp(null); toast("2FA activado"); } catch { toast("Código inválido", "bad"); } };

  return (
    <>
      <div className="page-head"><div><div className="meta">08 · Ajustes</div><h1 className="h1">Empresa y cuenta</h1></div></div>
      <div className="grid-2">
        <Card title="Ajustes generales de empresa">
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <Field label="Empresa"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="Razón social"><input className="input" value={form.legal_name} onChange={(e) => setForm({ ...form, legal_name: e.target.value })} /></Field>
            <div className="grid-2">
              <Field label="Cédula jurídica"><input className="input input--mono" style={{ textAlign: "left" }} value={form.tax_id} onChange={(e) => setForm({ ...form, tax_id: e.target.value })} /></Field>
              <Field label="Divisa predeterminada"><select className="select" value={form.default_currency} onChange={(e) => setForm({ ...form, default_currency: e.target.value })}><option>CRC</option><option>USD</option></select></Field>
            </div>
            <button className="btn btn--crimson" style={{ alignSelf: "flex-start" }} onClick={save}><Icon d={I.check} />Guardar</button>
          </div>
        </Card>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Card title="Grupos de facturación" flush>
            <table className="table"><thead><tr><th>Tipo</th><th>Prefijo</th><th>Sucursal · terminal</th><th className="num">Valor actual</th></tr></thead>
              <tbody>{groups.map((g) => <tr key={g.id}><td style={{ fontWeight: 600 }}>{g.doc_type}</td><td className="mono">{g.prefix}</td><td className="mono muted">{g.branch} · {g.terminal}</td><td className="num mono">{g.current}</td></tr>)}</tbody></table>
          </Card>
          <Card title="Mi cuenta · 2FA">
            {me?.user.totp_enabled ? <p className="muted">Autenticación de dos factores <b style={{ color: "var(--ok)" }}>activa</b> para {me.user.email}.</p> : totp ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <p className="muted" style={{ fontSize: 13 }}>Agregá esta clave en tu app de autenticación (Google Authenticator, Authy…) y confirmá con el código.</p>
                <code className="mono" style={{ padding: 10, background: "var(--bg-2)", borderRadius: 8, wordBreak: "break-all" }}>{totp.secret}</code>
                <div style={{ display: "flex", gap: 8 }}><input className="input input--mono" maxLength={6} placeholder="000000" value={code} onChange={(e) => setCode(e.target.value)} style={{ width: 140 }} /><button className="btn btn--crimson" onClick={verify2fa}>Activar</button></div>
              </div>
            ) : <button className="btn btn--soft" onClick={setup2fa}>Configurar 2FA</button>}
          </Card>
          <Card title="Roles y permisos">
            <p className="muted" style={{ fontSize: 13 }}>Tu rol: <b style={{ color: "var(--text)" }}>{me?.role}</b>. Roles disponibles: admin, ventas, caja, inventario, contabilidad, lectura — permisos por módulo × acción aplicados en menú y API.</p>
          </Card>
        </div>
      </div>
    </>
  );
}
