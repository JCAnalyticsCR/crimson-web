/* Ajustes -> Hacienda: modo (y el interruptor de produccion con su lista de verificacion), identificacion, ubicacion,
   actividades economicas, credenciales por ambiente y consecutivos fiscales. Las credenciales SOLO se escriben: la API
   devuelve "cargado / vence el …", nunca el usuario, la contrasena, el PIN ni el .p12. */
import { useEffect, useRef, useState } from "react";
import { api, fmtDate, parseTs } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Field, I, Icon, Loading, Modal } from "../../ui/components";
import { MODE_LABEL, type CredBlock, type HaciendaState } from "../accounting/hacienda";

type Geo = { code: string; name: string; cantons: { code: string; name: string }[] }[];
type Lookup = { code: string; description: string | null; registered: boolean }[];
const ID_TYPES = [["01", "Física"], ["02", "Jurídica"], ["03", "DIMEX"], ["04", "NITE"]];
const ENVS = [["pruebas", "Pruebas"], ["produccion", "Producción"]] as const;
const ENV_NAME = { pruebas: "Pruebas", produccion: "Producción" } as const;
/* lo mismo que hacienda_config.atv_user_env: el usuario de pruebas termina en "stag" y el de producción en "prod" */
const atvEnv = (u: string): "pruebas" | "produccion" | null => {
  const s = u.trim().toLowerCase();
  if (!s) return null;
  const dom = s.includes("@") ? s.slice(s.lastIndexOf("@") + 1) : "";
  for (const [marca, env] of [["stag", "pruebas"], ["prod", "produccion"]] as const) {
    if (dom.startsWith(`${marca}.`) || dom === marca || s.endsWith(marca)) return env;
  }
  return null;
};

