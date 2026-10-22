/* Ajustes → Usuarios → Roles y permisos (reunión 21/10, pedido 20: "que Andrés pueda definir qué ve cada rol, y crear
   roles, sin depender del desarrollador"). Solo el administrador. Los permisos de fábrica siguen en el código; aquí se
   cambian por empresa y se pueden restablecer. Un rol nuevo se crea a partir de uno existente.
   Lo que es solo del administrador (anular facturas, publicar en la tienda, ver costos, configurar la empresa…) no sale
   en la lista y la API lo rechaza aunque alguien lo mande. Todo cambio queda en la bitácora. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { registerRoles, roleMeta } from "../../app/roles";
import { Card, Field, I, Icon, Modal } from "../../ui/components";

type Perms = Record<string, string[]>;
type RoleRow = {
  code: string; name: string; kind: "predefinido" | "personalizado"; editable: boolean; customized: boolean; base_code: string | null;
  permissions: Perms; default_permissions: Perms | null; members: number;
};
type Catalog = { module: string; label: string; actions: { action: string; label: string }[] }[];
type Resp = { roles: RoleRow[]; catalog: Catalog; locked: string[] };

const errMsg = (e: unknown) => (e instanceof Error ? e.message : "Error");
/* lo mismo que core/permisos.SENSITIVE: sumarle esto a un rol pide la contraseña (quienes ya lo tienen lo ganan) */
const SENSITIVE: [string, string | null][] = [["settings", null], ["accounting", null], ["payroll", null], ["aliados", "usuarios"], ["portal_clientes", "aprobar"]];
const sensitive = (p: Perms) => new Set(SENSITIVE.filter(([m, a]) => (a == null ? (p[m] || []).length > 0 : (p[m] || []).includes(a))).map(([m, a]) => (a ? `${m}.${a}` : m)));
const same = (a: Perms, b: Perms) => {
  const ka = Object.keys(a).filter((k) => a[k]?.length), kb = Object.keys(b).filter((k) => b[k]?.length);
  return ka.length === kb.length && ka.every((k) => (b[k] || []).length === a[k].length && a[k].every((x) => b[k].includes(x)));
};

