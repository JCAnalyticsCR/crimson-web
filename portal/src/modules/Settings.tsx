/* Ajustes (plan 3.8): empresa, facturacion, pagos (metodos manuales, cuentas, pasarelas), usuarios (settings/UsersTab.tsx), cuenta/2FA, correo saliente. */
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, fmtDate } from "../lib/api";
import { useSession } from "../app/session";
import { Badge, Card, Empty, Field, I, Icon, Modal, Loading } from "../ui/components";
import { ImageField } from "../ui/MediaPicker";
import { SlaSettings } from "./support/SlaTable";
import { Bitacora } from "./Bitacora";
import DataExport from "./tools/DataExport";
import UsersTab from "./settings/UsersTab";
import HaciendaTab from "./settings/HaciendaTab";
import { MODE_LABEL, type HaciendaState } from "./accounting/hacienda";

type Group = { id: number; doc_type: string; prefix: string; branch: string; terminal: string; current: number; is_default: boolean };
type Cfg = { prevent_negative_stock?: boolean; reservation_auto_approve_units?: number; reservation_days?: number; labor_day_cost?: number; labor_day_cost_civil?: number; labor_day_cost_contratado?: number; per_diem_cost?: number; travel_cost?: number; max_discount_pct?: number; archive_purge_days?: number; invoice_valid_days: number; quote_valid_days: number; invoice_footer: string; quote_footer: string; notify_due: boolean; remind_days_before: number; daily_close_email: boolean; bcc: string[]; manual_payment_methods: { name: string; instructions: string; active: boolean }[]; activity_codes: string[]; einvoice_provider: string; phones: { number: string; kind: string; main: boolean }[]; social: Record<string, string> };
type Bank = { id: number; name: string; bank: string | null; currency: string; number: string | null; active: boolean };
type Gw = { id: number; provider: string; client_id: string | null; secret_mask: string | null; is_primary: boolean; active: boolean; mode: string; webhook_secret_set: boolean };
type OnvoStatus = { configured: boolean; source: "ajustes" | "entorno" | null; mode: "test" | "live" | null; webhook_secret: boolean; webhook_url: string; active: boolean; problems: string[] };
/* Llaves largas (onvo_test_publishable_key_…) rompian la tabla: prefijo + ultimos 4; la completa queda en el title. */
const shortKey = (k: string | null) => (!k ? "—" : k.length <= 24 ? k : `${k.slice(0, k.includes("_key_") ? k.indexOf("_key_") + 5 : 12)}…${k.slice(-4)}`);
type Mail = { id: number; to: string; subject: string; status: string; entity: string | null; entity_id: number | null; created_at: string; error: string | null; attempts?: number; next_attempt_at?: string | null; retrying?: boolean };
/* "se reintenta solo" solo si la API dice que el worker lo vuelve a intentar (15, 30, 60, 120, 240 min; al 6.º queda
   "fallido"). Un correo con adjuntos o una invitación que falla no se reintenta: "fallido" de una vez (se reenvía a
   mano). "reemplazado": se reenvió después y salió; este ya no cuenta. */
const MAIL_MAX_ATTEMPTS = 6;
const mailBadge = (m: Mail) => (m.status === "enviado" ? "confirmado" : m.status === "reemplazado" ? "descartado" : m.retrying ? "pendiente" : m.status === "error" || m.status === "fallido" ? "fallido" : "pendiente");
const mailError = (m: Mail) => m.error && m.status !== "enviado" && m.status !== "reemplazado";

/* Las claves no cambian (los enlaces ?tab= siguen sirviendo). "Soporte" chocaba con el menú Soporte (tickets) y
   Andrés creyó que era una conexión con Fygaro (03:31); la Bandeja XML la mezcló con el correo que sale (03:46). */
const TABS = [["empresa", "Empresa"], ["facturacion", "Facturación"], ["hacienda", "Hacienda"], ["pagos", "Pagos y cobros"], ["inventario", "Inventario"], ["usuarios", "Usuarios"], ["cuenta", "Mi cuenta"], ["correo", "Correo"], ["bandeja", "XML de proveedores"], ["sla", "SLA"], ["soporte", "Acceso de soporte"], ["api", "API"], ["bitacora", "Bitácora"], ["respaldo", "Respaldo"]];

