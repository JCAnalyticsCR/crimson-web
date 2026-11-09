/* Mi Tienda -> Tienda de pruebas (/mi-tienda/pruebas), 09/11, pedido de Andrés.
   Compras de PRUEBA con ONVO modo Test, aisladas de lo real: solo dentro del panel y solo para el administrador (la API
   responde 404 a cualquier otro); nunca en StoreHost ni en /tienda. No usa Checkout.tsx ni Google Analytics (sin etiqueta ni eventos
   de compra). Los pedidos de prueba (PRB-0001) viven aparte: no aparecen en Pedidos, Clientes, Inventario, Facturas,
   Reportes ni Inicio, y no envían correos. Al volver de ONVO (?prueba=id&r=ok|cancelado) consulta de inmediato y luego
   cada 5 s, hasta 6 veces. */
import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, downloadFile, parseTs, type ApiError } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, Loading, Modal } from "../../ui/components";
import type { TestRun } from "../accounting/hacienda";
import { crTime, type OnvoTestStatus } from "../settings/OnvoTestCard";
import "./test-shop.css";

type Check = { label: string; ok: boolean | null; detalle: string; origen: string };
type Ev = { at: string; what: string; detail: string; evt?: string; mode?: string; ref?: string };
type TestOrder = {
  id: number; number: string; status: string; status_label: string; method: string; method_label: string; paid_method: string | null;
  currency: string; subtotal: string; tax: string; total: string; lines: { name: string; quantity: number }[]; sessions: string[]; intent_id: string | null;
  checks: Record<string, Check>; timeline: Ev[]; created_at: string; paid_at: string | null; expires_at: string | null; can_reopen: boolean;
  hacienda_test_run_id: number | null; hacienda: TestRun | null; checkout_url?: string;
};
type Home = {
  status: OnvoTestStatus;
  config: { methods: { tarjeta: boolean; sinpe_onvo: boolean }; price: string; product: string; quote: string };
  orders: TestOrder[];
  hacienda: { blocked_reason: string | null; cabys_sugerido: string | null };
  limits: { max_total: string; max_qty: number; max_sessions: number };
};

