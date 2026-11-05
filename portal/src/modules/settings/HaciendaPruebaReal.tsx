/* Ajustes -> Hacienda -> «Prueba real con Hacienda» (ambiente de Pruebas). Envía una factura de ₡1 000 + IVA de la
   empresa a sí misma, firmada con el certificado de Pruebas, y opcionalmente el mensaje receptor; muestra lo que se
   envió, si Hacienda lo aceptó o rechazó y por qué. No cambia el modo de la empresa ni usa la numeración real.
   La consulta del resultado la hace el servidor una vez por llamada (POST /{id}/consultar): aquí solo se pide cada
   5 s los primeros 2 min y cada 20 s después, hasta 15 min desde el envío, y solo con la pestaña visible. */
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, downloadFile, parseTs } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Field, Icon, I } from "../../ui/components";
import type { TestRun, TestRunList, TestStep } from "../accounting/hacienda";
import { TestResult } from "./HaciendaTab";

const URL = "/settings/hacienda/prueba-real";
const FAST_MS = 5_000, SLOW_MS = 20_000, FAST_FOR = 2 * 60_000, STOP_AFTER = 15 * 60_000, RESUME_SIN_RESPUESTA = 24 * 3600_000;
const RUTA_MR = "Ruta de consulta del mensaje receptor sin confirmar";

const STATE_LABEL: Record<TestRun["state"], string> = {
  firmado: "Firmada, por confirmar el envío", enviado: "Enviada, esperando respuesta", procesando: "Hacienda la está procesando",
  aceptado: "Aceptada", rechazado: "Rechazada", error: "Error", no_concluyente: "No concluyente", sin_respuesta: "Sin respuesta",
};
const MR_LABEL: Record<string, string> = {
  firmado: "firmado, por confirmar el envío", enviado: "enviado, esperando respuesta", procesando: "Hacienda lo está procesando",
  aceptado: "aceptado por Hacienda", rechazado: "rechazado por Hacienda", error: "error", sin_respuesta: "sin respuesta en 15 minutos",
};
/* estilos propios: accounting.css (acc-check, acc-warn) no siempre está cargado en Ajustes */
const CHECK = { display: "flex", gap: 8, alignItems: "flex-start", fontSize: 13, margin: "8px 0 0" } as const;
const WARN = { padding: "10px 14px", borderRadius: 10, background: "var(--warn-soft)", border: "1px solid var(--warn)", fontSize: 13, marginTop: 10 } as const;
const sentAt = (r: TestRun) => parseTs(r.fe_body_sent_at || r.created_at).getTime();
const esRuta = (r: TestRun) => r.state === "error" && /entrada de Hacienda/i.test(r.fe_post_cause || "");

function Verdict({ run }: { run: TestRun }) {
  const box = (color: string, title: string, body?: ReactNode) => (
    <div role="status" style={{ border: `2px solid ${color}`, borderRadius: 12, padding: "14px 16px", margin: "12px 0" }}>
      <div style={{ fontSize: 18, fontWeight: 700, color }}>{title}</div>
      {body && <div style={{ fontSize: 13.5, marginTop: 6 }}>{body}</div>}
    </div>
  );
  const motivo = run.fe_detalle || run.fe_post_cause;
  switch (run.state) {
    case "aceptado":
      return box("var(--ok)", "✓ Hacienda aceptó la factura de prueba", <>Hacienda validó el XML, la firma y los datos de su empresa. Avísenos para planear el paso a Producción, que es un paso aparte y lo activamos juntos.{run.fe_detalle && <><br /><span className="muted">Mensaje de Hacienda: {run.fe_detalle}</span></>}</>);
    case "rechazado":
      return box("var(--bad)", "✗ Hacienda rechazó la factura de prueba", <><b>Motivo de Hacienda:</b> {motivo || "Hacienda no indicó el motivo"}<br /><span className="muted">Corrija el dato que indica y repita la prueba, o envíenos el motivo y la clave.</span></>);
    case "sin_respuesta":
      return run.fe_llego
        ? box("var(--warn)", "Sin respuesta de Hacienda en 15 minutos", "La factura llegó a Hacienda, pero todavía no da el resultado. Toque «Volver a consultar» más tarde; no hace falta enviar otra.")
        : box("var(--warn)", "No se pudo confirmar el envío", <>No logramos confirmar que la factura llegara a Hacienda (Hacienda no contestó el envío). Puede hacer otra prueba.{run.fe_post_cause && <><br /><span className="muted">{run.fe_post_cause}</span></>}</>);
    case "no_concluyente":
      return box("var(--warn)", "No concluyente: Hacienda ya tenía un comprobante con esos datos", <>{run.fe_post_cause && <>{run.fe_post_cause}<br /></>}Avísenos para revisar la numeración de pruebas.</>);
    case "error":
      return esRuta(run)
        ? box("var(--bad)", "✗ Error de dirección (403)", <>{run.fe_post_cause}<br /><span className="muted">Es un problema de configuración del servidor, no de sus datos. Avísenos.</span></>)
        : box("var(--bad)", "✗ La prueba no se pudo completar", motivo || "Error sin detalle");
    default:
      return box("var(--warn)", `Esperando la respuesta de Hacienda (consulta n.º ${run.polls || 0})`, <>{STATE_LABEL[run.state]}. La respuesta tarda entre unos segundos y algunos minutos; puede salir de la página y, cuando vuelva, la consulta sigue sola.{run.fe_post_cause && <><br /><span className="muted">{run.fe_post_cause}</span></>}</>);
  }
}