export default function Settings() {
  const { me, reload, toast, allows } = useSession();
  const [params, setParams] = useSearchParams();
  const full = allows("settings.ver");
  // la bitacora y el acceso de soporte son solo de administracion (settings.configurar: sus endpoints lo exigen);
  // el resto de Ajustes con settings.ver
  const tabs = full ? TABS.filter(([k]) => (k !== "bitacora" && k !== "soporte") || allows("settings.configurar")) : TABS.filter(([k]) => k === "cuenta");
  const tab = full ? params.get("tab") || "empresa" : "cuenta";
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [banksSt, setBanks] = useState<Bank[] | null>(null); // null = cargando
  const banks = banksSt ?? [];
  const [gwsSt, setGws] = useState<Gw[] | null>(null); // null = cargando
  const gws = gwsSt ?? [];
  const [mailsSt, setMails] = useState<Mail[] | null>(null); // null = cargando
  const mails = mailsSt ?? [];
  const [company, setCompany] = useState({ name: "", legal_name: "", tax_id: "", default_currency: "CRC", sector: "", logo_url: "" });
  const [totp, setTotp] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [bank, setBank] = useState<{ id?: number; name: string; bank: string; currency: string; number: string; active: boolean } | null>(null);
  const [gw, setGw] = useState<{ provider: string; client_id: string; secret: string; webhook_secret: string; is_primary: boolean; active: boolean; mode: string } | null>(null);
  const [onvoSt, setOnvoSt] = useState<OnvoStatus | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; mode: string | null; detail: string } | null>(null);
  const [testBusy, setTestBusy] = useState(false);
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  /* Los codigos se muestran UNA sola vez: despues solo existen hasheados en la base. */
  const [recovery, setRecovery] = useState<string[] | null>(null);
  const [apagar, setApagar] = useState<{ password: string; regenerar: boolean } | null>(null);
  const [hmode, setHmode] = useState<HaciendaState["mode"] | null>(null);

  const load = () => {
    if (!allows("settings.ver")) return;
    api<Cfg>("/settings").then(setCfg); api<Group[]>("/billing-groups").then(setGroups); api<Bank[]>("/settings/bank-accounts").then(setBanks);
    api<Gw[]>("/settings/gateways").then(setGws); api<Mail[]>("/settings/outbox").then(setMails);
    api<OnvoStatus>("/settings/gateways/onvo/status").then(setOnvoSt).catch(() => {});
    api<HaciendaState>("/settings/hacienda").then((h) => setHmode(h.mode)).catch(() => setHmode(null));
  };
  const testOnvo = async () => {
    setTestBusy(true); setTestResult(null);
    try { const r = await api<{ ok: boolean; mode: "test" | "live" | null; detail: string }>("/settings/gateways/onvo/test", { method: "POST" }); setTestResult(r); }
    catch (e) { setTestResult({ ok: false, mode: null, detail: e instanceof Error ? e.message : "Error" }); }
    finally { setTestBusy(false); }
  };
  useEffect(load, []);
  useEffect(() => { if (me) setCompany({ name: me.tenant.name, legal_name: me.tenant.legal_name || "", tax_id: me.tenant.tax_id || "", default_currency: me.tenant.default_currency, sector: "", logo_url: me.tenant.logo_url || "" }); }, [me]);

  const ok = (m: string) => { toast(m); load(); };
  const err = (e: unknown) => toast(e instanceof Error ? e.message : "Error", "bad");
  const saveCompany = () => api("/tenant", { method: "PATCH", json: { ...company, logo_url: company.logo_url || null, sector: company.sector || null } }).then(async () => { await reload(); ok("Empresa actualizada"); }).catch(err);
  const saveCfg = (patch: Partial<Cfg>) => api<Cfg>("/settings", { method: "PUT", json: patch }).then((c) => { setCfg(c); toast("Ajustes guardados"); }).catch(err);
  const saveGroup = (g: Group) => api(`/settings/billing-groups/${g.id}`, { method: "PUT", json: { prefix: g.prefix, branch: g.branch, terminal: g.terminal, current: g.current, is_default: g.is_default } }).then(() => ok("Grupo actualizado")).catch(err);
  const saveBank = () => { if (!bank) return; const { id, ...b } = bank; api(id ? `/settings/bank-accounts/${id}` : "/settings/bank-accounts", { method: id ? "PUT" : "POST", json: b }).then(() => { setBank(null); ok("Cuenta guardada"); }).catch(err); };
  const saveGw = () => { if (!gw) return; api("/settings/gateways", { method: "PUT", json: { ...gw, secret: gw.secret || null, webhook_secret: gw.webhook_secret || null } }).then(() => { setGw(null); ok("Pasarela guardada"); }).catch(err); };
  const setup2fa = () => api<{ secret: string; otpauth_uri: string }>("/auth/2fa/setup", { method: "POST" }).then(setTotp).catch(err);
  const verify2fa = () => api<{ codes: string[] }>("/auth/2fa/verify", { method: "POST", json: { code } }).then(async (r) => { await reload(); setTotp(null); setCode(""); setRecovery(r.codes); toast("2FA activado"); }).catch(() => toast("Código inválido", "bad"));
  const disable2fa = () => { if (!apagar) return; api("/auth/2fa/disable", { method: "POST", json: { password: apagar.password } }).then(async () => { await reload(); setApagar(null); toast("2FA desactivado"); }).catch(err); };
  const regen2fa = () => { if (!apagar) return; api<{ codes: string[] }>("/auth/2fa/recovery-codes", { method: "POST", json: { password: apagar.password } }).then((r) => { setApagar(null); setRecovery(r.codes); toast("Códigos nuevos generados; los anteriores ya no sirven"); }).catch(err); };
  const changePw = () => api("/auth/password", { method: "POST", json: pw }).then(() => { setPw({ current_password: "", new_password: "" }); toast("Contraseña actualizada"); }).catch(err);

  return (
    <>
      <div className="page-head"><div><div className="meta">09 · Ajustes</div><h1 className="h1">Ajustes</h1></div></div>
      <div className="tabs" style={{ alignSelf: "flex-start" }}>{tabs.map(([k, l]) => <button key={k} className={tab === k ? "is-active" : ""} onClick={() => setParams({ tab: k })}>{l}</button>)}</div>

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
              <Field label="Logo" hint="Aparece en cotizaciones, facturas y la página de pago. Guardá para aplicarlo."><ImageField value={company.logo_url || null} label="Logo" onChange={(url) => setCompany({ ...company, logo_url: url || "" })} /></Field>
              <button className="btn btn--crimson" style={{ alignSelf: "flex-start" }} onClick={saveCompany}><Icon d={I.check} />Guardar</button>
            </div>
          </Card>
          {cfg && (
            <Card title="Teléfonos, actividad y redes">
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <Field label="Códigos de actividad económica (Hacienda)" hint="Se administran en Ajustes → Hacienda (con su descripción y cuál es la predeterminada)."><div className="input" style={{ display: "flex", alignItems: "center", gap: 8, background: "var(--bg-2)" }}><span className="mono">{cfg.activity_codes.join(", ") || "—"}</span><button className="btn btn--ghost btn--sm" style={{ marginLeft: "auto" }} onClick={() => setParams({ tab: "hacienda" })}>Ajustes → Hacienda</button></div></Field>
                <Field label="Teléfono principal (WhatsApp)"><input className="input" defaultValue={cfg.phones[0]?.number || ""} onBlur={(e) => saveCfg({ phones: [{ number: e.target.value, kind: "whatsapp", main: true }] })} /></Field>
                <div className="grid-3">
                  {(["facebook", "instagram", "linkedin"] as const).map((k) => <Field key={k} label={k[0].toUpperCase() + k.slice(1)}><input className="input" defaultValue={cfg.social[k] || ""} onBlur={(e) => saveCfg({ social: { ...cfg.social, [k]: e.target.value } })} /></Field>)}
                </div>
              </div>
            </Card>
          )}
        </div>
      )}

      {tab === "facturacion" && <FxCard />}
      {tab === "facturacion" && cfg && (
        <div className="grid-2">
          <Card title="Vigencias y mensajes">
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div className="grid-2">
                <Field label="Días de vigencia de factura"><input className="input input--mono" type="number" defaultValue={cfg.invoice_valid_days} onBlur={(e) => saveCfg({ invoice_valid_days: Number(e.target.value) })} /></Field>
                <Field label="Descuento máximo sin aprobación (%)" hint="Aplica a vendedores y caja. Más que eso: la cotización queda por aprobar y la factura o el POS se rechazan."><input className="input input--mono" type="number" min={0} max={100} defaultValue={cfg.max_discount_pct ?? 10} onBlur={(e) => saveCfg({ max_discount_pct: Number(e.target.value) })} /></Field>
                <Field label="Días en la papelera antes de borrar" hint="Levantamientos, proyectos, oportunidades y órdenes que se mandan a la papelera se borran solos después de estos días (Administración › Archivo)."><input className="input input--mono" type="number" min={1} max={365} defaultValue={cfg.archive_purge_days ?? 30} onBlur={(e) => saveCfg({ archive_purge_days: Number(e.target.value) })} /></Field>
                <Field label="Días de vigencia de cotización"><input className="input input--mono" type="number" defaultValue={cfg.quote_valid_days} onBlur={(e) => saveCfg({ quote_valid_days: Number(e.target.value) })} /></Field>
              </div>
              <Field label="Mensaje al pie de toda factura"><textarea className="textarea" defaultValue={cfg.invoice_footer} onBlur={(e) => saveCfg({ invoice_footer: e.target.value })} /></Field>
              <Field label="Mensaje al pie de cotizaciones" hint="Ideal para instrucciones de pago y cuentas bancarias."><textarea className="textarea" defaultValue={cfg.quote_footer} onBlur={(e) => saveCfg({ quote_footer: e.target.value })} /></Field>
              <Field label="Factura electrónica (Hacienda)" hint="El modo (desactivada, simulado, pruebas o producción) se cambia en Ajustes → Hacienda.">
                <div className="input" style={{ display: "flex", alignItems: "center", gap: 8, background: "var(--bg-2)" }}><b>{hmode ? MODE_LABEL[hmode] : "—"}</b><button className="btn btn--ghost btn--sm" style={{ marginLeft: "auto" }} onClick={() => setParams({ tab: "hacienda" })}>Configurar en Ajustes → Hacienda</button></div>
              </Field>
            </div>
          </Card>
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {/* Precio de mano de obra por defecto. Cada levantamiento lo puede ajustar en su costeo sin tocar esto. */}
            <Card title="Mano de obra (costo por persona por día)">
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div className="grid-2">
                  <Field label="Personal técnico (₡)"><input className="input input--mono" inputMode="decimal" defaultValue={cfg.labor_day_cost ?? 25000} onBlur={(e) => saveCfg({ labor_day_cost: Number(e.target.value) })} /></Field>
                  <Field label="Personal de obra civil (₡)" hint="Vacío: igual que el técnico."><input className="input input--mono" inputMode="decimal" defaultValue={cfg.labor_day_cost_civil ?? ""} onBlur={(e) => e.target.value.trim() && saveCfg({ labor_day_cost_civil: Number(e.target.value) })} /></Field>
                  <Field label="Personal contratado (₡)" hint="Vacío: igual que el técnico."><input className="input input--mono" inputMode="decimal" defaultValue={cfg.labor_day_cost_contratado ?? ""} onBlur={(e) => e.target.value.trim() && saveCfg({ labor_day_cost_contratado: Number(e.target.value) })} /></Field>
                  <Field label="Viáticos por persona por día (₡)" hint="Alimentación."><input className="input input--mono" inputMode="decimal" defaultValue={cfg.per_diem_cost ?? 0} onBlur={(e) => saveCfg({ per_diem_cost: Number(e.target.value) })} /></Field>
                  <Field label="Transporte por trabajo (₡)"><input className="input input--mono" inputMode="decimal" defaultValue={cfg.travel_cost ?? 35000} onBlur={(e) => saveCfg({ travel_cost: Number(e.target.value) })} /></Field>
                </div>
                <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Son costos internos, sin IVA. El precio al cliente sale con el margen del costeo. En cada levantamiento se pueden ajustar solo para ese trabajo.</p>
              </div>
            </Card>
            <Card title="Notificaciones">
              <div style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 13 }}>
                <label style={{ display: "flex", gap: 10 }}><input type="checkbox" checked={cfg.notify_due} onChange={(e) => saveCfg({ notify_due: e.target.checked })} />Notificar vencimiento al cliente</label>
                <Field label="¿Cuántos días antes recordar?"><input className="input input--mono" type="number" defaultValue={cfg.remind_days_before} onBlur={(e) => saveCfg({ remind_days_before: Number(e.target.value) })} style={{ width: 120 }} /></Field>
                <label style={{ display: "flex", gap: 10 }}><input type="checkbox" checked={cfg.daily_close_email} onChange={(e) => saveCfg({ daily_close_email: e.target.checked })} />Enviar reporte de cierre de caja diario</label>
                <Field label="Correos en copia (BCC) de toda factura" hint="Separados por coma."><input className="input" defaultValue={cfg.bcc.join(", ")} onBlur={(e) => saveCfg({ bcc: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} /></Field>
              </div>
            </Card>
            <Card title="Numeración visible de documentos" flush>
              <table className="table"><thead><tr><th>Tipo</th><th>Prefijo</th><th>Sucursal</th><th>Terminal</th><th className="num">Actual</th><th /></tr></thead>
                <tbody>{groups.filter((g) => !/^[FT]0[1-7]$/.test(g.doc_type)).map((g) => <tr key={g.id}><td style={{ fontWeight: 600 }}>{g.doc_type}</td><td><input className="input" style={{ height: 32, width: 80 }} value={g.prefix} onChange={(e) => setGroups(groups.map((x) => (x.id === g.id ? { ...x, prefix: e.target.value } : x)))} /></td><td><input className="input input--mono" style={{ height: 32, width: 64 }} value={g.branch} onChange={(e) => setGroups(groups.map((x) => (x.id === g.id ? { ...x, branch: e.target.value } : x)))} /></td><td><input className="input input--mono" style={{ height: 32, width: 84 }} value={g.terminal} onChange={(e) => setGroups(groups.map((x) => (x.id === g.id ? { ...x, terminal: e.target.value } : x)))} /></td><td className="num"><input className="input input--mono" style={{ height: 32, width: 90 }} value={g.current} onChange={(e) => setGroups(groups.map((x) => (x.id === g.id ? { ...x, current: Number(e.target.value) } : x)))} /></td><td className="num"><button className="btn btn--ghost btn--sm" onClick={() => saveGroup(g)}>Guardar</button></td></tr>)}</tbody></table>
              <p className="muted" style={{ padding: "10px 18px", fontSize: 12 }}>El número que se ve en el documento (FEC-000513). El consecutivo fiscal de 20 dígitos se asigna al emitir y se administra en Ajustes → Hacienda → Consecutivos. El valor actual nunca retrocede.</p>
            </Card>
          </div>
        </div>
      )}

      {tab === "inventario" && cfg && (
        <div className="grid-2">
          <Card title="Existencias">
            <div style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 13 }}>
              <label style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                <input type="checkbox" checked={!!cfg.prevent_negative_stock} onChange={(e) => saveCfg({ prevent_negative_stock: e.target.checked })} style={{ marginTop: 3 }} />
                <span><b>Impedir stock negativo en salidas</b><br /><span className="muted">Apagado (recomendado mientras se cuadra el inventario): ventas, punto de venta, órdenes de trabajo y ajustes descuentan aunque no haya existencia, como hasta hoy. Encendido: cualquier salida sin existencia suficiente en esa bodega se rechaza.</span></span>
              </label>
            </div>
          </Card>
          <Card title="Reservas de material">
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <p className="muted" style={{ fontSize: 13, margin: 0 }}>Ver existencia no es retirar: una reserva compromete material solo cuando la aprueba un administrador o supervisor. Al confirmar se valida que haya existencia libre.</p>
              <div className="grid-2">
                <Field label="Aprobar solas las reservas de proyectos hasta (unidades)" hint="0 = toda reserva requiere aprobación."><input className="input input--mono" type="number" min={0} defaultValue={cfg.reservation_auto_approve_units ?? 0} onBlur={(e) => saveCfg({ reservation_auto_approve_units: Number(e.target.value || 0) })} /></Field>
                <Field label="Vigencia de una reserva (días)" hint="Al vencer se libera sola."><input className="input input--mono" type="number" min={1} max={365} defaultValue={cfg.reservation_days ?? 15} onBlur={(e) => saveCfg({ reservation_days: Number(e.target.value || 15) })} /></Field>
              </div>
            </div>
          </Card>
        </div>
      )}

      {tab === "pagos" && cfg && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div className="grid-2 grid-2--even">
          <Card title="Métodos de pago personalizados" extra={<button className="btn btn--ghost btn--sm" onClick={() => saveCfg({ manual_payment_methods: [...cfg.manual_payment_methods, { name: "Nuevo método", instructions: "", active: true }] })}><Icon d={I.plus} />Agregar</button>}>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {cfg.manual_payment_methods.map((m, i) => (
                <div key={i} className="pay__method" style={{ gap: 8 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}><input className="input" style={{ height: 34, fontWeight: 600, flex: 1, minWidth: 0 }} defaultValue={m.name} onBlur={(e) => saveCfg({ manual_payment_methods: cfg.manual_payment_methods.map((x, k) => (k === i ? { ...x, name: e.target.value } : x)) })} /><label style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center", whiteSpace: "nowrap" }}><input type="checkbox" checked={m.active} onChange={(e) => saveCfg({ manual_payment_methods: cfg.manual_payment_methods.map((x, k) => (k === i ? { ...x, active: e.target.checked } : x)) })} />Activo</label><button className="x" onClick={() => saveCfg({ manual_payment_methods: cfg.manual_payment_methods.filter((_, k) => k !== i) })}><Icon d={I.x} size={14} /></button></div>
                  <textarea className="textarea" style={{ minHeight: 56 }} defaultValue={m.instructions} placeholder="Instrucciones de pago que verá el cliente" onBlur={(e) => saveCfg({ manual_payment_methods: cfg.manual_payment_methods.map((x, k) => (k === i ? { ...x, instructions: e.target.value } : x)) })} />
                </div>
              ))}
            </div>
          </Card>
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <Card title="Cuentas bancarias" flush extra={<button className="btn btn--ghost btn--sm" onClick={() => setBank({ name: "", bank: "", currency: "CRC", number: "", active: true })}><Icon d={I.plus} />Agregar</button>}>
              {banksSt === null ? <Loading /> : banks.length === 0 ? <Empty hint="Se vinculan a los pagos registrados." /> : <div style={{ overflowX: "auto" }}><table className="table"><thead><tr><th>Nombre</th><th>Banco</th><th>Divisa</th><th>Número</th><th /></tr></thead><tbody>{banks.map((b) => <tr key={b.id}><td style={{ fontWeight: 600 }}>{b.name}</td><td className="muted">{b.bank || "—"}</td><td>{b.currency}</td><td className="mono muted">{b.number || "—"}</td><td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setBank({ id: b.id, name: b.name, bank: b.bank || "", currency: b.currency, number: b.number || "", active: b.active })}>Ver</button></td></tr>)}</tbody></table></div>}
            </Card>
          </div>
        </div>
        <Card title="Pasarelas de pago" flush extra={<button className="btn btn--ghost btn--sm" onClick={() => setGw({ provider: "onvo", client_id: "", secret: "", webhook_secret: "", is_primary: true, active: false, mode: "test" })}><Icon d={I.plus} />Agregar</button>}>
              {gwsSt === null ? <Loading /> : gws.length === 0 ? <Empty hint="ONVO Pay (SINPE Móvil + tarjetas) y PayPal. El secreto se guarda cifrado y nunca vuelve al navegador." /> : <div style={{ overflowX: "auto" }}><table className="table"><thead><tr><th>Pasarela</th><th>Llave pública</th><th>Secreto</th><th>Modo</th><th>Webhook</th><th>Estado</th><th /></tr></thead><tbody>{gws.map((g) => <tr key={g.id}><td style={{ fontWeight: 600, textTransform: "uppercase" }}>{g.provider}{g.is_primary && <span className="meta"> · principal</span>}</td><td className="mono muted" title={g.client_id || undefined}>{shortKey(g.client_id)}</td><td className="mono muted" style={{ whiteSpace: "nowrap" }}>{g.secret_mask || "—"}</td><td className="muted">{g.mode === "live" ? "Producción" : "Pruebas"}</td><td className="muted">{g.webhook_secret_set ? "Webhook ✓" : "sin secreto"}</td><td><Badge status={g.active ? "confirmado" : "pendiente"} /></td><td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setGw({ provider: g.provider, client_id: g.client_id || "", secret: "", webhook_secret: "", is_primary: g.is_primary, active: g.active, mode: g.mode })}>Editar</button></td></tr>)}</tbody></table></div>}
              <div style={{ padding: "12px 18px", display: "flex", flexDirection: "column", gap: 8, fontSize: 12.5 }}>
                <div><span className="meta">Estado ONVO · </span>{onvoSt?.configured ? <span style={{ color: "var(--ok)" }}>Conectado en modo {onvoSt.mode === "live" ? "Producción" : "Pruebas"} · desde {onvoSt.source === "ajustes" ? "Ajustes" : "variable de entorno"}</span> : <span className="muted">Sin conectar</span>}</div>
                {onvoSt?.webhook_url && <div style={{ display: "flex", gap: 8, alignItems: "center" }}><input className="input input--mono" style={{ textAlign: "left", height: 32, fontSize: 12, flex: 1, minWidth: 0 }} readOnly value={onvoSt.webhook_url} /><button className="btn btn--ghost btn--sm" onClick={() => { navigator.clipboard.writeText(onvoSt!.webhook_url); toast("URL copiada"); }}>Copiar</button></div>}
                <p className="muted" style={{ margin: 0 }}>Registrala en ONVO → Desarrolladores → Webhooks y pegá aquí su secreto (<span className="mono">webhook_secret_…</span>).</p>
                {onvoSt?.problems.map((p, i) => <p key={i} style={{ color: "var(--warn)", margin: 0 }}>{p}</p>)}
                {onvoSt?.configured && <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 2 }}><button className="btn btn--soft btn--sm" onClick={testOnvo} disabled={testBusy}>{testBusy ? "Probando…" : "Probar conexión"}</button>{testResult && <span style={{ color: testResult.ok ? "var(--ok)" : "var(--bad)", fontSize: 12.5 }}>{testResult.ok ? `Conectado (modo ${testResult.mode === "live" ? "Producción" : "Pruebas"})` : testResult.detail}</span>}</div>}
              </div>
        </Card>
        </div>
      )}

      {tab === "usuarios" && <UsersTab />}

      {tab === "cuenta" && (
        <div className="grid-2">
          <Card title="Autenticación de dos factores">
            {me?.user.totp_enabled ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <p className="muted">2FA <b style={{ color: "var(--ok)" }}>activo</b> para {me.user.email}.</p>
                <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Si perdés el teléfono, entrás con uno de tus códigos de recuperación en lugar del código de seis dígitos.</p>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button className="btn btn--ghost btn--sm" onClick={() => setApagar({ password: "", regenerar: true })}>Generar códigos nuevos</button>
                  <button className="btn btn--danger btn--sm" onClick={() => setApagar({ password: "", regenerar: false })}>Desactivar 2FA</button>
                </div>
              </div>
            ) : totp ? (
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
          <SessionCard />
        </div>
      )}

      {tab === "correo" && (
        <><MailStatus onSent={() => api<Mail[]>("/settings/outbox").then(setMails)} />
        <Card title="Correos enviados" flush>
          {mailsSt === null ? <Loading /> : mails.length === 0 ? <Empty hint="Cotizaciones, facturas, invitaciones y recordatorios enviados aparecen aquí." /> : <table className="table"><thead><tr><th>Fecha</th><th>Para</th><th>Asunto</th><th>Estado</th></tr></thead><tbody>{mails.map((m) => <tr key={m.id}><td className="muted">{fmtDate(m.created_at.slice(0, 10))}</td><td>{m.to}</td><td>{m.subject}{mailError(m) && <div className="meta" style={{ color: "var(--bad)" }}>{m.error}</div>}</td><td><Badge status={mailBadge(m)} />{m.status === "simulado" && <div className="meta">no salió</div>}{m.retrying && <div className="meta">{m.attempts || 1} de {MAIL_MAX_ATTEMPTS} intentos · se reintenta solo</div>}{m.status === "reemplazado" && <div className="meta">se reenvió después</div>}</td></tr>)}</tbody></table>}
        </Card></>
      )}

      {tab === "api" && <ApiCreds />}
      {tab === "hacienda" && <HaciendaTab />}
      {tab === "bandeja" && <InboxSettings />}
      {tab === "sla" && <SlaSettings />}
      {tab === "soporte" && allows("settings.configurar") && <SupportAccess />}
      {tab === "bitacora" && allows("settings.configurar") && <Bitacora />}
      {tab === "respaldo" && <DataExport />}

      {recovery && (
        <Modal
          title="Guardá estos códigos ahora"
          onClose={() => setRecovery(null)}
          foot={<>
            <button className="btn btn--ghost" onClick={() => { navigator.clipboard.writeText(recovery.join("\n")); toast("Códigos copiados"); }}><Icon d={I.copy} />Copiar</button>
            <button className="btn btn--ghost" onClick={() => {
              const texto = `Códigos de recuperación · Crimson · ${me?.user.email}\nGenerados el ${new Date().toLocaleString("es-CR")}\n\n${recovery.join("\n")}\n\nCada código sirve UNA sola vez.\n`;
              const a = document.createElement("a");
              a.href = URL.createObjectURL(new Blob([texto], { type: "text/plain" }));
              a.download = "crimson-codigos-recuperacion.txt";
              a.click();
            }}>Descargar</button>
            <button className="btn btn--crimson" onClick={() => setRecovery(null)}>Ya los guardé</button>
          </>}
        >
          <p style={{ fontSize: 14, marginTop: 0 }}>Esta es la <b>única vez</b> que se muestran. Si perdés el teléfono, con uno de estos entrás al panel; después podés desactivar el 2FA desde esta misma pantalla.</p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 8, margin: "14px 0" }}>
            {recovery.map((c) => <code key={c} className="mono" style={{ padding: "10px 12px", background: "var(--bg-2)", borderRadius: 8, textAlign: "center", fontSize: 15, letterSpacing: ".05em" }}>{c}</code>)}
          </div>
          <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Cada uno sirve una sola vez. Guardalos en tu gestor de contraseñas o impresos, no en el mismo teléfono que tiene la app.</p>
        </Modal>
      )}

      {apagar && (
        <Modal
          title={apagar.regenerar ? "Generar códigos nuevos" : "Desactivar 2FA"}
          onClose={() => setApagar(null)}
          foot={<>
            <button className="btn btn--ghost" onClick={() => setApagar(null)}>Cancelar</button>
            {apagar.regenerar
              ? <button className="btn btn--crimson" onClick={regen2fa} disabled={!apagar.password}>Generar</button>
              : <button className="btn btn--danger" onClick={disable2fa} disabled={!apagar.password}>Desactivar</button>}
          </>}
        >
          <p className="muted" style={{ fontSize: 13.5, marginTop: 0 }}>
            {apagar.regenerar
              ? "Los códigos que tenías dejan de servir en el momento en que se generan los nuevos."
              : "La cuenta vuelve a entrar solo con la contraseña. Si esa contraseña ya circuló por algún lado, mejor cambiala primero."}
          </p>
          <Field label="Confirmá con tu contraseña"><input className="input" type="password" autoFocus value={apagar.password} onChange={(e) => setApagar({ ...apagar, password: e.target.value })} onKeyDown={(e) => { if (e.key === "Enter" && apagar.password) (apagar.regenerar ? regen2fa : disable2fa)(); }} /></Field>
        </Modal>
      )}

      {bank && <Modal title={bank.id ? "Cuenta bancaria" : "Nueva cuenta"} onClose={() => setBank(null)} foot={<><button className="btn btn--ghost" onClick={() => setBank(null)}>Cancelar</button><button className="btn btn--crimson" onClick={saveBank}>Guardar</button></>}>
        <div className="grid-2"><Field label="Nombre"><input className="input" value={bank.name} onChange={(e) => setBank({ ...bank, name: e.target.value })} /></Field><Field label="Banco"><input className="input" value={bank.bank} onChange={(e) => setBank({ ...bank, bank: e.target.value })} /></Field><Field label="Divisa"><select className="select" value={bank.currency} onChange={(e) => setBank({ ...bank, currency: e.target.value })}><option>CRC</option><option>USD</option></select></Field><Field label="Número / IBAN"><input className="input input--mono" style={{ textAlign: "left" }} value={bank.number} onChange={(e) => setBank({ ...bank, number: e.target.value })} /></Field></div>
      </Modal>}
      {gw && <Modal title="Pasarela de pago" onClose={() => setGw(null)} foot={<><button className="btn btn--ghost" onClick={() => setGw(null)}>Cancelar</button><button className="btn btn--crimson" onClick={saveGw}>Guardar</button></>}>
        <div className="grid-2">
          <Field label="Pasarela"><select className="select" value={gw.provider} onChange={(e) => setGw({ ...gw, provider: e.target.value })}><option value="onvo">ONVO Pay</option><option value="paypal">PayPal</option></select></Field>
          {gw.provider === "onvo" ? (<>
            <Field label="Llave secreta" hint="Se cifra en el servidor y nunca vuelve al navegador. Vacío = no cambiar."><input className="input" type="password" placeholder="onvo_test_secret_key_…" value={gw.secret} onChange={(e) => setGw({ ...gw, secret: e.target.value })} /></Field>
            <Field label="Llave publicable (opcional)"><input className="input" placeholder="onvo_test_publishable_key_…" value={gw.client_id} onChange={(e) => setGw({ ...gw, client_id: e.target.value })} /></Field>
            <Field label="Secreto del webhook" hint="Vacío = no cambiar."><input className="input" type="password" placeholder="webhook_secret_…" value={gw.webhook_secret} onChange={(e) => setGw({ ...gw, webhook_secret: e.target.value })} /></Field>
          </>) : (<>
            <Field label="Client ID"><input className="input" value={gw.client_id} onChange={(e) => setGw({ ...gw, client_id: e.target.value })} /></Field>
            <Field label="Client Secret" hint="Se cifra en el servidor. Dejar vacío para no cambiarlo."><input className="input" type="password" value={gw.secret} onChange={(e) => setGw({ ...gw, secret: e.target.value })} /></Field>
            <Field label="Modo"><select className="select" value={gw.mode} onChange={(e) => setGw({ ...gw, mode: e.target.value })}><option value="test">Pruebas</option><option value="live">Producción</option></select></Field>
          </>)}
        </div>
        <div style={{ display: "flex", gap: 16, fontSize: 13 }}><label style={{ display: "flex", gap: 8 }}><input type="checkbox" checked={gw.is_primary} onChange={(e) => setGw({ ...gw, is_primary: e.target.checked })} />Principal</label><label style={{ display: "flex", gap: 8 }}><input type="checkbox" checked={gw.active} onChange={(e) => setGw({ ...gw, active: e.target.checked })} />Activa</label></div>
      </Modal>}
    </>
  );
}