export default function RolesEditor({ onChanged, focus }: { onChanged: () => void; focus?: { code: string; n: number } | null }) {
  const { toast, reloadMatrix } = useSession();
  const [data, setData] = useState<Resp | null>(null);
  const [sel, setSel] = useState<string>("ventas");
  const selRef = useRef(sel);
  selRef.current = sel;
  const [draft, setDraft] = useState<Perms>({});
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [pw, setPw] = useState("");
  const [nuevo, setNuevo] = useState<{ name: string; from_code: string } | null>(null);

  const load = useCallback(async (focus?: string) => {
    try {
      const d = await api<Resp>("/settings/roles");
      registerRoles({ labels: Object.fromEntries(d.roles.filter((r) => r.kind === "personalizado").map((r) => [r.code, r.name])) });
      setData(d);
      if (focus) setSel(focus);
    } catch (e) { toast(errMsg(e), "bad"); }
  }, [toast]);
  useEffect(() => { load(); }, [load]);
  /* «Qué ve cada rol» pide abrir un rol (reunión 2, pedido 29) */
  useEffect(() => {
    if (!focus) return;
    // se pregunta aquí y no dentro de setSel: React puede correr dos veces esa función (StrictMode) y el aviso saldría doble
    if (focus.code !== selRef.current && dirtyRef.current && !confirm("Hay cambios sin guardar. ¿Descartarlos?")) return;
    setSel(focus.code);
  }, [focus]);

  const role = data?.roles.find((r) => r.code === sel) ?? null;
  useEffect(() => { if (role) { setDraft(JSON.parse(JSON.stringify(role.permissions))); setReason(""); setPw(""); } }, [role?.code, role?.permissions]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = useMemo(() => !!role && !same(draft, role.permissions), [draft, role]);
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  const needPw = useMemo(() => {
    if (!role) return false;
    const antes = sensitive(role.permissions);
    return [...sensitive(draft)].some((x) => !antes.has(x));
  }, [draft, role]);

  const toggle = (mod: string, act: string, on: boolean) => {
    setDraft((cur) => {
      let acts = new Set(cur[mod] || []);
      if (on) { acts.add(act); if (act !== "ver" && data?.catalog.find((m) => m.module === mod)?.actions.some((a) => a.action === "ver")) acts.add("ver"); }
      else if (act === "ver") acts = new Set(); // sin "ver" el resto del módulo no sirve
      else acts.delete(act);
      const out = { ...cur };
      if (acts.size) out[mod] = [...acts]; else delete out[mod];
      return out;
    });
  };

  const done = (msg: string, focus?: string) => { toast(msg); load(focus); onChanged(); reloadMatrix(); };
  const save = async () => {
    if (!role) return;
    setBusy(true);
    try { await api(`/settings/roles/${role.code}`, { method: "PUT", json: { permissions: draft, reason: reason.trim() || undefined, admin_password: needPw ? pw : undefined } }); setPw(""); done(`Listo: ${role.name} actualizado. Aplica en la siguiente acción de cada persona.`); }
    catch (e) { toast(errMsg(e), "bad"); } finally { setBusy(false); }
  };
  const reset = async () => {
    if (!role || !confirm(`¿Volver ${role.name} a sus permisos de fábrica?`)) return;
    try { await api(`/settings/roles/${role.code}/reset`, { method: "POST" }); done(`${role.name} volvió a sus permisos de fábrica`); }
    catch (e) { toast(errMsg(e), "bad"); }
  };
  const remove = async () => {
    if (!role || !confirm(`¿Borrar el rol «${role.name}»?`)) return;
    try { await api(`/settings/roles/${role.code}`, { method: "DELETE" }); done("Rol borrado", "ventas"); }
    catch (e) { toast(errMsg(e), "bad"); }
  };
  const create = async () => {
    if (!nuevo) return;
    setBusy(true);
    try {
      const r = await api<{ code: string; name: string }>("/settings/roles", { method: "POST", json: { name: nuevo.name.trim(), from_code: nuevo.from_code } });
      setNuevo(null); done(`Rol «${r.name}» creado: ajustá sus permisos y asignalo en la lista de usuarios`, r.code);
    } catch (e) { toast(errMsg(e), "bad"); } finally { setBusy(false); }
  };

  if (!data) return <Card title="Roles y permisos"><div style={{ padding: 16, textAlign: "center" }}><span className="spinner" /></div></Card>;
  return (
    <Card title="Roles y permisos" extra={<button className="btn btn--soft btn--sm" onClick={() => setNuevo({ name: "", from_code: sel === "admin" ? "ventas" : sel })}><Icon d={I.plus} />Crear rol</button>}>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Elegí un rol para ver y cambiar lo que puede hacer. Aplica en la siguiente acción de cada persona, sin cerrar sesión, y queda en la bitácora. Anular facturas, publicar en la tienda, ver costos (eso va en «Quién ve costos») y configurar la empresa son solo del administrador.</p>
      <div className="chips" role="group" aria-label="Roles" style={{ marginBottom: 12, flexWrap: "wrap" }}>
        {data.roles.map((r) => (
          <button key={r.code} type="button" className={`chip ${sel === r.code ? "is-on" : ""}`} aria-pressed={sel === r.code} onClick={() => { if (dirty && !confirm("Hay cambios sin guardar. ¿Descartarlos?")) return; setSel(r.code); }}>
            {r.name}{r.kind === "personalizado" ? " · creado" : r.customized ? " · cambiado" : ""}{r.members ? ` (${r.members})` : ""}
          </button>
        ))}
      </div>
      {role && !role.editable && <p style={{ fontSize: 13, margin: 0 }}><b>{role.name}</b> siempre puede todo: no se edita.</p>}
      {role && role.editable && (
        <>
          <p className="muted" style={{ fontSize: 12.5, margin: "0 0 10px" }}>
            {role.kind === "personalizado" ? `Creado a partir de ${roleMeta(role.base_code || "").label}.` : role.customized ? "Este rol tiene permisos cambiados respecto de los de fábrica." : "Permisos de fábrica."}
            {role.members ? ` ${role.members} persona(s) activa(s) tienen este rol.` : " Nadie tiene este rol todavía."}
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))", gap: 10 }}>
            {data.catalog.map((m) => {
              const tiene = draft[m.module] || [];
              const fab = role.default_permissions?.[m.module] || [];
              return (
                <fieldset key={m.module} style={{ border: "1px solid var(--hair)", borderRadius: 10, padding: "8px 12px", margin: 0, minWidth: 0 }}>
                  <legend style={{ fontSize: 13, fontWeight: 600, padding: "0 4px" }}>{m.label}</legend>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {m.actions.map((a) => {
                      const on = tiene.includes(a.action);
                      const cambio = role.default_permissions != null && on !== fab.includes(a.action);
                      return (
                        <label key={a.action} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }} title={cambio ? (on ? "Agregado (de fábrica no lo tiene)" : "Quitado (de fábrica lo tiene)") : undefined}>
                          <input type="checkbox" checked={on} onChange={(e) => toggle(m.module, a.action, e.target.checked)} />
                          <span style={cambio ? { fontWeight: 600, color: on ? "var(--ok)" : "var(--bad)" } : undefined}>{a.label}</span>
                        </label>
                      );
                    })}
                  </div>
                </fieldset>
              );
            })}
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap", marginTop: 12 }}>
            <div style={{ flex: "1 1 240px" }}><Field label="Motivo (queda en la bitácora)"><input className="input" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Opcional" /></Field></div>
            {needPw && <div style={{ flex: "1 1 220px" }}><Field label="Confirmá con tu contraseña" hint="Darle a un rol acceso a Ajustes, contabilidad, planillas, usuarios de aliados o accesos de clientes pide tu contraseña."><input className="input" type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} /></Field></div>}
            <button className="btn btn--crimson" onClick={save} disabled={busy || !dirty || !Object.keys(draft).length || (needPw && !pw)}>{busy ? "Guardando…" : "Guardar permisos"}</button>
            {dirty && <button className="btn btn--ghost" onClick={() => setDraft(JSON.parse(JSON.stringify(role.permissions)))} disabled={busy}>Deshacer</button>}
            {role.kind === "predefinido" && role.customized && <button className="btn btn--ghost" onClick={reset} disabled={busy}>Restablecer de fábrica</button>}
            {role.kind === "personalizado" && <button className="btn btn--danger btn--sm" onClick={remove} disabled={busy || role.members > 0} title={role.members > 0 ? "Cambiales el rol a las personas que lo tienen antes de borrarlo" : undefined}>Borrar rol</button>}
          </div>
        </>
      )}
      {nuevo && (
        <Modal title="Crear rol" onClose={() => setNuevo(null)} foot={<><button className="btn btn--ghost" onClick={() => setNuevo(null)}>Cancelar</button><button className="btn btn--crimson" onClick={create} disabled={busy || nuevo.name.trim().length < 2}>{busy ? "Creando…" : "Crear"}</button></>}>
          <Field label="Nombre del rol"><input className="input" autoFocus maxLength={80} value={nuevo.name} onChange={(e) => setNuevo({ ...nuevo, name: e.target.value })} placeholder="Por ejemplo: Vendedor senior" /></Field>
          <Field label="A partir de" hint="Copia los permisos que ese rol tiene hoy en la empresa; después los ajustás.">
            <select className="select" value={nuevo.from_code} onChange={(e) => setNuevo({ ...nuevo, from_code: e.target.value })}>
              {data.roles.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
            </select>
          </Field>
          {nuevo.from_code === "admin" && <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Del administrador se copia todo lo que se puede repartir; lo que es solo del administrador queda fuera.</p>}
        </Modal>
      )}
    </Card>
  );
}
