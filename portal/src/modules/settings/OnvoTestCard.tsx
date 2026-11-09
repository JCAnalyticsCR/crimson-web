/* Ajustes -> Pagos -> Pasarelas -> ONVO «Pruebas · sin dinero real» (09/11, pedido de Andrés).
   Llaves de PRUEBAS aparte de las de producción (otra tabla en la API): solo las usa la Tienda de pruebas; la tienda
   pública y los enlaces de pago de facturas nunca. Los valores escritos nunca vuelven a mostrarse: al guardar se borran
   del formulario y la API solo devuelve máscara (prefijo + últimos 4) y «cargado». */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, parseTs } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, Modal } from "../../ui/components";
import "./onvo-env.css";

export type OnvoTestStatus = {
  api: { configured: boolean; enabled: boolean; mode: "test" | null; unreadable: boolean; secret_mask: string | null; publishable_short: string | null; last_ping: { at: string; ok: boolean } | null };
  webhook: { set: boolean; format: "firmado" | "anterior" | null; url: string; unreadable: boolean; last: { at: string; type: string; result: string } | null };
  fiscal_keys: boolean;
  problems: string[];
};

export const crTime = (s: string) => parseTs(s).toLocaleString("es-CR", { timeZone: "America/Costa_Rica", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

const EMPTY = { secret: "", publishable: "", webhook_secret: "", enabled: true };

export default function OnvoTestCard() {
  const { toast, allows, me } = useSession();
  const nav = useNavigate();
  const canEdit = allows("settings.configurar");
  const isAdmin = me?.role === "admin";
  const [st, setSt] = useState<OnvoTestStatus | null>(null);
  const [form, setForm] = useState<typeof EMPTY | null>(null);
  const [busy, setBusy] = useState(false);
  const [ping, setPing] = useState<{ ok: boolean; detail: string } | null>(null);
  const [quitar, setQuitar] = useState(false);

  const load = () => api<OnvoTestStatus>("/settings/gateways/onvo-pruebas").then(setSt).catch(() => setSt(null));
  useEffect(() => { load(); }, []);
  const err = (e: unknown) => toast(e instanceof Error ? e.message : "Error", "bad");

  const save = async () => {
    if (!form) return;
    setBusy(true);
    try {
      const r = await api<OnvoTestStatus>("/settings/gateways/onvo-pruebas", { method: "PUT", json: { secret: form.secret || null, publishable: form.publishable || null, webhook_secret: form.webhook_secret || null, enabled: form.enabled } });
      setSt(r); setForm(null); setPing(null); toast("Llaves de pruebas guardadas");
    } catch (e) { err(e); } finally { setBusy(false); }
  };
  const probar = async () => {
    setBusy(true); setPing(null);
    try { setPing(await api<{ ok: boolean; detail: string }>("/settings/gateways/onvo-pruebas/probar", { method: "POST" })); load(); }
    catch (e) { setPing({ ok: false, detail: e instanceof Error ? e.message : "Error" }); } finally { setBusy(false); }
  };
  const remove = async () => {
    setBusy(true);
    try { setSt(await api<OnvoTestStatus>("/settings/gateways/onvo-pruebas", { method: "DELETE" })); setQuitar(false); setPing(null); toast("Llaves de pruebas quitadas"); }
    catch (e) { err(e); } finally { setBusy(false); }
  };

  const a = st?.api;
  const wh = st?.webhook;
  return (
    <section className="gw-env__card" data-env="test" aria-label="ONVO · Pruebas">
      <header className="gw-env__head">
        <div><div className="gw-env__title">Pruebas · sin dinero real</div><div className="meta">ONVO modo Test</div></div>
        <span className="env-badge env-badge--test">Pruebas</span>
      </header>
      <p className="gw-env__note">Solo las usa la <b>Tienda de pruebas</b>. La tienda pública y los enlaces de pago de facturas nunca usan estas llaves.</p>
      {st === null ? <p className="muted">Cargando…</p> : (<>
        <div className="gw-env__row"><span className="meta">API · </span>
          {a?.unreadable ? <span style={{ color: "var(--bad)" }}>Ilegible: vuelva a cargarla</span>
            : a?.configured ? <><span className="mono">{a.secret_mask}</span> · <span style={{ color: a.enabled ? "var(--ok)" : "var(--warn)" }}>{a.enabled ? "Tienda de pruebas habilitada" : "apagada"}</span></>
            : <span className="muted">Sin cargar</span>}
        </div>
        {a?.publishable_short && <div className="gw-env__row"><span className="meta">Publicable · </span><span className="mono">{a.publishable_short}</span></div>}
        {a?.last_ping && <div className="gw-env__row muted">Última prueba de conexión: {crTime(a.last_ping.at)} · {a.last_ping.ok ? "bien" : "falló"}</div>}
        {a?.configured && canEdit && <div className="gw-env__actions"><button className="btn btn--soft btn--sm" onClick={probar} disabled={busy}>{busy ? "Probando…" : "Probar"}</button>{ping && <span style={{ color: ping.ok ? "var(--ok)" : "var(--bad)" }}>{ping.detail}</span>}</div>}
        <div className="gw-env__row"><span className="meta">Webhook de pruebas · </span>
          {wh?.unreadable ? <span style={{ color: "var(--bad)" }}>Ilegible: vuelva a cargarlo</span>
            : !wh?.set ? <span className="muted">Sin secreto cargado</span>
            : <><span>Formato firmado (whsec_…)</span>{" · "}{wh.last ? <span style={{ color: "var(--ok)" }}>último aviso {crTime(wh.last.at)} ({wh.last.type === "webhook_endpoint.test" ? "prueba de conexión" : wh.last.type})</span> : <span className="muted">todavía no llega ningún aviso</span>}</>}
        </div>
        {wh?.url && <div className="gw-env__url"><input className="input input--mono" readOnly value={wh.url} aria-label="URL del webhook de pruebas" /><button className="btn btn--ghost btn--sm" onClick={() => { navigator.clipboard.writeText(wh.url); toast("URL de pruebas copiada"); }}>Copiar</button></div>}
        <p className="muted gw-env__help">En ONVO, cambie a <b>modo Test</b> → Desarrolladores → Webhooks, registre esta URL (termina en <span className="mono">/pruebas</span>) y pegue aquí su <span className="mono">whsec_</span> propio.</p>
        {st.problems.map((p, i) => <p key={i} className="gw-env__problem">{p}</p>)}
        <div className="gw-env__actions">
          {canEdit && <button className="btn btn--soft btn--sm" onClick={() => setForm({ ...EMPTY, enabled: a?.configured ? !!a.enabled : true })}>Cargar o cambiar llaves</button>}
          {canEdit && a && (a.configured || a.unreadable || wh?.set) && <button className="btn btn--ghost btn--sm" onClick={() => setQuitar(true)}>Quitar llaves de pruebas</button>}
          {isAdmin && <button className="btn btn--ghost btn--sm" onClick={() => nav("/mi-tienda/pruebas")}>Tienda de pruebas</button>}
        </div>
      </>)}

      {form && <Modal title="Llaves de PRUEBAS de ONVO" onClose={() => setForm(null)} foot={<><button className="btn btn--ghost" onClick={() => setForm(null)}>Cancelar</button><button className="btn btn--crimson" disabled={busy} onClick={save}>Guardar</button></>}>
        <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>Solo llaves de <b>modo Test</b> (onvo_test_…). Se cifran en el servidor y no vuelven a mostrarse. Campo vacío = no cambiar.</p>
        {st && !st.fiscal_keys && <p className="gw-env__problem">El servidor no tiene el llavero de cifrado (FISCAL_KEYS): no se pueden guardar.</p>}
        <div className="grid-2">
          <Field label="Llave secreta de pruebas"><input className="input" type="password" autoComplete="off" placeholder="onvo_test_secret_key_…" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} /></Field>
          <Field label="Llave publicable de pruebas"><input className="input" type="password" autoComplete="off" placeholder="onvo_test_publishable_key_…" value={form.publishable} onChange={(e) => setForm({ ...form, publishable: e.target.value })} /></Field>
          <Field label="Secreto del webhook de pruebas" hint="El whsec_ del webhook del modo Test (distinto del de producción)."><input className="input" type="password" autoComplete="off" placeholder="whsec_…" value={form.webhook_secret} onChange={(e) => setForm({ ...form, webhook_secret: e.target.value })} /></Field>
        </div>
        <label style={{ display: "flex", gap: 8, fontSize: 13, marginTop: 4 }}><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />Habilitar la Tienda de pruebas</label>
      </Modal>}
      {quitar && <Modal title="Quitar llaves de pruebas" onClose={() => setQuitar(false)} foot={<><button className="btn btn--ghost" onClick={() => setQuitar(false)}>Cancelar</button><button className="btn btn--crimson" disabled={busy} onClick={remove}>Quitar</button></>}>
        <p style={{ marginTop: 0, fontSize: 13 }}>La Tienda de pruebas deja de funcionar hasta que vuelva a cargarlas. Las llaves de producción no cambian.</p>
      </Modal>}
    </section>
  );
}