type Cred = { id: number; kid: string; name: string; webhook_url: string | null; active: boolean; last_used_at: string | null; created_at: string };
function ApiCreds() {
  const { toast } = useSession();
  const [itemsSt, setItems] = useState<Cred[] | null>(null); // null = cargando
  const items = itemsSt ?? [];
  const [form, setForm] = useState<{ name: string; webhook_url: string } | null>(null);
  const [created, setCreated] = useState<{ kid: string; secret: string } | null>(null);
  const load = () => api<Cred[]>("/settings/api-credentials").then(setItems);
  useEffect(() => { load(); }, []);
  const create = () => { if (!form) return; api<{ kid: string; secret: string }>("/settings/api-credentials", { method: "POST", json: { name: form.name, webhook_url: form.webhook_url || null } }).then((r) => { setCreated(r); setForm(null); load(); }).catch((e) => toast(e.message, "bad")); };
  const revoke = (c: Cred) => { if (!confirm(`¿Revocar ${c.kid}? Las integraciones que la usen dejan de funcionar.`)) return; api(`/settings/api-credentials/${c.id}`, { method: "DELETE" }).then(() => { toast("Credencial revocada"); load(); }); };
  return (
    <div className="grid-2">
      <Card title="Credenciales de API" flush extra={<button className="btn btn--crimson btn--sm" onClick={() => setForm({ name: "", webhook_url: "" })}><Icon d={I.plus} />Crear</button>}>
        <p className="muted" style={{ fontSize: 12.5, margin: 0, padding: "10px 18px 0" }}>Solo sirve para conectar otros sistemas (la página web, un ERP, Power BI). Si no estás conectando nada, no hace falta tocarla.</p>
        {itemsSt === null ? <Loading /> : items.length === 0 ? <Empty hint="Para integrar el sitio web, un ERP o Power BI con la plataforma." /> : (
          <table className="table"><thead><tr><th>Nombre</th><th>Public key</th><th>Último uso</th><th>Estado</th><th /></tr></thead>
            <tbody>{items.map((c) => <tr key={c.id}><td style={{ fontWeight: 600 }}>{c.name}{c.webhook_url && <div className="meta" style={{ textTransform: "none" }}>{c.webhook_url}</div>}</td><td className="mono muted">{c.kid}</td><td className="muted">{c.last_used_at ? fmtDate(c.last_used_at.slice(0, 10)) : "nunca"}</td><td><Badge status={c.active ? "confirmado" : "anulada"} /></td><td className="num">{c.active && <button className="btn btn--danger btn--sm" onClick={() => revoke(c)}>Revocar</button>}</td></tr>)}</tbody></table>
        )}
      </Card>
      <Card title="Cómo usarla">
        <div style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 13 }}>
          <p className="muted">Encabezados <span className="mono">X-Api-Key</span> (public key) y <span className="mono">X-Api-Secret</span>. Endpoints:</p>
          <code className="mono" style={{ display: "block", maxWidth: "100%", overflowX: "auto", padding: 12, background: "var(--bg-2)", borderRadius: 10, fontSize: 12, lineHeight: 1.7, whiteSpace: "pre" }}>{`GET  /api/v1/customers · POST /api/v1/customers
GET  /api/v1/products  · GET  /api/v1/inventory
GET  /api/v1/invoices  · POST /api/v1/invoices
POST /api/v1/payment-links/{invoice_id}
POST /api/v1/checkout   { token: JWT HS256, kid }`}</code>
          <p className="muted">Checkout: firmá un JWT con el secreto (header <span className="mono">kid</span>) y los campos <span className="mono">amount, currency, custom_reference, exp</span>. Devuelve la URL de pago. Los webhooks salientes van firmados <span className="mono">X-Signature: t=…,v1=…</span>.</p>
        </div>
      </Card>
      {form && <Modal title="Nueva credencial" onClose={() => setForm(null)} foot={<><button className="btn btn--ghost" onClick={() => setForm(null)}>Cancelar</button><button className="btn btn--crimson" onClick={create}>Crear</button></>}>
        <Field label="Nombre"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Sitio web, ERP…" /></Field>
        <Field label="Webhook (opcional)" hint="Recibe eventos firmados."><input className="input" value={form.webhook_url} onChange={(e) => setForm({ ...form, webhook_url: e.target.value })} placeholder="https://…" /></Field>
      </Modal>}
      {created && <Modal title="Credencial creada" onClose={() => setCreated(null)} foot={<button className="btn btn--crimson" onClick={() => setCreated(null)}>Listo, la guardé</button>}>
        <p style={{ color: "var(--bad)", fontSize: 13, fontWeight: 600 }}>El secreto se muestra una sola vez. Guardalo en un gestor de contraseñas; no se puede recuperar.</p>
        <Field label="Public key"><input className="input input--mono" style={{ textAlign: "left" }} readOnly value={created.kid} /></Field>
        <Field label="Secret"><input className="input input--mono" style={{ textAlign: "left" }} readOnly value={created.secret} onFocus={(e) => e.currentTarget.select()} /></Field>
      </Modal>}
    </div>
  );
}