const METHOD: Record<string, string> = { tarjeta: "Tarjeta", sinpe_onvo: "SINPE Móvil (ONVO)" };
const OPEN = ["nuevo", "en_onvo", "en_proceso"];
const TONE: Record<string, string> = { pagado: "ok", fallido: "bad", revisar: "bad", cancelado: "muted", vencido: "muted", en_proceso: "warn", en_onvo: "info", nuevo: "info" };
const HSTATE: Record<string, string> = { firmado: "Firmada", enviado: "Enviada", procesando: "Procesando", aceptado: "Aceptada", rechazado: "Rechazada", error: "Error", no_concluyente: "No concluyente", sin_respuesta: "Sin respuesta" };
const crc = (v: string | number) => `₡${Number(v).toLocaleString("es-CR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const r2 = (n: number) => Math.round(n * 100) / 100;
const Chip = ({ s, label }: { s: string; label: string }) => <span className={`ts-chip ts-chip--${TONE[s] || "muted"}`}>{label}</span>;

export default function TestShop() {
  const { me, toast } = useSession();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const [home, setHome] = useState<Home | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [sel, setSel] = useState<TestOrder | null>(null);
  const [buy, setBuy] = useState({ method: "tarjeta", quantity: 1 });
  const [cfg, setCfg] = useState<{ tarjeta: boolean; sinpe_onvo: boolean; price: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [limpiar, setLimpiar] = useState(false);
  const [fact, setFact] = useState<{ cabys: string; admin_password: string; confirmo: boolean } | null>(null);
  const [steps, setSteps] = useState<{ label: string; status: string; detail: string }[] | null>(null);
  const timer = useRef<number | null>(null);

  const err = (e: unknown) => toast(e instanceof Error ? e.message : "Error", "bad");
  const load = () => api<Home>("/store/pruebas").then((h) => { setHome(h); setCfg((c) => c ?? { ...h.config.methods, price: h.config.price }); setFailed(null); return h; })
    .catch((e: ApiError) => { setFailed(e.status === 404 ? "Solo un administrador puede usar la Tienda de pruebas." : e.message); return null; });
  const put = (o: TestOrder) => { setSel((s) => (s && s.id === o.id ? o : s)); setHome((h) => (h ? { ...h, orders: h.orders.map((x) => (x.id === o.id ? { ...x, ...o } : x)) } : h)); };
  const consult = (id: number, regreso?: string) => api<TestOrder>(`/store/pruebas/orders/${id}/consultar`, { method: "POST", json: regreso ? { regreso } : {} }).then((o) => { put(o); return o; });

  useEffect(() => {
    const before = document.title;
    document.title = "[PRUEBAS] Tienda de pruebas · Crimson";
    return () => { document.title = before; if (timer.current) window.clearTimeout(timer.current); };
  }, []);

  useEffect(() => {
    if (me?.role !== "admin") return;
    (async () => {
      const h = await load();
      if (!h) return;
      const id = Number(params.get("prueba") || 0);
      const r = params.get("r");
      if (id) {
        setParams({}, { replace: true });
        // regreso de ONVO: consulta ya y luego cada 5 s (hasta 6 veces) mientras siga abierto
        let n = 0;
        const tick = async (regreso?: string) => {
          try {
            const o = await consult(id, regreso);
            setSel(o);
            if (OPEN.includes(o.status) && n++ < 6) timer.current = window.setTimeout(() => tick(), 5000);
          } catch (e) { err(e); }
        };
        tick(r === "cancelado" ? "cancelado" : r === "ok" ? "ok" : undefined);
      }
      // si la sesión se cortó y el regreso se perdió: los abiertos se consultan solos (máximo 5)
      for (const o of h.orders.filter((x) => OPEN.includes(x.status) && x.id !== id).slice(0, 5)) {
        try { await consult(o.id); } catch { /* el detalle queda en su línea de tiempo */ }
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me?.role]);

  if (me && me.role !== "admin") return <><Band /><Empty title="Solo administración" hint="La Tienda de pruebas es solo para el administrador de la empresa." /></>;
  if (failed) return <><Band /><Empty title="Tienda de pruebas" hint={failed} /></>;
  if (!home || !cfg) return <><Band /><Loading /></>;

  const st = home.status;
  const ready = st.api.configured && st.api.enabled;
  const price = Number(cfg.price || 0);
  const total = r2(r2(Number(home.config.price) * buy.quantity) + r2(r2(Number(home.config.price) * buy.quantity) * 0.13));
  const methods = (Object.keys(METHOD) as ("tarjeta" | "sinpe_onvo")[]).filter((k) => home.config.methods[k]);

  const saveCfg = async () => {
    setBusy(true);
    try { const c = await api<Home["config"]>("/store/pruebas/config", { method: "PUT", json: { methods: { tarjeta: cfg.tarjeta, sinpe_onvo: cfg.sinpe_onvo }, price: cfg.price } }); setHome({ ...home, config: c }); setCfg({ ...c.methods, price: c.price }); toast("Tienda de pruebas guardada (la tienda pública no cambia)"); }
    catch (e) { err(e); } finally { setBusy(false); }
  };
  const method = methods.includes(buy.method as "tarjeta") ? buy.method : methods[0];
  const go = async () => {
    setBusy(true);
    try { const o = await api<TestOrder>("/store/pruebas/orders", { method: "POST", json: { method, quantity: buy.quantity } }); if (o.checkout_url) window.location.assign(o.checkout_url); }
    catch (e) { err(e); load(); } finally { setBusy(false); }
  };
  const open = (o: TestOrder) => api<TestOrder>(`/store/pruebas/orders/${o.id}`).then((x) => { setSel(x); put(x); }).catch(err);
  const reopen = async (o: TestOrder) => {
    setBusy(true);
    try { const x = await api<TestOrder>(`/store/pruebas/orders/${o.id}/reabrir`, { method: "POST" }); if (x.checkout_url) window.location.assign(x.checkout_url); }
    catch (e) { err(e); } finally { setBusy(false); }
  };
  const act = async (fn: () => Promise<unknown>) => { setBusy(true); try { await fn(); } catch (e) { err(e); } finally { setBusy(false); } };
  const sendInvoice = async () => {
    if (!sel || !fact) return;
    setBusy(true); setSteps(null);
    try { const o = await api<TestOrder>(`/store/pruebas/orders/${sel.id}/factura-prueba`, { method: "POST", json: fact }); put(o); setSel(o); setFact(null); toast("Factura de prueba enviada a Hacienda (ambiente de Pruebas)"); }
    catch (e) {
      const d = (e as ApiError).data as { steps?: { label: string; status: string; detail: string }[] } | undefined;
      if (d?.steps) setSteps(d.steps);
      err(e);
    } finally { setBusy(false); }
  };

  return (
    <>
      <Band />
      <div className="page-head">
        <div><div className="meta">Mi Tienda · Pruebas</div><h1 className="h1">[PRUEBAS] Tienda de pruebas</h1></div>
        <div className="page-head__actions">
          <button className="btn btn--ghost btn--sm" onClick={() => nav("/mi-tienda")}>Volver a Mi Tienda</button>
          {home.orders.length > 0 && <button className="btn btn--ghost btn--sm" onClick={() => setLimpiar(true)}>Limpiar pruebas</button>}
        </div>
      </div>

      <div className="grid-2 ts-grid">
        <Card title="Estado de pruebas (ONVO modo Test)">
          <div className="ts-stack">
            <div><span className="meta">API · </span>{st.api.unreadable ? <span style={{ color: "var(--bad)" }}>Llave ilegible: vuelva a cargarla</span> : st.api.configured ? <span style={{ color: st.api.enabled ? "var(--ok)" : "var(--warn)" }}>{st.api.secret_mask} · {st.api.enabled ? "habilitada" : "apagada"}</span> : <span className="muted">Sin llave de pruebas</span>}</div>
            <div><span className="meta">Webhook · </span>{st.webhook.set ? (st.webhook.last ? <span style={{ color: "var(--ok)" }}>último aviso {crTime(st.webhook.last.at)}</span> : <span className="muted">cargado, sin avisos todavía</span>) : <span className="muted">sin secreto: confirme con «Consultar ONVO»</span>}</div>
            {st.problems.map((p, i) => <p key={i} className="ts-warn">{p}</p>)}
            <div><button className="btn btn--ghost btn--sm" onClick={() => nav("/ajustes?tab=pagos")}>Llaves en Ajustes → Pagos</button></div>
          </div>
        </Card>
        <Card title="Métodos y precio de prueba">
          <div className="ts-stack">
            <p className="ts-note">Esto no cambia la tienda pública: allí «Tarjeta (ONVO)» sigue como esté en Mi Tienda → Pagos y entrega.</p>
            <label className="ts-check"><input type="checkbox" checked={cfg.tarjeta} onChange={(e) => setCfg({ ...cfg, tarjeta: e.target.checked })} />Tarjeta</label>
            <label className="ts-check"><input type="checkbox" checked={cfg.sinpe_onvo} onChange={(e) => setCfg({ ...cfg, sinpe_onvo: e.target.checked })} />SINPE Móvil (ONVO)</label>
            <Field label="Precio base del artículo de prueba (sin IVA)" hint={`Con IVA 13 %: ${crc(r2(price + r2(price * 0.13)))}. Tope por pedido: ₡2 000.`}><input className="input input--mono" type="number" min={1} max={1769} step="0.01" value={cfg.price} onChange={(e) => setCfg({ ...cfg, price: e.target.value })} /></Field>
            <p className="muted ts-small">ONVO muestra en su página los métodos activos de su cuenta de Test: la casilla indica qué quiere probar.</p>
            <div><button className="btn btn--soft btn--sm" disabled={busy} onClick={saveCfg}>Guardar</button></div>
          </div>
        </Card>
      </div>

      <Card title="Compra de prueba">
        {!ready ? <Empty title="Falta la llave de pruebas" hint="Cargue la llave secreta de pruebas en Ajustes → Pagos → Pasarelas (tarjeta «Pruebas») y marque «Habilitar la Tienda de pruebas»." />
          : methods.length === 0 ? <Empty title="Sin métodos" hint="Encienda Tarjeta o SINPE Móvil arriba." />
          : (
          <div className="ts-buy">
            <div className="ts-buy__item"><b>{home.config.product}</b><span className="muted"> · {crc(home.config.price)} + IVA</span></div>
            <Field label="Cantidad"><input className="input input--mono" type="number" min={1} max={home.limits.max_qty} value={buy.quantity} onChange={(e) => setBuy({ ...buy, quantity: Math.max(1, Math.min(home.limits.max_qty, Number(e.target.value) || 1)) })} /></Field>
            <Field label="Método que va a probar"><select className="select" value={method} onChange={(e) => setBuy({ ...buy, method: e.target.value })}>{methods.map((m) => <option key={m} value={m}>{METHOD[m]}</option>)}</select></Field>
            <div className="ts-buy__total"><span className="meta">Total</span><b className="mono">{crc(total)}</b>{total > 2000 && <span className="ts-warn">Pasa de ₡2 000: baje la cantidad</span>}</div>
            <div><button className="btn btn--crimson" disabled={busy || total > 2000} onClick={go}>Ir a ONVO (pruebas)</button></div>
          </div>
        )}
        <details className="ts-help">
          <summary>Datos de prueba de ONVO</summary>
          <ul>
            <li><span className="mono">4242 4242 4242 4242</span> o <span className="mono">5555 5555 5555 4444</span>: pago aprobado.</li>
            <li><span className="mono">4000 0000 0000 0002</span>: tarjeta rechazada.</li>
            <li><span className="mono">4000 0000 0000 3220</span>: pide la verificación 3DS (complétela y queda aprobado).</li>
            <li>SINPE Móvil <span className="mono">+506 8888 8888</span>: queda «En proceso» unos 15 segundos y después se aprueba.</li>
            <li>Cualquier fecha futura y cualquier código de seguridad. No se cobra dinero real.</li>
          </ul>
        </details>
      </Card>

      <Card title="Pedidos de prueba" flush>
        {home.orders.length === 0 ? <Empty title="Sin pedidos de prueba" hint="Haga una compra de prueba arriba." /> : (
          <table className="table table--stack">
            <thead><tr><th>Pedido</th><th>Creado</th><th>Método</th><th style={{ textAlign: "right" }}>Total</th><th>Estado</th><th /></tr></thead>
            <tbody>{home.orders.map((o) => <tr key={o.id}>
              <td data-label="Pedido" className="mono" style={{ fontWeight: 600 }}>{o.number}</td>
              <td data-label="Creado">{parseTs(o.created_at).toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</td>
              <td data-label="Método">{o.method_label}</td>
              <td data-label="Total" className="mono" style={{ textAlign: "right" }}>{crc(o.total)}</td>
              <td data-label="Estado"><Chip s={o.status} label={o.status_label} />{o.hacienda_test_run_id ? <span className="meta"> · factura T01</span> : null}</td>
              <td className="stack-actions num"><button className="btn btn--ghost btn--sm" onClick={() => open(o)}>Ver</button></td>
            </tr>)}</tbody>
          </table>
        )}
      </Card>

      {sel && <Modal wide title={`[PRUEBAS] ${sel.number}`} onClose={() => { setSel(null); setFact(null); setSteps(null); }} foot={<button className="btn btn--ghost" onClick={() => { setSel(null); setFact(null); setSteps(null); }}>Cerrar</button>}>
        <div className="ts-stack">
          <div className="ts-summary">
            <Chip s={sel.status} label={sel.status_label} />
            <span className="mono">{crc(sel.total)} CRC</span>
            <span className="muted">quiere probar: {sel.method_label}{sel.paid_method ? ` · pagó con: ${METHOD[sel.paid_method] || sel.paid_method}` : ""}</span>
          </div>
          <div className="ts-actions">
            <button className="btn btn--soft btn--sm" disabled={busy} onClick={() => act(() => consult(sel.id))}>Consultar ONVO</button>
            {sel.can_reopen && <button className="btn btn--soft btn--sm" disabled={busy} onClick={() => reopen(sel)}>Reabrir pago</button>}
            {["nuevo", "en_onvo"].includes(sel.status) && <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => act(() => api<TestOrder>(`/store/pruebas/orders/${sel.id}/cancelar`, { method: "POST" }).then(put))}>Cancelar</button>}
            <button className="btn btn--ghost btn--sm" onClick={() => downloadFile(`/store/pruebas/orders/${sel.id}/evidencia`, `evidencia-${sel.number}.json`).catch(err)}>Descargar evidencia</button>
          </div>

          {Object.keys(sel.checks).length > 0 && <>
            <div className="meta">Chequeos del pago</div>
            <ul className="ts-checks">{Object.entries(sel.checks).map(([k, c]) => <li key={k} data-ok={c.ok === null ? "na" : String(c.ok)}><b>{c.ok === true ? "✓" : c.ok === false ? "✗" : "—"} {c.label}</b> <span>{c.detalle}</span> <span className="muted">({c.origen})</span></li>)}</ul>
          </>}

          <div className="meta">Hacienda (ambiente de Pruebas)</div>
          {sel.hacienda ? (
            <div className="ts-link">
              <div className="ts-chain"><span className="mono">{sel.number}</span><span>→</span><span className="mono">{sel.sessions[sel.sessions.length - 1] || "—"}{sel.intent_id && sel.intent_id !== sel.sessions[sel.sessions.length - 1] ? ` / ${sel.intent_id}` : ""}</span><span>→</span><span className="mono">{sel.hacienda.consecutive || "T01"}</span><span>→</span><b>{HSTATE[sel.hacienda.state] || sel.hacienda.state}</b></div>
              {sel.hacienda.clave && <div className="mono ts-small" style={{ wordBreak: "break-all" }}>Clave {sel.hacienda.clave}</div>}
              {sel.hacienda.fe_detalle && <div className="ts-small">{sel.hacienda.fe_detalle}</div>}
              <p className="muted ts-small">Numeración de pruebas (T01): no afecta la numeración real ni el modo de la empresa, pero consume un número de pruebas T01. No se envió ningún correo.</p>
              <div className="ts-actions">
                {sel.hacienda.live && <button className="btn btn--soft btn--sm" disabled={busy} onClick={() => act(() => api<TestOrder>(`/store/pruebas/orders/${sel.id}/factura-prueba/consultar`, { method: "POST" }).then(put))}>Consultar Hacienda</button>}
                {sel.hacienda.has_fe_xml && <button className="btn btn--ghost btn--sm" onClick={() => downloadFile(`/settings/hacienda/prueba-real/${sel.hacienda!.id}/xml/fe`, `${sel.hacienda!.clave || sel.number}-prueba-fe.xml`).catch(err)}>XML enviado</button>}
                {sel.hacienda.has_fe_respuesta && <button className="btn btn--ghost btn--sm" onClick={() => downloadFile(`/settings/hacienda/prueba-real/${sel.hacienda!.id}/xml/fe-respuesta`, `${sel.hacienda!.clave || sel.number}-respuesta.xml`).catch(err)}>Respuesta de Hacienda</button>}
              </div>
            </div>
          ) : sel.status !== "pagado" ? <p className="muted ts-small">Cuando el pedido quede Pagado puede emitir su factura de prueba (T01) en el ambiente de Pruebas de Hacienda.</p>
            : !fact ? <div><button className="btn btn--soft btn--sm" disabled={!!home.hacienda.blocked_reason && !/curso/.test(home.hacienda.blocked_reason)} onClick={() => setFact({ cabys: home.hacienda.cabys_sugerido || "", admin_password: "", confirmo: false })}>Factura de prueba en Hacienda</button>{home.hacienda.blocked_reason && <p className="ts-warn">{home.hacienda.blocked_reason}</p>}</div>
            : (
              <div className="ts-stack">
                <p className="ts-small" style={{ margin: 0 }}>Se firma con el certificado de <b>Pruebas</b> y se envía al ambiente de Pruebas de Hacienda: a nombre de su propia empresa, con consecutivo T01, sin correos y sin cambiar el modo. Comparte el cupo con «Prueba real con Hacienda» (3 por hora, 10 por día, una a la vez).</p>
                <div className="grid-2">
                  <Field label="CABYS de servicio (13 dígitos)"><input className="input input--mono" value={fact.cabys} onChange={(e) => setFact({ ...fact, cabys: e.target.value })} /></Field>
                  <Field label="Su contraseña de administrador"><input className="input" type="password" autoComplete="current-password" value={fact.admin_password} onChange={(e) => setFact({ ...fact, admin_password: e.target.value })} /></Field>
                </div>
                <label className="ts-check"><input type="checkbox" checked={fact.confirmo} onChange={(e) => setFact({ ...fact, confirmo: e.target.checked })} />Entiendo que se enviará un comprobante de prueba a Hacienda (ambiente de Pruebas)</label>
                {steps && <ul className="ts-checks">{steps.map((s, i) => <li key={i} data-ok={s.status === "ok" ? "true" : s.status === "falla" ? "false" : "na"}><b>{s.label}</b> <span>{s.detail}</span></li>)}</ul>}
                <div className="ts-actions"><button className="btn btn--crimson btn--sm" disabled={busy || !fact.confirmo || !fact.admin_password} onClick={sendInvoice}>Enviar factura de prueba</button><button className="btn btn--ghost btn--sm" onClick={() => { setFact(null); setSteps(null); }}>Cancelar</button></div>
              </div>
            )}

          <div className="meta">Línea de tiempo</div>
          <ol className="ts-timeline">{[...sel.timeline].reverse().map((e, i) => <li key={i}><span className="muted ts-small">{crTime(e.at)}</span> <b>{e.what}</b>{e.detail ? <> · <span>{e.detail}</span></> : null}{e.evt ? <span className="mono muted ts-small"> · {e.evt}{e.mode ? ` (${e.mode})` : ""}</span> : null}</li>)}</ol>
        </div>
      </Modal>}

      {limpiar && <Modal title="Limpiar pruebas" onClose={() => setLimpiar(false)} foot={<><button className="btn btn--ghost" onClick={() => setLimpiar(false)}>Cancelar</button><button className="btn btn--crimson" disabled={busy} onClick={() => act(async () => { await api("/store/pruebas/orders", { method: "DELETE" }); setLimpiar(false); setSel(null); toast("Pedidos de prueba borrados"); load(); })}>Borrar pedidos de prueba</button></>}>
        <p style={{ marginTop: 0, fontSize: 13 }}>Se borran solo los pedidos de prueba ({home.orders.length}). Descargue antes la evidencia que necesite. Nada real cambia y las facturas de prueba de Hacienda siguen en Ajustes → Hacienda.</p>
      </Modal>}
    </>
  );
}

function Band() {
  return <div className="ts-band" role="status"><span>PRUEBAS · no se cobra · ONVO modo Test</span></div>;
}
