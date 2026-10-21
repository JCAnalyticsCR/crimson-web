/* Administración → Aliados (reunión 13/10, 10:47-12:44): las empresas aliadas con su estado, usuarios (cupos),
   lo que llevan en común y si la mensualidad está al día. Arriba, con permiso de precios: el recargo por defecto
   para aliados ("costo 100 → aliado 105") y qué activos ve el aliado; con permiso de mensualidad, el servicio y el monto
   por defecto del cobro mensual (plan 1.4). Sus usuarios usan el portal de aliados. */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, api, fmtDate } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";
import type { InviteOut } from "../cliente/InviteLink";
import { Lookup, searchCustomers, searchProducts, type LookupItem } from "../../ui/Lookup";

export type AllyRow = {
  id: number; name: string; tax_id: string | null; email: string | null; phone: string | null; state: "activo" | "suspendido" | "inactivo"; suspended_reason: string | null;
  users_active: number; invitations_pending: number; last_login: string | null; seats_used: number; seats_max: number | null; proyectos_en_comun: number; oportunidades_abiertas: number;
  plan: { amount: string | null; currency: string; mode: string; al_dia: boolean; activo: boolean };
};
type PlanDefault = { amount: string | number | null; currency: "CRC" | "USD"; mode: "fija" | "por_usuario" };
type Config = {
  markup_pct: string | null; plan_product_id: number | null; assets_scope: "proyectos" | "clientes";
  /* plan 1.4: servicio facturado y mensualidad por defecto de los planes nuevos */
  plan_product?: { id: number; code: string; name: string; cabys_code: string | null } | null; plan_default?: PlanDefault;
};
export const STATE_TONE: Record<string, string> = { activo: "ok", suspendido: "warn", inactivo: "muted" };
export const STATE_LABEL: Record<string, string> = { activo: "Activo", suspendido: "Suspendido", inactivo: "Inactivo" };

export const searchSuppliers = async (q: string): Promise<LookupItem[]> => {
  const r = await api<{ id: number; name: string; tax_id?: string | null }[]>("/suppliers");
  const t = q.trim().toLowerCase();
  return r.filter((s) => !t || s.name.toLowerCase().includes(t)).slice(0, 20).map((s) => ({ id: s.id, label: s.name, hint: s.tax_id || undefined }));
};