export default function HaciendaPruebaReal({ canConfigure }: { canConfigure: boolean }) {
  const { toast } = useSession();
  const [list, setList] = useState<TestRunList | null>(null);
  const [run, setRun] = useState<TestRun | null>(null); // la corrida que se muestra (la última)
  const [form, setForm] = useState({ cabys: "", con_mr: true, admin_password: "", confirmo: false });
  const [sending, setSending] = useState(false);
  const [refused, setRefused] = useState<TestStep[] | null>(null);
  const resumed = useRef<Set<number>>(new Set()); // las 'sin_respuesta' ya consultadas en esta vista
  const timer = useRef<number | null>(null);
  const alive = useRef(true);
  const shown = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const l = await api<TestRunList>(URL);
    if (!alive.current) return;
    setList(l);
    setRun(l.runs[0] ?? null);
    setForm((f) => (f.cabys ? f : { ...f, cabys: l.cabys_sugerido || "" }));
    // al volver a la página: una 'sin_respuesta' de menos de 24 h se consulta una vez más (una respuesta tardía la cierra)
    const r0 = l.runs[0];
    if (r0 && r0.state === "sin_respuesta" && l.can_configure && !resumed.current.has(r0.id) && Date.now() - sentAt(r0) < RESUME_SIN_RESPUESTA) {
      resumed.current.add(r0.id);
      try { const v = await api<TestRun>(`${URL}/${r0.id}/consultar`, { method: "POST" }); if (alive.current) setRun(v); } catch { /* se queda con lo que había */ }
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    load().catch((e) => toast(e instanceof Error ? e.message : "Error", "bad"));
    return () => { alive.current = false; if (timer.current) window.clearTimeout(timer.current); };
  }, [load]); // eslint-disable-line react-hooks/exhaustive-deps

  /* consulta mientras la corrida esté viva: se retoma sola al montar y se detiene al desmontar o al terminar */
  const liveId = run?.live ? run.id : null;
  const liveSent = run?.live ? (run.state === "aceptado" && run.mr_sent_at ? parseTs(run.mr_sent_at).getTime() : sentAt(run)) : 0; // con el MR pendiente, los 15 min cuentan desde el MR
  useEffect(() => {
    if (!liveId) return;
    let stop = false;
    const tick = async () => {
      if (stop) return;
      const elapsed = Date.now() - liveSent;
      if (elapsed > STOP_AFTER) { load().catch(() => undefined); return; } // el servidor la cierra como «Sin respuesta»
      if (document.visibilityState === "visible") {
        try {
          const v = canConfigure ? await api<TestRun>(`${URL}/${liveId}/consultar`, { method: "POST" }) : await api<TestRun>(`${URL}/${liveId}`);
          if (stop || !alive.current) return;
          setRun(v);
          if (!v.live) { load().catch(() => undefined); return; }
        } catch { /* un fallo de red no corta la consulta */ }
      }
      if (!stop) timer.current = window.setTimeout(tick, Date.now() - liveSent < FAST_FOR ? FAST_MS : SLOW_MS);
    };
    timer.current = window.setTimeout(tick, FAST_MS);
    return () => { stop = true; if (timer.current) window.clearTimeout(timer.current); };
  }, [liveId, liveSent, canConfigure, load]);

  if (!list) return <Card title="Prueba real con Hacienda (ambiente de Pruebas)"><p className="muted" style={{ margin: 0 }}>Cargando…</p></Card>;
  const liveRun = list.runs.find((r) => r.live) || (run?.live ? run : null);
  const blocked = list.blocked_reason || (liveRun ? "Ya hay una prueba en curso: espere su resultado" : null);
  const cabysOk = /^[5-9]\d{12}$/.test(form.cabys.replace(/\D/g, "")) && form.cabys.replace(/\D/g, "").length === 13;
  const canSend = canConfigure && !blocked && !sending && cabysOk && !!form.admin_password && form.confirmo;

  const send = async () => {
    setSending(true); setRefused(null);
    try {
      const r = await api<TestRun>(URL, { method: "POST", json: { admin_password: form.admin_password, cabys: form.cabys.replace(/\D/g, ""), con_mensaje_receptor: form.con_mr, confirmo: form.confirmo } });
      setForm((f) => ({ ...f, admin_password: "", confirmo: false }));
      setRun(r);
      toast(r.state === "enviado" ? "Factura de prueba enviada a Hacienda" : "Prueba registrada", r.state === "error" || r.state === "rechazado" ? "bad" : "ok");
      await load(); // trae la lista con esta corrida arriba (y el motivo si quedó bloqueada)
    } catch (e) {
      if (e instanceof ApiError && e.status === 422 && e.data && typeof e.data === "object" && Array.isArray((e.data as { steps?: unknown }).steps)) {
        setRefused((e.data as { steps: TestStep[] }).steps);
      }
      toast(e instanceof Error ? e.message : "Error", "bad");
    } finally { setSending(false); }
  };
  const again = async (id: number) => {
    try { const v = await api<TestRun>(`${URL}/${id}/consultar`, { method: "POST" }); setRun(v); if (!v.live) await load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const dl = (r: TestRun, which: "fe" | "fe-respuesta" | "mr" | "mr-respuesta") =>
    downloadFile(`${URL}/${r.id}/xml/${which}`, `${r.clave || r.id}-prueba-${which}.xml`).catch((e) => toast(e.message, "bad"));
  const copy = (txt: string) => navigator.clipboard?.writeText(txt).then(() => toast("Clave copiada")).catch(() => toast("No se pudo copiar", "bad"));

  return (
    <Card title="Prueba real con Hacienda (ambiente de Pruebas)">
      <p style={{ fontSize: 13.5, marginTop: 0 }}>
        Envía a Hacienda, en el <b>ambiente de Pruebas</b>, una factura de <b>₡1 000 + IVA (₡1 130)</b> de la empresa a sí misma, firmada con su certificado de Pruebas, y si lo marca, el mensaje receptor de aceptación sobre esa factura.
        Así ve qué se envió, si Hacienda lo aceptó o lo rechazó y por qué.
      </p>
      <p className="muted" style={{ fontSize: 13, marginTop: 0, marginBottom: 14 }}>
        No cambia el modo de la empresa, no crea facturas en Crimson, no envía correos a nadie y no usa su numeración real (usa la de pruebas, T01 y T05). Hasta 3 pruebas por hora y 10 por día, una a la vez.
      </p>

      {canConfigure && (
        <>
          <div className="grid-2">
            <Field label="CABYS del servicio" hint="Debe ser un servicio: empieza con 5, 6, 7, 8 o 9, existe en el catálogo de Hacienda y tiene IVA del 13 %.">
              <input className="input input--mono" style={{ textAlign: "left" }} inputMode="numeric" maxLength={13} disabled={sending} value={form.cabys} onChange={(e) => setForm({ ...form, cabys: e.target.value.replace(/\D/g, "").slice(0, 13) })} />
            </Field>
            <Field label="Su contraseña de administrador">
              <input className="input" type="password" autoComplete="new-password" disabled={sending} value={form.admin_password} onChange={(e) => setForm({ ...form, admin_password: e.target.value })} />
            </Field>
          </div>
          <label style={CHECK}><input type="checkbox" disabled={sending} checked={form.con_mr} onChange={(e) => setForm({ ...form, con_mr: e.target.checked })} />Responder también con un mensaje receptor de aceptación</label>
          <label style={CHECK}><input type="checkbox" disabled={sending} checked={form.confirmo} onChange={(e) => setForm({ ...form, confirmo: e.target.checked })} />Entiendo que se enviará un comprobante de prueba a Hacienda</label>
          <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap", alignItems: "center" }}>
            <button className="btn btn--crimson btn--sm" disabled={!canSend} onClick={send}>{sending ? "Enviando a Hacienda…" : "Enviar factura de prueba a Hacienda"}</button>
            {sending && <span className="muted" style={{ fontSize: 12 }}>Puede tardar hasta un minuto. No lo toque dos veces.</span>}
            {!sending && form.cabys && !cabysOk && <span style={{ fontSize: 12, color: "var(--bad)" }}>El CABYS debe tener 13 dígitos y empezar con 5, 6, 7, 8 o 9.</span>}
          </div>
        </>
      )}
      {blocked && <div role="status" style={WARN}>{blocked}</div>}
      {refused && <TestResult ok={false} at={null} steps={refused} title="✗ No se envió nada: corrija lo marcado y vuelva a intentar" />}

      {run && (
        <div ref={shown} style={{ marginTop: 6, scrollMarginTop: 80 }}>
          <Verdict run={run} />
          {run.con_mr && run.state === "aceptado" && (
            <div style={{ fontSize: 13.5, margin: "0 0 10px" }}>
              <b>Mensaje receptor{run.mr_consecutive ? ` ${run.mr_consecutive}` : ""}:</b> {run.mr_estado ? MR_LABEL[run.mr_estado] || run.mr_estado : "por enviar"}
              {run.mr_detalle && <> · <span className="muted">{run.mr_detalle}</span></>}
              {run.mr_detalle === RUTA_MR && <div className="muted" style={{ fontSize: 12 }}>No es una falla de su factura; nos sirve para ajustar Crimson.</div>}
            </div>
          )}
          {run.clave && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
              <span className="muted">Clave:</span><span className="mono" style={{ wordBreak: "break-all" }}>{run.clave}</span>
              <button className="btn btn--ghost btn--sm" onClick={() => copy(run.clave || "")}><Icon d={I.copy} />Copiar</button>
            </div>
          )}
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
            {parseTs(run.created_at).toLocaleString("es-CR")}{run.created_by_name ? ` · ${run.created_by_name}` : ""}{run.consecutive ? ` · consecutivo de pruebas ${run.consecutive}` : ""}{run.fe_resent ? " · se reenvió una vez (mismos datos)" : ""}
          </div>
          {canConfigure && (
            <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
              {run.state === "sin_respuesta" && <button className="btn btn--soft btn--sm" onClick={() => again(run.id)}><Icon d={I.refresh} />Volver a consultar</button>}
              {run.has_fe_xml && <button className="btn btn--ghost btn--sm" onClick={() => dl(run, "fe")}>XML enviado</button>}
              {run.has_fe_respuesta && <button className="btn btn--ghost btn--sm" onClick={() => dl(run, "fe-respuesta")}>Respuesta de Hacienda</button>}
              {run.has_mr_xml && <button className="btn btn--ghost btn--sm" onClick={() => dl(run, "mr")}>Mensaje receptor</button>}
              {run.has_mr_respuesta && <button className="btn btn--ghost btn--sm" onClick={() => dl(run, "mr-respuesta")}>Respuesta al mensaje receptor</button>}
            </div>
          )}
          {!!run.steps.length && <TestResult ok={!run.steps.some((s) => s.status === "falla")} at={run.created_at} steps={run.steps} title="Lo que se revisó y se envió" />}
        </div>
      )}

      {list.runs.length > 1 && (
        <>
          <div className="meta" style={{ marginTop: 12 }}>Últimas pruebas</div>
          <table className="table table--stack">
            <thead><tr><th>Fecha</th><th>Resultado</th><th>Mensaje receptor</th><th>Clave</th><th /></tr></thead>
            <tbody>
              {list.runs.slice(0, 5).map((r) => (
                <tr key={r.id}>
                  <td data-label="Fecha" style={{ whiteSpace: "nowrap" }}>{parseTs(r.created_at).toLocaleString("es-CR")}</td>
                  <td data-label="Resultado">{STATE_LABEL[r.state]}{r.state === "rechazado" && r.fe_detalle ? ` · ${r.fe_detalle.length > 80 ? `${r.fe_detalle.slice(0, 80)}…` : r.fe_detalle}` : ""}</td>
                  <td data-label="Mensaje receptor">{r.con_mr ? (r.mr_estado ? MR_LABEL[r.mr_estado] || r.mr_estado : "—") : "no"}</td>
                  <td data-label="Clave" className="mono" style={{ fontSize: 11, wordBreak: "break-all" }}>{r.clave ? `…${r.clave.slice(-12)}` : "—"}</td>
                  <td className="stack-actions">
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
                      <button className="btn btn--ghost btn--sm" onClick={() => { setRun(r); shown.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>Ver</button>
                      {canConfigure && r.has_fe_xml && <button className="btn btn--ghost btn--sm" onClick={() => dl(r, "fe")}>XML</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Card>
  );
}