export default function HaciendaTab() {
  const { toast } = useSession();
  const [st, setSt] = useState<HaciendaState | null>(null);
  const [geo, setGeo] = useState<Geo>([]);
  const [prof, setProf] = useState<HaciendaState["profile"] | null>(null);
  const [newAct, setNewAct] = useState({ code: "", description: "" });
  const [lookup, setLookup] = useState<Lookup | null>(null);
  const [env, setEnv] = useState<"pruebas" | "produccion">("pruebas");
  const [cred, setCred] = useState({ atv_user: "", atv_password: "", pin: "", admin_password: "", allow_other_id: false });
  const p12 = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [prodModal, setProdModal] = useState<"on" | "off" | null>(null);
  const [prodForm, setProdForm] = useState({ admin_password: "", confirm_text: "" });
  const [pruebasTxt, setPruebasTxt] = useState<string | null>(null); // confirmacion escrita para el modo Pruebas (plan 1.11)
  const [confirmPw, setConfirmPw] = useState<string | null>(null);
  const [delPw, setDelPw] = useState<string | null>(null);
  const [groups, setGroups] = useState<HaciendaState["consecutives"]>([]);

  const first = useRef(true);
  const apply = (s: HaciendaState) => {
    setSt(s); setProf(s.profile); setGroups(s.consecutives);
    // la primera vez se abre la pestaña del ambiente con el que se emite
    if (first.current) { first.current = false; if (s.emission_environment === "produccion" || s.mode === "produccion") setEnv("produccion"); }
  };
  const load = () => api<HaciendaState>("/settings/hacienda").then(apply).catch((e) => toast(e.message, "bad"));
  useEffect(() => { load(); api<Geo>("/fiscal/geo").then(setGeo).catch(() => setGeo([])); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  if (!st || !prof) return <Loading />;
  const ro = !st.can_configure;
  const err = (e: unknown) => toast(e instanceof Error ? e.message : "Error", "bad");
  const call = async (fn: () => Promise<HaciendaState | unknown>, ok: string) => {
    setBusy(true);
    try { const r = await fn(); if (r && typeof r === "object" && "mode" in r) apply(r as HaciendaState); else await load(); toast(ok); }
    catch (e) { err(e); }
    finally { setBusy(false); }
  };
  const prov = geo.find((p) => p.code === prof.province);
  const c: CredBlock = st.credentials[env];
  const emite = st.emission_environment !== undefined ? st.emission_environment : st.mode === "produccion" || st.mode === "pruebas" ? st.mode : null;
  const typed = atvEnv(cred.atv_user);
  const typedWarn = typed && typed !== env ? `Este usuario termina en «${typed === "pruebas" ? "stag" : "prod"}», que es de ${ENV_NAME[typed]}, y lo está cargando en ${ENV_NAME[env]}. Revise que no esté cruzado (se puede guardar igual).` : null;

  const setMode = (mode: string, confirm_text?: string) => call(() => api("/settings/hacienda/mode", { method: "PUT", json: { mode, confirm_text } }), "Modo actualizado");
  /* Pruebas en la empresa real manda cada venta al ambiente de pruebas: se confirma escribiendo PRUEBAS. Para probar
     la conexion sin tocar las ventas: credenciales de Pruebas + "Probar conexion" + hacienda_smoke --sin-modo. */
  const pickMode = (k: string) => (k === "pruebas" && st.mode !== "pruebas" ? setPruebasTxt("") : setMode(k));
  const saveProfile = () => call(() => api("/settings/hacienda/profile", { method: "PUT", json: { ...prof, commercial_name: prof.commercial_name || null, phone: prof.phone || null, district_name: prof.district_name || null, barrio: prof.barrio || null } }), "Identificación guardada");
  const addAct = () => call(() => api("/settings/hacienda/activities", { method: "POST", json: { code: newAct.code.trim(), description: newAct.description.trim() || null } }).then((r) => { setNewAct({ code: "", description: "" }); return r; }), "Actividad agregada");
  const saveCred = async () => {
    const fd = new FormData();
    fd.append("admin_password", cred.admin_password);
    if (cred.atv_user) fd.append("atv_user", cred.atv_user);
    if (cred.atv_password) fd.append("atv_password", cred.atv_password);
    if (cred.pin) fd.append("pin", cred.pin);
    fd.append("allow_other_id", String(cred.allow_other_id));
    const file = p12.current?.files?.[0];
    if (file) fd.append("p12", file);
    setBusy(true);
    try {
      await api(`/settings/hacienda/credentials/${env}`, { method: "PUT", body: fd });
      setCred({ atv_user: "", atv_password: "", pin: "", admin_password: "", allow_other_id: false });
      if (p12.current) p12.current.value = ""; // el archivo no queda en la pagina
      toast("Credenciales guardadas (cifradas)"); load();
    } catch (e) { err(e); }
    finally { setBusy(false); }
  };
  const test = () => call(() => api<{ ok: boolean; detail: string }>(`/settings/hacienda/credentials/${env}/test`, { method: "POST" }).then((r) => { toast(r.detail, r.ok ? "ok" : "bad"); return null; }), "Prueba terminada");
  const saveGroup = (g: HaciendaState["consecutives"][number]) => call(() => api(`/settings/billing-groups/${g.group_id}`, { method: "PUT", json: { prefix: `F${g.code}`, branch: g.branch, terminal: g.terminal, current: g.current } }), "Consecutivo guardado");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
      <div className="grid-2">
        <Card title="Modo" extra={<span className={`badge ${st.mode === "produccion" ? "badge--bad" : st.mode === "pruebas" ? "badge--warn" : "badge--muted"}`} style={{ fontSize: 13 }}>{MODE_LABEL[st.mode]}</span>}>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Desactivada: no se emite (las respuestas a proveedores se simulan). Simulado: todo se simula, sin validez fiscal. Pruebas: ambiente de pruebas real de Hacienda (cada venta real iría a pruebas: para probar la conexión no hace falta cambiar el modo). Producción solo con el interruptor de abajo.</p>
          <div className="acc-seg">
            {[["none", "Desactivada", "ninguno"], ["sandbox", "Simulado", "simulado"], ["pruebas", "Pruebas", "pruebas"]].map(([k, l, m]) => <button key={k} type="button" disabled={ro || busy} className={st.mode === m ? "is-active" : ""} onClick={() => pickMode(k)}>{l}</button>)}
          </div>
        </Card>
        <Card title="Producción">
          <ul style={{ listStyle: "none", padding: 0, margin: 0, fontSize: 13, display: "flex", flexDirection: "column", gap: 4 }}>
            {st.production.checks.map((ch) => <li key={ch.key} style={{ color: ch.ok ? "var(--ok)" : "var(--bad)" }}>{ch.ok ? "✓" : "✗"} <span style={{ color: "var(--text)" }}>{ch.detail}</span></li>)}
          </ul>
          {st.production.blocked_reason && <div className="acc-warn" style={{ marginTop: 8 }}>No se emite nada (ni a producción ni a pruebas) hasta resolverlo: {st.production.blocked_reason}</div>}
          {!ro && (
            <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
              {st.production.enabled_at ? <button className="btn btn--soft btn--sm" onClick={() => setProdModal("off")}>Apagar producción</button> : <button className="btn btn--danger btn--sm" onClick={() => setProdModal("on")}>Activar producción</button>}
              {st.production.enabled_at && <span className="muted" style={{ fontSize: 12 }}>Activa desde {parseTs(st.production.enabled_at).toLocaleString("es-CR")}</span>}
            </div>
          )}
        </Card>
      </div>

      <div className="grid-2">
        <Card title="Identificación">
          <div className="grid-2">
            <Field label="Tipo"><select className="select" disabled={ro} value={prof.id_type} onChange={(e) => setProf({ ...prof, id_type: e.target.value })}>{ID_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
            <Field label="Cédula"><input className="input input--mono" style={{ textAlign: "left" }} disabled={ro} value={prof.tax_id || ""} onChange={(e) => setProf({ ...prof, tax_id: e.target.value })} /></Field>
            <Field label="Razón social"><input className="input" disabled={ro} value={prof.legal_name || ""} onChange={(e) => setProf({ ...prof, legal_name: e.target.value })} /></Field>
            <Field label="Nombre comercial"><input className="input" disabled={ro} value={prof.commercial_name || ""} onChange={(e) => setProf({ ...prof, commercial_name: e.target.value })} /></Field>
            <Field label="Correo de facturación"><input className="input" type="email" disabled={ro} value={prof.email || ""} onChange={(e) => setProf({ ...prof, email: e.target.value })} /></Field>
            <Field label="Teléfono"><div style={{ display: "flex", gap: 6 }}><input className="input input--mono" style={{ width: 70 }} disabled={ro} value={prof.phone_country} onChange={(e) => setProf({ ...prof, phone_country: e.target.value })} /><input className="input input--mono" disabled={ro} value={prof.phone || ""} onChange={(e) => setProf({ ...prof, phone: e.target.value })} /></div></Field>
          </div>
        </Card>
        <Card title="Ubicación">
          <div className="grid-2">
            <Field label="Provincia"><select className="select" disabled={ro} value={prof.province || ""} onChange={(e) => setProf({ ...prof, province: e.target.value, canton: "" })}><option value="">—</option>{geo.map((p) => <option key={p.code} value={p.code}>{p.code} · {p.name}</option>)}</select></Field>
            <Field label="Cantón"><select className="select" disabled={ro || !prov} value={prof.canton || ""} onChange={(e) => setProf({ ...prof, canton: e.target.value })}><option value="">—</option>{prov?.cantons.map((c2) => <option key={c2.code} value={c2.code}>{c2.code} · {c2.name}</option>)}</select></Field>
            <Field label="Distrito (código)" hint="Según la codificación de Hacienda (2 dígitos)."><input className="input input--mono" maxLength={2} disabled={ro} value={prof.district || ""} onChange={(e) => setProf({ ...prof, district: e.target.value.replace(/\D/g, "").slice(0, 2) })} /></Field>
            <Field label="Nombre del distrito (opcional)"><input className="input" disabled={ro} value={prof.district_name || ""} onChange={(e) => setProf({ ...prof, district_name: e.target.value })} /></Field>
            <Field label="Barrio (opcional)" hint="De 5 a 50 caracteres."><input className="input" disabled={ro} value={prof.barrio || ""} onChange={(e) => setProf({ ...prof, barrio: e.target.value })} /></Field>
            <Field label="Otras señas" hint="De 5 a 250 caracteres."><input className="input" disabled={ro} value={prof.other_signs || ""} onChange={(e) => setProf({ ...prof, other_signs: e.target.value })} /></Field>
          </div>
          {!ro && <button className="btn btn--crimson btn--sm" style={{ marginTop: 10 }} disabled={busy} onClick={saveProfile}><Icon d={I.check} />Guardar identificación y ubicación</button>}
        </Card>
      </div>

      <Card title="Actividades económicas" flush extra={!ro && <button className="btn btn--ghost btn--sm" onClick={() => api<Lookup>("/settings/hacienda/activities/lookup").then(setLookup).catch(err)}>Consultar en Hacienda</button>}>
        <table className="table"><thead><tr><th>Código</th><th>Descripción</th><th>Por defecto</th><th /></tr></thead>
          <tbody>
            {st.activities.filter((a) => a.active).map((a) => <tr key={a.id}>
              <td className="mono">{a.code}</td><td>{a.description || "—"}</td>
              <td><input type="radio" name="act-default" disabled={ro} checked={a.is_default} onChange={() => call(() => api(`/settings/hacienda/activities/${a.id}`, { method: "PUT", json: { code: a.code, description: a.description, is_default: true, active: true } }), "Actividad por defecto")} /></td>
              <td className="num">{!ro && !a.is_default && <button className="btn btn--ghost btn--sm" onClick={() => call(() => api(`/settings/hacienda/activities/${a.id}`, { method: "DELETE" }), "Actividad quitada")}>Quitar</button>}</td>
            </tr>)}
            {!ro && <tr><td><input className="input input--mono" style={{ height: 32, width: 100 }} placeholder="6202.0" maxLength={6} value={newAct.code} onChange={(e) => setNewAct({ ...newAct, code: e.target.value })} /></td><td><input className="input" style={{ height: 32 }} placeholder="Descripción" value={newAct.description} onChange={(e) => setNewAct({ ...newAct, description: e.target.value })} /></td><td /><td className="num"><button className="btn btn--soft btn--sm" disabled={newAct.code.trim().length !== 6} onClick={addAct}><Icon d={I.plus} />Agregar</button></td></tr>}
          </tbody></table>
        {lookup && <div style={{ padding: "10px 18px", fontSize: 13 }}><div className="meta">Registradas en Hacienda</div>{lookup.length === 0 ? <p className="muted">Hacienda no devolvió actividades.</p> : <ul style={{ margin: 0, paddingLeft: 18 }}>{lookup.map((l) => <li key={l.code}><span className="mono">{l.code}</span> · {l.description}{!l.registered && " (inactiva)"} {!ro && l.code.length === 6 && !st.activities.some((a) => a.code === l.code && a.active) && <button className="btn btn--ghost btn--sm" onClick={() => setNewAct({ code: l.code, description: l.description || "" })}>Usar</button>}</li>)}</ul>}</div>}
      </Card>

      <Card title="Ambiente para emitir" extra={<span className={`badge ${emite === "produccion" ? "badge--bad" : emite === "pruebas" ? "badge--warn" : "badge--muted"}`} style={{ fontSize: 13 }}>{emite ? ENV_NAME[emite] : "No se emite"}</span>}>
        {/* reunión 2, pedido 8: quedó cargado Pruebas y se creyó que era Producción. Aquí se dice sin ambigüedad con qué se emite. */}
        <p style={{ fontSize: 14, margin: 0 }}>
          {emite === "produccion" && <>Hoy las facturas se emiten en <b>Producción</b> (con validez fiscal), con las credenciales de Producción.</>}
          {emite === "pruebas" && <>Hoy las facturas se emiten en <b>Pruebas</b> (ambiente de pruebas de Hacienda, <b>sin validez fiscal</b>), con las credenciales de Pruebas.</>}
          {!emite && <>Hoy <b>no se emite</b> a Hacienda (modo {MODE_LABEL[st.mode]}){st.production.blocked_reason ? `: ${st.production.blocked_reason}` : ""}. Las credenciales cargadas no se usan para emitir.</>}
        </p>
        {!ro && (
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 10 }}>
            <span className="muted" style={{ fontSize: 13 }}>Emitir con:</span>
            <div className="acc-seg" role="group" aria-label="Ambiente para emitir">
              <button type="button" disabled={busy} className={emite === "pruebas" ? "is-active" : ""} onClick={() => { if (emite !== "pruebas") pickMode("pruebas"); }}>Pruebas</button>
              <button type="button" disabled={busy} className={emite === "produccion" ? "is-active" : ""} onClick={() => { if (!st.production.enabled_at) setProdModal("on"); }}>Producción</button>
            </div>
            <span className="muted" style={{ fontSize: 12 }}>Pruebas pide escribir PRUEBAS; Producción pide la lista de verificación completa y escribir PRODUCCIÓN.</span>
          </div>
        )}
      </Card>

      <Card title="Credenciales" extra={<div className="tabs" role="tablist" aria-label="Ambiente de las credenciales">{ENVS.map(([k, l]) => <button key={k} role="tab" aria-selected={env === k} className={env === k ? "is-active" : ""} onClick={() => setEnv(k)}>{l}{emite === k ? " · se usa para emitir" : ""}</button>)}</div>}>
        <div className="acc-warn" style={{ marginTop: 0, marginBottom: 10, fontSize: 13.5, borderColor: env === "produccion" ? "var(--bad)" : undefined }}>
          Está viendo las credenciales de <b>{ENV_NAME[env]}</b>. {env === "pruebas" ? "El usuario ATV de Pruebas termina en «stag» (…@stag.comprobanteselectronicos.go.cr)." : "El usuario ATV de Producción termina en «prod» (…@prod.comprobanteselectronicos.go.cr)."}{" "}
          {emite === env ? <b>Estas son las que se usan hoy para emitir.</b> : <>Hoy <b>no</b> se usan para emitir{emite ? ` (se emite con ${ENV_NAME[emite]})` : ""}.</>}
        </div>
        <p style={{ fontSize: 13, marginTop: 0 }}><b>Cargue aquí el usuario ATV, la contraseña, el certificado y el PIN de este ambiente. Se guardan cifrados y nunca se vuelven a mostrar. No los envíe por chat ni por correo a nadie, tampoco al equipo de desarrollo.</b></p>
        {c.source === "entorno" && <div className="acc-warn">Configuradas en el servidor (variables de entorno). Si carga aquí unas propias, mandan sobre las del servidor.</div>}
        {!c.readable && <div className="acc-warn" style={{ borderColor: "var(--bad)" }}>Ilegibles con la llave actual del servidor: vuelva a cargarlas.</div>}
        {c.atv_user_warning && <div className="acc-warn" role="alert">{c.atv_user_warning}</div>}
        <dl className="acc-kv" style={{ margin: "10px 0" }}>
          <dt>Usuario ATV</dt><dd>{c.atv_user_set ? `✓ cargado${c.atv_user_env ? ` · de ${ENV_NAME[c.atv_user_env]} (termina en «${c.atv_user_env === "pruebas" ? "stag" : "prod"}»)` : ""}` : "—"}</dd>
          <dt>Contraseña</dt><dd>{c.atv_password_set ? "✓ cargada" : "—"}</dd>
          <dt>Certificado</dt><dd>{c.cert ? `Cargado · vence el ${fmtDate(c.cert.not_after ? c.cert.not_after.slice(0, 10) : null)}${c.cert.days_left !== null ? ` (${c.cert.days_left} días)` : ""} · emitido a ${c.cert.subject_cn || "—"}` : c.p12_set ? "✓ cargado" : "—"}</dd>
          <dt>PIN</dt><dd>{c.pin_set ? "✓ cargado" : "—"}</dd>
          <dt>Última prueba</dt><dd>{c.last_test_at ? `${parseTs(c.last_test_at).toLocaleString("es-CR")} · ${c.last_test_ok ? "✓" : "✗"} ${c.last_test_detail || ""}` : "sin probar"}</dd>
        </dl>
        {!ro && (
          <>
            <div className="grid-2">
              <Field label={`Usuario ATV de ${ENV_NAME[env]}`} hint={`Deje vacío para conservar. Termina en «${env === "pruebas" ? "stag" : "prod"}».`}><input className="input" autoComplete="off" value={cred.atv_user} onChange={(e) => setCred({ ...cred, atv_user: e.target.value })} /></Field>
              <Field label="Contraseña ATV" hint="Deje vacío para conservar."><input className="input" type="password" autoComplete="new-password" value={cred.atv_password} onChange={(e) => setCred({ ...cred, atv_password: e.target.value })} /></Field>
              <Field label="Certificado (.p12)" hint="Deje vacío para conservar. Máx. 64 KB."><input ref={p12} className="input" type="file" accept=".p12,.pfx,application/x-pkcs12" /></Field>
              <Field label="PIN del certificado" hint="Hasta 64 caracteres. Los espacios al inicio o al final se quitan. Deje vacío para conservar."><input className="input" type="password" autoComplete="new-password" maxLength={80} value={cred.pin} onChange={(e) => setCred({ ...cred, pin: e.target.value })} /></Field>
              <Field label="Su contraseña de administrador"><input className="input" type="password" autoComplete="new-password" value={cred.admin_password} onChange={(e) => setCred({ ...cred, admin_password: e.target.value })} /></Field>
            </div>
            {typedWarn && <div className="acc-warn" role="alert">{typedWarn}</div>}
            <label className="acc-check"><input type="checkbox" checked={cred.allow_other_id} onChange={(e) => setCred({ ...cred, allow_other_id: e.target.checked })} />El certificado es de otra cédula a propósito</label>
            <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
              <button className="btn btn--crimson btn--sm" disabled={busy || !cred.admin_password} onClick={saveCred}>Guardar credenciales de {ENV_NAME[env]}</button>
              <button className="btn btn--soft btn--sm" disabled={busy || c.source === "ninguna"} onClick={test}>Probar conexión</button>
              {c.source === "ajustes" && <button className="btn btn--ghost btn--sm" onClick={() => setDelPw("")}>Eliminar</button>}
            </div>
          </>
        )}
      </Card>

      <Card title="Consecutivos fiscales" flush extra={!ro && <button className="btn btn--soft btn--sm" onClick={() => setConfirmPw("")}>Confirmar consecutivos</button>}>
        <div style={{ overflowX: "auto" }}>
          <table className="table"><thead><tr><th>Tipo</th><th>Sucursal</th><th>Terminal</th><th className="num">Último usado</th><th>Siguiente</th><th>Fygaro</th><th /></tr></thead>
            <tbody>{groups.map((g, i) => <tr key={g.code}>
              <td><b>{g.code}</b> · {g.label}</td>
              <td><input className="input input--mono" style={{ height: 32, width: 64 }} disabled={ro} value={g.branch} onChange={(e) => setGroups(groups.map((x, k) => (k === i ? { ...x, branch: e.target.value } : x)))} /></td>
              <td><input className="input input--mono" style={{ height: 32, width: 84 }} disabled={ro} value={g.terminal} onChange={(e) => setGroups(groups.map((x, k) => (k === i ? { ...x, terminal: e.target.value } : x)))} /></td>
              <td className="num"><input className="input input--mono" style={{ height: 32, width: 100 }} disabled={ro} value={g.current} onChange={(e) => setGroups(groups.map((x, k) => (k === i ? { ...x, current: Number(e.target.value.replace(/\D/g, "")) || 0 } : x)))} /></td>
              <td className="mono muted" style={{ fontSize: 12 }}>{g.next_preview}</td>
              <td className="muted" style={{ fontSize: 12 }}>{g.fygaro_max ? <span style={{ color: g.current < g.fygaro_max ? "var(--bad)" : undefined }}>último en el histórico de Fygaro: {g.fygaro_max}</span> : "—"}</td>
              <td className="num">{!ro && <button className="btn btn--ghost btn--sm" onClick={() => saveGroup(g)}>Guardar</button>}</td>
            </tr>)}</tbody></table>
        </div>
        <p className="muted" style={{ padding: "10px 18px", fontSize: 12, margin: 0 }}>Si viene de Fygaro, ponga aquí el último número que usó Fygaro para cada tipo (Hacienda exige continuar la numeración). Si los dos sistemas van a convivir, use otra terminal. Solo sube, nunca baja. {st.consecutives_confirmed_at ? `Confirmados el ${parseTs(st.consecutives_confirmed_at).toLocaleString("es-CR")} (vale 24 h).` : "Sin confirmar."} Lo simulado y las pruebas usan otra numeración (T01…T07) que no gasta esta.</p>
      </Card>

      {prodModal && (
        <Modal title={prodModal === "on" ? "Activar producción" : "Apagar producción"} onClose={() => setProdModal(null)} foot={<><button className="btn btn--ghost" onClick={() => setProdModal(null)}>Cancelar</button><button className={`btn ${prodModal === "on" ? "btn--danger" : "btn--crimson"}`} disabled={busy || !prodForm.admin_password || (prodModal === "on" && !/^PRODUCCI[OÓ]N$/i.test(prodForm.confirm_text.trim()))} onClick={async () => { await call(() => api("/settings/hacienda/production", { method: "POST", json: { enabled: prodModal === "on", ...prodForm } }), prodModal === "on" ? "Producción activada" : "Producción desactivada"); setProdModal(null); setProdForm({ admin_password: "", confirm_text: "" }); }}>{prodModal === "on" ? "Activar" : "Desactivar"}</button></>}>
          {prodModal === "on" && <p style={{ fontSize: 13, marginTop: 0 }}>Desde ahora los comprobantes y los mensajes receptor van a Hacienda <b>con validez fiscal</b>. Escriba <b>PRODUCCIÓN</b> para confirmar.</p>}
          {prodModal === "on" && <Field label="Confirmación"><input className="input" value={prodForm.confirm_text} onChange={(e) => setProdForm({ ...prodForm, confirm_text: e.target.value })} /></Field>}
          {/* apagar deja la empresa en Desactivada, nunca en Pruebas (cada venta real iria al ambiente de pruebas) */}
          {prodModal === "off" && <p style={{ fontSize: 13, marginTop: 0 }}>La empresa queda en modo <b>Desactivada</b>: no se emite nada hasta volver a activar producción y lo vendido entra a la lista «Por emitir».</p>}
          <Field label="Su contraseña de administrador"><input className="input" type="password" autoComplete="new-password" value={prodForm.admin_password} onChange={(e) => setProdForm({ ...prodForm, admin_password: e.target.value })} /></Field>
        </Modal>
      )}
      {pruebasTxt !== null && (
        <Modal title="Pasar a modo Pruebas" onClose={() => setPruebasTxt(null)} foot={<><button className="btn btn--ghost" onClick={() => setPruebasTxt(null)}>Cancelar</button><button className="btn btn--danger" disabled={busy || pruebasTxt.trim().toUpperCase() !== "PRUEBAS"} onClick={async () => { await setMode("pruebas", pruebasTxt.trim()); setPruebasTxt(null); }}>Pasar a Pruebas</button></>}>
          <div className="acc-warn" style={{ marginTop: 0 }}>Con el modo Pruebas, <b>cada venta real se emitiría al ambiente de pruebas y después no se podría emitir en producción</b>.</div>
          <p className="muted" style={{ fontSize: 13 }}>Para probar la conexión con Hacienda sin tocar las ventas no hace falta cambiar el modo: cargue las credenciales de Pruebas abajo y use «Probar conexión»; la prueba completa la corre el equipo técnico sin cambiar el modo. Si igual quiere pasar a Pruebas, escriba <b>PRUEBAS</b>.</p>
          <Field label="Confirmación"><input className="input" autoFocus value={pruebasTxt} onChange={(e) => setPruebasTxt(e.target.value)} /></Field>
        </Modal>
      )}
      {confirmPw !== null && (
        <Modal title="Confirmar consecutivos" onClose={() => setConfirmPw(null)} foot={<><button className="btn btn--ghost" onClick={() => setConfirmPw(null)}>Cancelar</button><button className="btn btn--crimson" disabled={!confirmPw} onClick={async () => { await call(() => api("/settings/hacienda/consecutives/confirm", { method: "POST", json: { admin_password: confirmPw } }), "Consecutivos confirmados"); setConfirmPw(null); }}>Confirmar</button></>}>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Confirma que los números de arriba siguen la numeración que usaba Fygaro. La confirmación vale 24 h.</p>
          <Field label="Su contraseña de administrador"><input className="input" type="password" autoComplete="new-password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} /></Field>
        </Modal>
      )}
      {delPw !== null && (
        <Modal title={`Eliminar credenciales de ${env}`} onClose={() => setDelPw(null)} foot={<><button className="btn btn--ghost" onClick={() => setDelPw(null)}>Cancelar</button><button className="btn btn--danger" disabled={!delPw} onClick={async () => { await call(() => api(`/settings/hacienda/credentials/${env}`, { method: "DELETE", json: { admin_password: delPw } }), "Credenciales eliminadas"); setDelPw(null); }}>Eliminar</button></>}>
          <Field label="Su contraseña de administrador"><input className="input" type="password" autoComplete="new-password" value={delPw} onChange={(e) => setDelPw(e.target.value)} /></Field>
        </Modal>
      )}
    </div>
  );
}