/* ---------- Bandeja IMAP: XML de proveedores entran solos a Recepcion ---------- */
type Inbox = { enabled: boolean; host: string; port: number; user: string; folder: string; has_password: boolean; last_run: string | null; last_result: { messages: number; nuevos: number; duplicados: number; ignorados: number; errores: string[] } | null; source?: "env" | "ajustes"; env_mailbox?: string };
function InboxSettings() {
  const { toast, allows } = useSession();
  const navigate = useNavigate();
  const [cfg, setCfg] = useState<Inbox | null>(null);
  const [saved, setSaved] = useState<Inbox | null>(null);
  const [password, setPassword] = useState("");
  const [adminPw, setAdminPw] = useState("");
  const [busy, setBusy] = useState(false);
  const loaded = (c: Inbox) => { setCfg(c); setSaved(c); };
  useEffect(() => { api<Inbox>("/settings/inbox").then(loaded); }, []);
  if (!cfg) return <span className="spinner" />;
  // otro servidor, puerto o usuario: la contrasena guardada no viaja sola (se vuelve a escribir) y, como guardar una
  // contrasena, pide la del administrador (la API responde 422 / 403 si falta)
  const conexion = !!saved && (cfg.host.trim() !== saved.host || Number(cfg.port) !== Number(saved.port) || cfg.user.trim() !== saved.user);
  const pideClave = conexion && !!saved?.has_password && !password;
  const pideAdmin = !!password || conexion;
  const save = async () => {
    try {
      loaded(await api<Inbox>("/settings/inbox", { method: "PUT", json: { enabled: cfg.enabled, host: cfg.host, port: Number(cfg.port), user: cfg.user, folder: cfg.folder, password: password || null, admin_password: pideAdmin ? adminPw : null } }));
      setPassword(""); setAdminPw(""); toast("Bandeja guardada");
    }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const run = async () => {
    setBusy(true);
    try { const r = await api<{ messages: number; nuevos: number; duplicados: number; errores: string[] }>("/settings/inbox/run", { method: "POST" }); toast(`${r.messages} correos revisados · ${r.nuevos} comprobantes nuevos${r.errores.length ? ` · ${r.errores.length} con error` : ""}`); loaded(await api<Inbox>("/settings/inbox")); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  const activo = cfg.enabled && cfg.has_password;
  if (cfg.source === "env") {
    return (
      <Card title="Buzón de facturas de proveedores" extra={<span className="meta" style={{ color: "var(--ok)" }}>● Activo · revisa cada 15 min</span>}>
        <p style={{ fontSize: 13, marginTop: 0 }}>Configurado en el servidor: <span className="mono">{cfg.env_mailbox}</span>. Se cambia en las variables del servidor (RECEPTION_IMAP_*), no aquí.</p>
        {allows("accounting.ver") && <button className="btn btn--soft btn--sm" onClick={() => navigate("/contabilidad/facturas/correos")}>Ver los correos recibidos</button>}
      </Card>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
    <Card title="¿Para qué sirve esta pestaña?">
      <ol className="muted" style={{ fontSize: 13, lineHeight: 1.7, paddingLeft: 18, margin: 0 }}>
        <li>Para <b>recibir</b> solas las facturas electrónicas (XML) que te mandan tus <b>proveedores</b>: lo que Crimson compra (equipos, materiales, servicios).</li>
        <li>La plataforma revisa cada 15 minutos un buzón de correo dedicado; cada comprobante que llega (XML, PDF y la respuesta de Hacienda) aparece en <b>Contabilidad → Facturas</b>, listo para responderlo ante Hacienda y registrarlo como gasto. Nunca marca los correos como leídos.</li>
        <li><b>No envía correos ni tiene que ver con Resend.</b> El correo que sale de la plataforma (invitaciones, cotizaciones, facturas) se configura en la pestaña <b>Correo</b>.</li>
        <li><b>Es opcional.</b> Mientras no lo configurés, podés cargar los archivos a mano en Contabilidad → Facturas.</li>
      </ol>
      {allows("accounting.ver") && <button className="btn btn--soft btn--sm" style={{ marginTop: 12 }} onClick={() => navigate("/contabilidad/facturas")}>Abrir Contabilidad → Facturas</button>}
    </Card>
    <div className="grid-2">
      <Card title="Buzón de facturas de proveedores" extra={<span className="meta" style={{ color: activo ? "var(--ok)" : "var(--text-3)" }}>{activo ? "● Activo · revisa cada 15 min" : "● Apagado"}</span>}>
        <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13, marginBottom: 12 }}><input type="checkbox" checked={cfg.enabled} onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })} />Revisar automáticamente cada 15 minutos</label>
        <div className="grid-2">
          <Field label="Servidor IMAP"><input className="input" placeholder="mail.tudominio.com o imap.gmail.com" value={cfg.host} onChange={(e) => setCfg({ ...cfg, host: e.target.value })} /></Field>
          <Field label="Puerto (SSL)"><input className="input input--mono" value={cfg.port} onChange={(e) => setCfg({ ...cfg, port: Number(e.target.value) || 993 })} /></Field>
          <Field label="Usuario"><input className="input" placeholder="facturas@empresa.com" value={cfg.user} onChange={(e) => setCfg({ ...cfg, user: e.target.value })} /></Field>
          <Field label="Contraseña de aplicación" hint={pideClave ? "Cambiaste el servidor, el puerto o el usuario: volvé a escribirla." : cfg.has_password ? "Guardada cifrada. Dejá vacío para conservarla." : "Se guarda cifrada."}><input className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          <Field label="Carpeta"><input className="input" value={cfg.folder} onChange={(e) => setCfg({ ...cfg, folder: e.target.value })} /></Field>
          {pideAdmin && <Field label="Tu contraseña de administrador" hint="Se pide al guardar la contraseña del buzón o al cambiar el servidor."><input className="input" type="password" autoComplete="current-password" value={adminPw} onChange={(e) => setAdminPw(e.target.value)} /></Field>}
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <button className="btn btn--crimson" onClick={save} disabled={pideClave || (pideAdmin && !adminPw)}>Guardar</button>
          <button className="btn btn--soft" onClick={run} disabled={busy || !cfg.has_password}>{busy ? "Revisando…" : "Revisar ahora"}</button>
        </div>
      </Card>
      <Card title="Cómo funciona">
        <ol className="muted" style={{ fontSize: 13, lineHeight: 1.7, paddingLeft: 18, margin: 0 }}>
          <li>Creá un buzón dedicado (p. ej. <span className="mono">facturas@tuempresa.com</span>) y pedile a tus proveedores que envíen ahí sus facturas electrónicas.</li>
          <li>En Gmail o Microsoft 365 generá una <b>contraseña de aplicación</b>; no uses la contraseña principal.</li>
          <li>Cada correo nuevo con XML, PDF o zip crea el comprobante en <b>Contabilidad → Facturas</b>, listo para aceptar y convertir en gasto. La respuesta de Hacienda y el PDF se emparejan con su comprobante y los duplicados no se repiten.</li>
        </ol>
        {cfg.last_run && cfg.last_result && (
          <div style={{ marginTop: 14, padding: 12, borderRadius: 10, background: "var(--bg-2)", fontSize: 13 }}>
            <div className="meta">Última revisión · {new Date(cfg.last_run).toLocaleString("es-CR")}</div>
            <div>{cfg.last_result.messages} correos · <b>{cfg.last_result.nuevos} nuevos</b> · {cfg.last_result.duplicados} duplicados · {cfg.last_result.ignorados} ignorados</div>
            {cfg.last_result.errores.map((x, i) => <div key={i} style={{ color: "var(--bad)" }}>{x}</div>)}
          </div>
        )}
      </Card>
    </div>
    </div>
  );
}