function NewAlly({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const { toast } = useSession();
  const [modo, setModo] = useState<"nuevo" | "proveedor" | "cliente">("nuevo");
  const { allows } = useSession();
  const [f, setF] = useState({ name: "", tax_id: "", email: "", phone: "" });
  /* alta en un paso: el primer usuario del portal (normalmente su contacto comercial) se invita al crear */
  const [inv, setInv] = useState({ on: allows("aliados.usuarios"), email: "", name: "", role: "aliado" });
  const [ref, setRef] = useState<LookupItem | null>(null);
  const [dup, setDup] = useState<{ id: number; name: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const crear = async () => {
    setBusy(true);
    setDup(null);
    try {
      const body = modo === "nuevo" ? { name: f.name, tax_id: f.tax_id || null, email: f.email || null, phone: f.phone || null } : modo === "proveedor" ? { supplier_id: ref?.id } : { customer_id: ref?.id };
      const a = await api<{ id: number; email?: string | null }>("/allies", { method: "POST", json: body });
      const correo = (inv.email || (modo === "nuevo" ? f.email : a.email || "")).trim();
      let invite: InviteOut | null = null;
      if (inv.on && correo.includes("@")) {
        try { invite = await api<InviteOut>(`/allies/${a.id}/invitations`, { method: "POST", json: { email: correo, role: inv.role, name: inv.name || null } }); }
        catch (e) { toast(`Aliado creado, pero la invitación no salió: ${e instanceof Error ? e.message : "error"}`, "bad"); }
      }
      onClose();
      nav(`/aliados/${a.id}?tab=${invite ? "usuarios" : "datos"}`, { state: invite ? { invite, email: correo } : undefined });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setDup((e.data as { existing?: { id: number; name: string } })?.existing || null);
      toast(e instanceof Error ? e.message : "No se pudo crear", "bad");
    } finally { setBusy(false); }
  };
  return (
    <Modal title="Nuevo aliado" onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" disabled={busy || (modo === "nuevo" ? f.name.trim().length < 2 : !ref)} onClick={crear}><Icon d={I.check} />Crear</button></>}>
      <div style={{ display: "flex", gap: 6, marginBottom: 12, flexWrap: "wrap" }}>
        {(["nuevo", "proveedor", "cliente"] as const).map((m) => <button key={m} className={`btn btn--sm ${modo === m ? "btn--crimson" : "btn--ghost"}`} onClick={() => { setModo(m); setRef(null); }}>{m === "nuevo" ? "Empresa nueva" : m === "proveedor" ? "Desde un proveedor" : "Desde un cliente"}</button>)}
      </div>
      {modo === "nuevo" ? (
        <>
          <Field label="Nombre comercial"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Cédula jurídica"><input className="input" value={f.tax_id} onChange={(e) => setF({ ...f, tax_id: e.target.value })} /></Field>
          <Field label="Correo"><input className="input" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          <Field label="Teléfono"><input className="input" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
        </>
      ) : (
        <Field label={modo === "proveedor" ? "Proveedor" : "Cliente"} hint="Se copian el nombre, la cédula, el correo y el teléfono, y queda ligado.">
          <Lookup value={ref?.label || ""} fetcher={modo === "proveedor" ? searchSuppliers : searchCustomers} onSelect={(it) => setRef(it)} placeholder="Buscar" />
        </Field>
      )}
      {allows("aliados.usuarios") && (
        <div style={{ borderTop: "1px solid var(--hair)", marginTop: 12, paddingTop: 12 }}>
          <label className="pf-bool"><input type="checkbox" checked={inv.on} onChange={(e) => setInv({ ...inv, on: e.target.checked })} />Invitar ya a su primer usuario al portal de aliados</label>
          {inv.on && (
            <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", marginTop: 8 }}>
              <Field label="Correo" hint={modo === "nuevo" ? "Vacío = el correo de la empresa" : "Vacío = el correo del registro"}><input className="input" type="email" value={inv.email} placeholder={modo === "nuevo" ? f.email : ""} onChange={(e) => setInv({ ...inv, email: e.target.value })} /></Field>
              <Field label="Nombre (opcional)"><input className="input" value={inv.name} onChange={(e) => setInv({ ...inv, name: e.target.value })} /></Field>
              <Field label="Rol" hint="El comercial ve lo de su empresa e invita a sus técnicos"><select className="select" value={inv.role} onChange={(e) => setInv({ ...inv, role: e.target.value })}><option value="aliado">Aliado comercial</option><option value="tecnico_aliado">Técnico de aliado</option></select></Field>
            </div>
          )}
        </div>
      )}
      {dup && <p className="muted">Ya existe: <Link to={`/aliados/${dup.id}`} onClick={onClose}>{dup.name}</Link></p>}
    </Modal>
  );
}

function Ajustes() {
  const { toast, allows } = useSession();
  const [c, setC] = useState<Config | null>(null);
  const [mk, setMk] = useState("");
  useEffect(() => { api<Config>("/allies/config").then((x) => { setC(x); setMk(x.markup_pct ?? ""); }).catch(() => setC(null)); }, []);
  if (!c || !allows("aliados.precios") || !allows("catalog.costos")) return null;
  const guardar = async (json: Partial<Config>) => {
    try { const x = await api<Config>("/allies/config", { method: "PUT", json }); setC(x); setMk(x.markup_pct ?? ""); toast("Guardado"); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo guardar", "bad"); }
  };
  const ej = 100 * (1 + (Number(mk) || 0) / 100);
  return (
    <Card title="Precio y alcance para aliados">
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
        <Field label="Recargo del aliado sobre el costo (%)" hint={`Ejemplo: costo 100 → aliado ${ej.toLocaleString("es-CR", { maximumFractionDigits: 2 })}`}>
          <input className="input input--mono" style={{ maxWidth: 120 }} inputMode="decimal" value={mk} onChange={(e) => setMk(e.target.value)} onBlur={() => mk !== (c.markup_pct ?? "") && guardar({ markup_pct: mk === "" ? null : mk })} />
        </Field>
        <Field label="Activos que ve el aliado">
          <select className="select" value={c.assets_scope} onChange={(e) => guardar({ assets_scope: e.target.value as Config["assets_scope"] })}>
            <option value="proyectos">De proyectos en común</option>
            <option value="clientes">De clientes en común</option>
          </select>
        </Field>
      </div>
    </Card>
  );
}

/* Mensualidad de la plataforma (plan 1.4): con qué arranca cada plan nuevo. El precio lo fija Andrés; mientras tanto,
   fija por empresa y sin monto. La primera factura de cada plan la revisa una persona y las siguientes salen solas. */
function MensualidadAjustes() {
  const { toast, allows } = useSession();
  const [c, setC] = useState<Config | null>(null);
  const [monto, setMonto] = useState("");
  const [prod, setProd] = useState<LookupItem | null>(null);
  const fill = (x: Config) => { setC(x); setMonto(x.plan_default?.amount == null ? "" : String(x.plan_default.amount)); };
  useEffect(() => { api<Config>("/allies/config").then(fill).catch(() => setC(null)); }, []);
  if (!c || !allows("aliados.mensualidad")) return null;
  const guardar = async (json: Record<string, unknown>) => {
    try { fill(await api<Config>("/allies/config", { method: "PUT", json })); toast("Guardado"); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo guardar", "bad"); }
  };
  const pd: PlanDefault = c.plan_default || { amount: null, currency: "CRC", mode: "fija" };
  const actual = pd.amount == null ? "" : String(pd.amount);
  return (
    <Card title="Mensualidad de la plataforma">
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
        <Field label="Servicio facturado" hint={c.plan_product ? `${c.plan_product.code} · CABYS ${c.plan_product.cabys_code || "sin CABYS"}` : "Producto de tipo servicio con su CABYS"}>
          <Lookup value={prod?.label || c.plan_product?.name || ""} fetcher={searchProducts} onSelect={(it) => { if (!it) return; setProd(it); guardar({ plan_product_id: it.id }); }} placeholder="Buscar servicio" />
        </Field>
        <Field label="Monto por defecto (sin IVA)" hint="Vacío = se define en cada aliado">
          <input className="input input--mono" style={{ maxWidth: 140 }} inputMode="decimal" value={monto} onChange={(e) => setMonto(e.target.value)} onBlur={() => monto.trim() !== actual && guardar({ plan_default_amount: monto.trim() === "" ? null : monto.trim() })} />
        </Field>
        <Field label="Moneda">
          <select className="select" value={pd.currency} onChange={(e) => guardar({ plan_default_currency: e.target.value })}><option>CRC</option><option>USD</option></select>
        </Field>
        <Field label="Modo">
          <select className="select" value={pd.mode} onChange={(e) => guardar({ plan_default_mode: e.target.value })}><option value="fija">Fija por empresa</option><option value="por_usuario">Por usuario activo</option></select>
        </Field>
      </div>
      <p className="meta" style={{ marginBottom: 0 }}>Con esto arranca el cobro mensual de cada aliado nuevo (se puede cambiar en su ficha). La primera factura de cada plan espera tu «Aprobar y enviar» en Aprobaciones; las siguientes salen solas con su PDF y enlace de pago.</p>
    </Card>
  );
}

export default function Allies() {
  const { allows } = useSession();
  const [estado, setEstado] = useState<"activos" | "suspendidos" | "inactivos" | "todos">("activos");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<AllyRow[] | null>(null);
  const [nuevo, setNuevo] = useState(false);
  const load = useCallback(() => { api<AllyRow[]>(`/allies?estado=${estado}&q=${encodeURIComponent(q)}`).then(setRows).catch(() => setRows([])); }, [estado, q]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);
  return (
    <>
      <div className="page-head">
        <div><div className="meta">Administración</div><h1 className="h1">Aliados</h1></div>
        <div className="page-head__actions">
          {allows("aliados.crear") && <button className="btn btn--crimson" onClick={() => setNuevo(true)}><Icon d={I.plus} />Nuevo aliado</button>}
        </div>
      </div>
      <Ajustes />
      <MensualidadAjustes />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "12px 0" }}>
        {(["activos", "suspendidos", "inactivos", "todos"] as const).map((k) => <button key={k} className={`btn btn--sm ${estado === k ? "btn--crimson" : "btn--ghost"}`} onClick={() => setEstado(k)}>{k[0].toUpperCase() + k.slice(1)}</button>)}
        <input className="input" style={{ flex: "1 1 220px" }} placeholder="Buscar por nombre, cédula o correo" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {!rows ? <div className="spinner" /> : rows.length ? (
        <div style={{ overflowX: "auto" }}>
          <table className="table">
            <thead><tr><th>Aliado</th><th>Cédula</th><th>Estado</th><th>Acceso al portal</th><th>En común</th><th>Mensualidad</th></tr></thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.id}>
                  <td><Link to={`/aliados/${a.id}`}><b>{a.name}</b></Link>{a.email && <div className="meta">{a.email}</div>}</td>
                  <td>{a.tax_id || "—"}</td>
                  <td><span className={`badge badge--${STATE_TONE[a.state]}`}>{STATE_LABEL[a.state]}</span></td>
                  <td>{a.users_active > 0
                    ? <>{a.users_active} activo(s){a.seats_max ? ` de ${a.seats_max}` : ""}<div className="meta">{a.last_login ? `último ingreso ${fmtDate(a.last_login.slice(0, 10))}` : "todavía no entra"}{a.invitations_pending ? ` · ${a.invitations_pending} invitación(es) pendiente(s)` : ""}</div></>
                    : a.invitations_pending ? <span className="badge badge--warn">Invitación pendiente</span> : <span className="badge badge--muted">Sin usuarios</span>}</td>
                  <td>{a.proyectos_en_comun} proyecto(s) · {a.oportunidades_abiertas} oportunidad(es) abiertas</td>
                  <td>{a.plan.activo ? <span className={`badge badge--${a.plan.al_dia ? "ok" : "bad"}`}>{a.plan.al_dia ? "Al día" : "Vencida"}</span> : <span className="muted">Sin plan</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <Empty title="No hay aliados con ese filtro" hint="Un aliado también se puede crear desde la oportunidad, en «Aliados y participación»." />}
      {nuevo && <NewAlly onClose={() => setNuevo(false)} />}
    </>
  );
}