/* ---------- Acceso de soporte auditado ---------- */
type Grant = { id: number; email: string; reason: string; expires_at: string; revoked_at: string | null; created_at: string; status: "activo" | "vencido" | "revocado"; actions: number; invite_link?: string | null };
type LogRow = { at: string; action: string; entity: string; entity_id: number | null; ip: string | null };
function SupportAccess() {
  const { toast } = useSession();
  const [itemsSt, setItems] = useState<Grant[] | null>(null); // null = cargando
  const items = itemsSt ?? [];
  const [form, setForm] = useState<{ email: string; hours: number; reason: string } | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [log, setLog] = useState<{ g: Grant; rows: LogRow[] } | null>(null);
  const load = () => api<Grant[]>("/settings/support").then(setItems);
  useEffect(() => { load(); }, []);
  const grant = async () => {
    if (!form) return;
    try { const g = await api<Grant>("/settings/support", { method: "POST", json: form }); setForm(null); load(); if (g.invite_link) setLink(g.invite_link); else toast("Acceso concedido"); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const revoke = async (g: Grant) => { if (!confirm(`¿Revocar el acceso de ${g.email}?`)) return; await api(`/settings/support/${g.id}/revoke`, { method: "POST" }); toast("Acceso revocado"); load(); };
  const tone = (s: string) => (s === "activo" ? "ok" : s === "revocado" ? "bad" : "muted");
  return (
    <div className="grid-2">
      <Card title="Acceso de soporte técnico" flush extra={<button className="btn btn--crimson btn--sm" onClick={() => setForm({ email: "", hours: 24, reason: "" })}><Icon d={I.plus} />Dar acceso temporal</button>}>
        {itemsSt === null ? <Loading /> : items.length === 0 ? <Empty title="Sin accesos" hint="Nadie externo tiene acceso. Concedé uno solo cuando soporte te lo pida para revisar un problema." /> : (
          <table className="table"><thead><tr><th>Persona</th><th>Vence</th><th>Estado</th><th className="num">Acciones</th><th /></tr></thead>
            <tbody>{items.map((g) => (
              <tr key={g.id}>
                <td><b>{g.email}</b><div className="meta" style={{ textTransform: "none" }}>{g.reason}</div></td>
                <td className="muted">{new Date(g.expires_at).toLocaleString("es-CR", { dateStyle: "short", timeStyle: "short" })}</td>
                <td><span className={`badge badge--${tone(g.status)}`}>{g.status}</span></td>
                <td className="num mono">{g.actions}</td>
                <td className="num" style={{ whiteSpace: "nowrap" }}><button className="btn btn--ghost btn--sm" onClick={async () => setLog({ g, rows: await api<LogRow[]>(`/settings/support/${g.id}/log`) })}>Bitácora</button>{g.status === "activo" && <button className="btn btn--danger btn--sm" onClick={() => revoke(g)}>Revocar</button>}</td>
              </tr>
            ))}</tbody></table>
        )}
      </Card>
      <Card title="¿Qué es esto?">
        <ol className="muted" style={{ fontSize: 13, lineHeight: 1.7, paddingLeft: 18, margin: 0 }}>
          <li><b>No es una conexión con Fygaro ni con ningún otro sistema.</b> Desde aquí no se conecta nada.</li>
          <li>Sirve para que, cuando reportés un problema, una persona del equipo técnico que mantiene la plataforma (por ejemplo, JC Analytics) entre a revisarlo <b>sin que le prestés tu contraseña</b>.</li>
          <li>Es de <b>solo lectura</b>, dura las horas que elijas y lo podés revocar cuando quieras. Cada pantalla que abra queda en la bitácora con fecha, ruta e IP.</li>
          <li><b>Si no lo necesitás, no tenés que hacer nada</b>: mientras no concedas un acceso aquí, nadie externo puede entrar a tu información.</li>
          <li>No es la mesa de ayuda de tus clientes: los tickets de tus clientes están en el menú <b>Soporte</b>.</li>
        </ol>
      </Card>
      {form && <Modal title="Dar acceso temporal de soporte técnico" onClose={() => setForm(null)} foot={<><button className="btn btn--ghost" onClick={() => setForm(null)}>Cancelar</button><button className="btn btn--crimson" onClick={grant} disabled={!form.email || form.reason.length < 5}>Dar acceso temporal</button></>}>
        <Field label="Correo de la persona"><input className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
        <div className="grid-2">
          <Field label="Duración"><select className="select" value={form.hours} onChange={(e) => setForm({ ...form, hours: Number(e.target.value) })}>{[[2, "2 horas"], [8, "8 horas"], [24, "1 día"], [72, "3 días"], [168, "7 días"]].map(([h, l]) => <option key={h} value={h}>{l}</option>)}</select></Field>
          <Field label="Motivo" hint="Queda en la bitácora."><input className="input" value={form.reason} placeholder="Revisar cierre de caja" onChange={(e) => setForm({ ...form, reason: e.target.value })} /></Field>
        </div>
      </Modal>}
      {link && <Modal title="Invitación de soporte" onClose={() => setLink(null)} foot={<button className="btn btn--crimson" onClick={() => { navigator.clipboard.writeText(link); toast("Enlace copiado"); }}>Copiar enlace</button>}>
        <p style={{ fontSize: 13 }}>La persona aún no tiene cuenta. Enviale este enlace de un solo uso; vence junto con el acceso.</p>
        <input className="input input--mono" style={{ textAlign: "left" }} readOnly value={link} onFocus={(e) => e.currentTarget.select()} />
      </Modal>}
      {log && <Modal title={`Bitácora · ${log.g.email}`} wide onClose={() => setLog(null)}>
        {log.rows.length === 0 ? <Empty title="Sin actividad" hint="Todavía no ha ingresado." /> : (
          <table className="table"><thead><tr><th>Fecha</th><th>Acción</th><th>Ruta</th><th>IP</th></tr></thead>
            <tbody>{log.rows.map((r, i) => <tr key={i}><td className="muted">{new Date(r.at).toLocaleString("es-CR")}</td><td>{r.action}</td><td className="mono muted">{r.entity}</td><td className="mono muted">{r.ip || "—"}</td></tr>)}</tbody></table>
        )}
      </Modal>}
    </div>
  );
}

/* ---------- Tipo de cambio: BCCR automatico (API SDDE) o manual ---------- */
type Fx = { date: string; currency: string; sell: string; buy: string; source: string; note: string | null };
function FxCard() {
  const { toast } = useSession();
  const [fx, setFx] = useState<Fx | null>(null);
  const [busy, setBusy] = useState(false);
  const [manual, setManual] = useState<{ sell: string; buy: string } | null>(null);
  const load = () => api<Fx | null>("/fx/today").then(setFx).catch(() => setFx(null));
  useEffect(() => { load(); }, []);
  const bccr = async () => {
    setBusy(true);
    try { const r = await api<Fx>("/fx/bccr", { method: "POST" }); setFx(r); toast(`BCCR: venta ₡${Number(r.sell).toFixed(2)} · compra ₡${Number(r.buy).toFixed(2)}`); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  const saveManual = async () => {
    if (!manual) return;
    try { const r = await api<Fx>("/fx", { method: "PUT", json: { date: new Date().toISOString().slice(0, 10), currency: "USD", sell: Number(manual.sell), buy: Number(manual.buy), note: "manual" } }); setFx(r); setManual(null); toast("Tipo de cambio guardado"); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  return (
    <Card title="Tipo de cambio del dólar" extra={<span className="meta">{fx ? `${fx.source === "bccr" ? "BCCR" : "Manual"} · ${fmtDate(fx.date)}` : "sin datos"}</span>}>
      <div style={{ display: "flex", gap: 22, flexWrap: "wrap", alignItems: "center" }}>
        <div><div className="meta">Venta</div><div className="money" style={{ fontSize: 26, fontWeight: 700 }}>{fx ? `₡${Number(fx.sell).toFixed(2)}` : "—"}</div></div>
        <div><div className="meta">Compra</div><div className="money" style={{ fontSize: 26, fontWeight: 700 }}>{fx ? `₡${Number(fx.buy).toFixed(2)}` : "—"}</div></div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn btn--soft btn--sm" onClick={() => setManual({ sell: fx ? String(Number(fx.sell)) : "", buy: fx ? String(Number(fx.buy)) : "" })}>Fijar manual</button>
          <button className="btn btn--crimson btn--sm" onClick={bccr} disabled={busy}>{busy ? "Consultando…" : "Actualizar desde el BCCR"}</button>
        </div>
      </div>
      <p className="muted" style={{ fontSize: 12.5, marginBottom: 0 }}>Se actualiza solo cada día a las 6:15 a. m. con el token del BCCR (variable compartida <span className="mono">BCCR_TOKEN</span> en Railway). Las facturas en dólares guardan el tipo de cambio del día en que se emiten.</p>
      {manual && <Modal title="Tipo de cambio manual (hoy)" onClose={() => setManual(null)} foot={<><button className="btn btn--ghost" onClick={() => setManual(null)}>Cancelar</button><button className="btn btn--crimson" onClick={saveManual}>Guardar</button></>}>
        <div className="grid-2">
          <Field label="Venta ₡"><input className="input input--mono" value={manual.sell} onChange={(e) => setManual({ ...manual, sell: e.target.value })} /></Field>
          <Field label="Compra ₡"><input className="input input--mono" value={manual.buy} onChange={(e) => setManual({ ...manual, buy: e.target.value })} /></Field>
        </div>
      </Modal>}
    </Card>
  );
}

/* ---------- Sesion: salir de este equipo o de todos ---------- */
function SessionCard() {
  const { me, logout, toast } = useSession();
  const nav = useNavigate();
  const [busy, setBusy] = useState<"" | "one" | "all">("");
  const out = async (all: boolean) => {
    if (all && !confirm("Se cerrará tu sesión en todos los equipos y celulares donde hayas entrado. ¿Continuar?")) return;
    setBusy(all ? "all" : "one");
    try {
      if (all) await api("/auth/logout-all", { method: "POST" });
      await logout();
      nav("/login", { replace: true });
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); setBusy(""); }
  };
  return (
    <Card title="Sesión">
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Conectado como <b>{me?.user.email}</b>. Si usaste una computadora compartida, cerrá la sesión al terminar.</p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn btn--crimson" onClick={() => out(false)} disabled={!!busy}><Icon d={I.logout} />{busy === "one" ? "Cerrando…" : "Cerrar sesión"}</button>
        <button className="btn btn--ghost" onClick={() => out(true)} disabled={!!busy}>{busy === "all" ? "Cerrando…" : "Cerrar en todos los dispositivos"}</button>
      </div>
    </Card>
  );
}

type MailSt = { configured: boolean; sender: string; reply_to: string | null; domain: string; domain_status: string | null };

// Andres: "cree un usuario y no le llego ningun correo". Esta tarjeta dice por que, en palabras simples, y deja probar.
function MailStatus({ onSent }: { onSent: () => void }) {
  const { toast, allows } = useSession();
  const [st, setSt] = useState<MailSt | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<MailSt>("/settings/mail-status").then(setSt).catch(() => setSt(null)); }, []);
  if (!st) return null;
  const ok = st.configured && (st.domain_status === "verified" || st.domain_status === "desconocido");
  const motivo = !st.configured
    ? "Falta conectar el servicio de envío (Resend). Mientras tanto ningún correo sale: invitaciones, cotizaciones y facturas quedan como “no salió” y el sistema ofrece el enlace para mandarlo por WhatsApp."
    : st.domain_status === "verified" ? "El dominio está verificado: los correos salen firmados y no caen en spam."
    : st.domain_status === "no_agregado" ? `El dominio ${st.domain} no está agregado en Resend. Hay que agregarlo y cargar los registros DNS.`
    : st.domain_status === "desconocido" ? "Servicio conectado. Use “Enviar prueba” para confirmar que llega."
    : `El dominio ${st.domain} todavía no está verificado en Resend (${st.domain_status}). Hasta que lo esté, Gmail y Outlook rechazan los correos.`;
  const probar = async () => {
    setBusy(true);
    try {
      const r = await api<{ to: string; status: string; error: string | null }>("/settings/mail-test", { method: "POST" });
      if (r.status === "enviado") toast(`Prueba enviada a ${r.to}. Revise la bandeja (y spam).`);
      else toast(r.status === "simulado" ? "No salió: falta conectar Resend." : `No salió: ${r.error}`, "bad");
      onSent();
    } finally { setBusy(false); }
  };
  return (
    <Card title="Correo saliente" extra={<span className="meta" style={{ color: ok ? "var(--ok)" : "var(--bad)" }}>{ok ? "● Activo" : "● No está saliendo"}</span>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div><span className="meta">Sale desde</span> <b>{st.sender}</b>{st.reply_to && <> · <span className="meta">respuestas a</span> {st.reply_to}</>}</div>
        <p className="muted" style={{ margin: 0 }}>{motivo}</p>
        <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>Este es el correo que <b>sale</b> de la plataforma. Para <b>recibir</b> facturas de proveedores, mirá la pestaña XML de proveedores.</p>
        {allows("settings.configurar") && <button className="btn btn--soft" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={probar}>Enviar prueba a mi correo</button>}
      </div>
    </Card>
  );
}
