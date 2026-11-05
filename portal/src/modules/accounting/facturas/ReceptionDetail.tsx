/* «Recepción de factura electrónica», con los bloques y las palabras de Fygaro (lo que Andrés usa a diario):
   Información del emisor · Estado («Acción realizada» y «Notificación a Hacienda») · Información del gasto (clave del
   documento tal cual, totales, condición del IVA, código de actividad, impuesto por acreditar, gasto aplicable) ·
   Documentos XML (Descargar XML / respuesta XML / todos). Lo que Crimson hace de más se queda a la derecha: responder
   (con la respuesta sugerida, reunion 2 pedido 4), historial por intento y acciones, gasto, inventario, descartar.
   05/11: la clave se ve completa y sin marcas; el numero propio de cada respuesta (…05) y el modo crudo solo en
   «Datos técnicos» (plegado), porque en primer plano confundian. En Pruebas la decision queda como ensayo y se envia
   despues con «Enviar en Producción» (la misma decision, con la casilla de Fygaro). */
import { HaciendaMensaje, parseHacienda } from "../../../ui/HaciendaMensaje";
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { STATUS, api, downloadFile, fmtDate, fmtMoney, openFile, parseTs, uploadFile } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Card, Field, I, Icon, Loading, Modal } from "../../../ui/components";
import { ACTIONS, ACTION_LABEL, DOC_TYPES, FYGARO, IVA_CONDITIONS, calcLive, ivaCode, ivaLabel, plazo, type IvaCondition, type ReceptionDetail as Detail, type ResponseAttempt } from "../hacienda";
import { EstadoCell, approveLabel, approvedToast } from "./Inbox";
import StockIn from "./StockIn";

type Activity = { id: number; code: string; description: string | null; is_default: boolean };
type Cat = { id: number; name: string };
const n = (v: string | number | null | undefined) => Number(v ?? 0) || 0;
const when = (s: string | null) => (s ? parseTs(s).toLocaleString("es-CR", { dateStyle: "medium", timeStyle: "short" }) : "");
const MODO: Record<string, string> = { simulado: "Simulado", pruebas: "Pruebas", produccion: "Producción", historico: "Fygaro" };
const ACTION_OF: Record<string, string> = { "1": "aceptada", "2": "parcial", "3": "rechazada" };
const o = (n: number) => ({ "--o": n }) as CSSProperties;

/** Estado de un intento en palabras; nada de Pruebas se pinta en rojo (no es la Hacienda real). */
function intentoEstado(r: ResponseAttempt): { label: string; tone: string } {
  if (r.status === "ensayo") return { label: "Ensayo · no se envió", tone: "muted" };
  if (r.status === "historico") return { label: "Respondido en Fygaro", tone: "ok" };
  if (r.mode === "pruebas") return { label: r.status === "por_enviar" || r.status === "enviando" ? "Pruebas · por enviar" : "Ambiente de Pruebas · sin validez fiscal", tone: "muted" };
  const s = STATUS[r.status] ?? { label: r.status, tone: "muted" };
  return { label: r.status === "aceptado" ? "Aprobado" : s.label, tone: s.tone };
}

export default function ReceptionDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const { toast, allows } = useSession();
  const [d, setD] = useState<Detail | null>(null);
  const [acts, setActs] = useState<Activity[]>([]);
  const [cats, setCats] = useState<Cat[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [discard, setDiscard] = useState<string | null>(null);
  const [modal, setModal] = useState<"reopen" | "produccion" | null>(null);
  const [confirmFygaro, setConfirmFygaro] = useState(false);
  const attachRef = useRef<HTMLInputElement>(null);
  const [f, setF] = useState({ action: "aceptada", cond: "credito" as IvaCondition, activity: "", detail: "", accepted_total: "", accepted_tax: "", credit: "", expense: "create" as "create" | "link" | "none", link: "", category: "" });

  const load = () => api<Detail>(`/reception/${id}`).then(setD).catch((e) => toast(e.message, "bad"));
  useEffect(() => { load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    api<Activity[]>("/fiscal/activities").then(setActs).catch(() => setActs([]));
    api<Cat[]>("/expense-categories?active=1").then(setCats).catch(() => setCats([]));
  }, []);
  useEffect(() => {
    if (!d) return;
    const def = acts.find((a) => a.is_default)?.code || acts[0]?.code || "";
    const cand = d.expense_candidates[0];
    const p = d.proposal;
    // el gasto como lo sugiere la plataforma: vincular el que ya existe, registrar uno (con su categoria) o ninguno (NC)
    const exp = p ? (p.link_expense_id ? "link" : p.create_expense ? (cand ? "link" : "create") : "none") : cand ? "link" : "create";
    setF((x) => ({
      ...x,
      activity: x.activity || p?.activity_code || def,
      expense: exp,
      link: p?.link_expense_id ? String(p.link_expense_id) : cand ? String(cand.id) : "",
      category: x.category || (p?.category_id ? String(p.category_id) : ""),
      action: p?.action || x.action,
      cond: (p?.iva_condition as IvaCondition) || x.cond,
      accepted_total: x.accepted_total || String(n(d.total)),
      accepted_tax: x.accepted_tax || String(n(d.iva_total)),
    }));
  }, [d, acts]);

  const live = useMemo(() => d ? calcLive(f.action, f.cond, n(d.iva_total), n(d.total), n(f.accepted_total), n(f.accepted_tax), f.credit === "" ? null : n(f.credit)) : null, [d, f]);
  if (!d) return <Loading />;
  const cur = d.currency;
  const needsDetail = f.action !== "aceptada";
  const showActivity = f.action !== "rechazada" && f.cond !== "proporcional";
  const saveLabel = d.mode === "produccion" ? "Guardar y enviar a Hacienda" : d.mode === "pruebas" ? "Guardar (ensayo en Pruebas, no se envía)" : "Guardar (simulado)";

  const act = async (path: string, body?: unknown, okMsg?: string) => {
    setBusy(true);
    try { setD(await api<Detail>(`/reception/${d.id}/${path}`, { method: "POST", json: body ?? {} })); if (okMsg) toast(okMsg); return true; }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); return false; }
    finally { setBusy(false); }
  };
  const respond = async () => {
    setError(null);
    const body: Record<string, unknown> = { action: f.action, iva_condition: f.cond, detail: f.detail.trim() || null, send_now: true };
    if (showActivity) body.activity_code = f.activity || null;
    if (f.action === "parcial") { body.accepted_total = f.accepted_total; body.accepted_tax = f.accepted_tax; }
    if (f.cond === "credito_parcial" || (f.cond === "bienes_capital" && f.credit !== "")) body.credit_amount = f.credit || null;
    if (f.action !== "rechazada") {
      body.create_expense = f.expense === "create";
      if (f.expense === "link" && f.link) body.link_expense_id = Number(f.link);
      if (f.expense === "create" && f.category) body.category_id = Number(f.category);
    } else body.create_expense = false;
    setBusy(true);
    try {
      const r = await api<Detail>(`/reception/${d.id}/respond`, { method: "POST", json: body });
      setD(r);
      toast(approvedToast(r.hacienda_status).replace("aprobada", "guardada"));
    } catch (e) { setError(e instanceof Error ? e.message : "Error"); }
    finally { setBusy(false); }
  };
  const approve = async () => {
    setError(null);
    setBusy(true);
    try {
      const r = await api<Detail>(`/reception/${d.id}/approve`, { method: "POST", json: { fingerprint: d.proposal?.fingerprint ?? null } });
      setD(r);
      toast(approvedToast(r.hacienda_status));
    } catch (e) { setError(e instanceof Error ? e.message : "Error"); }
    finally { setBusy(false); }
  };
  const addExpense = async () => {
    setBusy(true);
    try { const r = await api<{ expense_id: number; document: Detail }>(`/reception/${d.id}/expense`, { method: "POST", json: {} }); setD(r.document); toast("Gasto registrado"); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  const attach = async (fl: File | undefined) => {
    if (!fl) return;
    setBusy(true);
    try { setD(await uploadFile<Detail>(`/reception/${d.id}/attach`, fl)); toast("Archivo adjuntado"); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); if (attachRef.current) attachRef.current.value = ""; }
  };
  const closeModal = () => { setModal(null); setConfirmFygaro(false); };
  // respondido (o historico) aceptado sin gasto: un recurrente o manual que ya existia se vincula en vez de duplicarse (R13)
  const canLink = !d.can_respond && !d.expense_id && (d.action === "aceptada" || d.action === "parcial") && d.expense_candidates.length > 0 && allows("accounting.editar");
  const bajar = (which: string, name: string) => downloadFile(`/reception/${d.id}/file/${which}`, name).catch((e) => toast(e.message, "bad"));
  const last = d.responses[d.responses.length - 1];
  const ensayo = last?.status === "ensayo";
  // §8.3: ya respondido (aunque el ultimo mensaje quedara rechazado o con error) se ve primero el historial con el
  // motivo de Hacienda; el formulario aparece despues de "Responder de nuevo" (retry -> por_revisar).
  const showForm = d.can_respond && d.status !== "respondido" && allows("accounting.editar");
  const pendiente = d.status === "por_revisar";

  // Informacion del gasto: lo respondido o, si esta pendiente, lo que quedaria con la respuesta del formulario
  const gCond = pendiente ? (f.action === "rechazada" ? null : f.cond) : d.action === "rechazada" ? null : d.iva_condition;
  const gAct = pendiente ? (showActivity ? f.activity : null) : d.activity_code;
  const gCredit = pendiente ? (f.action === "rechazada" ? null : live?.credit ?? null) : d.credit_amount === null ? null : n(d.credit_amount);
  const gAplic = pendiente ? (f.action === "rechazada" ? null : live?.applicable ?? null) : d.applicable_amount === null ? null : n(d.applicable_amount);
  const tentativo = pendiente && d.doc_type !== "TE" ? <span className="muted" style={{ fontSize: 12 }}> (si se responde así)</span> : null;
  const dim = pendiente ? { opacity: 0.75 } : undefined;

  const timeline = (
    <ol className="acc-timeline">
      {d.responses.map((r, i) => {
        const vigente = i === d.responses.length - 1 && d.status === "respondido";
        const est = intentoEstado(r);
        const accion = ACTION_LABEL[ACTION_OF[r.message_code] || ""] || "—";
        return (
          <li key={r.id}>
            <b>Intento {r.attempt}</b> · {accion}{r.iva_condition ? ` · ${ivaLabel(r.iva_condition)}` : ""} · {MODO[r.mode] || r.mode}{" "}
            <span className={`badge badge--${vigente ? "info" : "muted"}`}>{vigente ? "vigente" : "reemplazado"}</span>
            <div style={{ marginTop: 4 }}><span className={`badge badge--${est.tone}`}>{est.label}</span>
              {r.hacienda_detail && r.status !== "ensayo" && !parseHacienda(r.hacienda_detail).items.length && <span className="muted"> {r.hacienda_detail}</span>}
              {r.last_error && r.mode === "produccion" && <span style={{ color: "var(--bad)" }}> · {r.last_error}</span>}
              {r.last_error && r.mode !== "produccion" && <span className="muted"> · {r.last_error}</span>}
            </div>
            {r.hacienda_detail && parseHacienda(r.hacienda_detail).items.length > 0 && <div style={{ marginTop: 6 }}><HaciendaMensaje msg={r.hacienda_detail} status={r.status === "aceptado" ? "aceptada" : r.status} modo={r.mode} /></div>}
            <div className="muted">{r.status === "ensayo" ? "Guardado" : "Respondido"} · {when(r.created_at)}{r.sent_at ? ` → Enviado · ${when(r.sent_at)}` : ""}</div>
            {r.credit_amount !== null && <div className="muted">Impuesto por acreditar {fmtMoney(r.credit_amount, cur)} · gasto aplicable {fmtMoney(r.applicable_amount, cur)}</div>}
            {r.detail && <div className="muted">“{r.detail}”</div>}
            <details style={{ marginTop: 4 }}>
              <summary className="meta" style={{ cursor: "pointer" }}>Datos técnicos</summary>
              <dl className="acc-kv" style={{ marginTop: 6 }}>
                <dt>Número de esta respuesta</dt><dd className="mono" title="Consecutivo propio del mensaje receptor (05 aceptación, 06 parcial, 07 rechazo). La clave de la factura del proveedor va dentro del archivo, igual que en su XML.">{r.consecutive}</dd>
                <dt>Modo</dt><dd className="mono">{r.mode}</dd>
                <dt>Estado</dt><dd className="mono">{r.status}</dd>
                <dt>Envíos a Hacienda</dt><dd>{r.attempts}</dd>
                {r.activity_code && <><dt>Actividad</dt><dd className="mono">{r.activity_code}</dd></>}
              </dl>
            </details>
          </li>
        );
      })}
    </ol>
  );

  const estadoCard = (
    <Card title={FYGARO.estado}>
      <dl className="acc-kv">
        <dt>{FYGARO.accion}</dt><dd><EstadoCell e={d.accion} /></dd>
        <dt>{FYGARO.notificacion}</dt><dd><EstadoCell e={d.notificacion} /></dd>
        {(pendiente || d.notificacion.key === "solo_pruebas") && d.deadline && (
          <><dt>Fecha límite</dt><dd style={{ color: (d.business_days_left ?? 9) <= 2 ? "var(--bad)" : undefined, fontWeight: (d.business_days_left ?? 9) <= 2 ? 700 : undefined }}>{fmtDate(d.deadline)} · {plazo(d.business_days_left)}</dd></>
        )}
      </dl>
      {d.notificacion.key === "pruebas_vencida" && allows("accounting.crear") && (
        <div style={{ marginTop: 10 }}><Link className="btn btn--soft btn--sm" to="/importar?kind=recepciones"><Icon d={I.upload} />Importar el mensaje de Fygaro</Link></div>
      )}
      {d.notificacion.key === "ya_recibido" && allows("accounting.crear") && (
        <div style={{ marginTop: 10 }}><Link className="btn btn--soft btn--sm" to="/importar?kind=recepciones"><Icon d={I.upload} />Importar el mensaje de Fygaro</Link></div>
      )}
    </Card>
  );

  const emisorCard = (
    <Card title={FYGARO.emisor}>
      <dl className="acc-kv">
        <dt>Nombre</dt><dd><b>{d.issuer_name || "—"}</b></dd>
        <dt>Cédula</dt><dd className="mono">{d.issuer_id || "—"}</dd>
      </dl>
    </Card>
  );

  const gastoCard = (
    <Card title={FYGARO.gasto}>
      <dl className="acc-kv">
        <dt className="acc-kv__full">{FYGARO.clave}</dt>
        <dd className="acc-kv__full" style={{ display: "flex", gap: 6, alignItems: "flex-start", minWidth: 0 }}>
          <span className="mono" style={{ fontSize: 12, wordBreak: "break-all", minWidth: 0 }} title="Clave del comprobante del proveedor, tal cual viene en su XML">{d.clave}</span>
          <button className="btn btn--ghost btn--sm" style={{ flex: "none" }} aria-label="Copiar la clave" onClick={() => { navigator.clipboard.writeText(d.clave); toast("Clave copiada"); }}><Icon d={I.copy} size={12} />Copiar</button>
        </dd>
        <dt>{FYGARO.impuestos}</dt><dd className="money">{fmtMoney(d.tax_total, cur)}</dd>
        <dt>{FYGARO.total}</dt><dd className="money"><b>{fmtMoney(d.total, cur)}</b>{cur !== "CRC" && <span className="muted"> · {fmtMoney(d.total_crc)}</span>}</dd>
        {d.doc_type !== "TE" && <>
          <dt>{FYGARO.condicion}</dt><dd style={dim}>{gCond ? <>{ivaLabel(gCond)} <span className="muted mono" style={{ fontSize: 11 }}>({ivaCode(gCond)})</span></> : "—"}{tentativo}</dd>
          <dt>{FYGARO.actividad}</dt><dd className="mono" style={dim}>{gAct || "—"}</dd>
          <dt>{FYGARO.acreditar}</dt><dd className="money" style={dim}>{gCond === "proporcional" ? "prorrata del periodo" : gCredit === null ? "—" : fmtMoney(gCredit, cur)}{tentativo}</dd>
          <dt>{FYGARO.aplicable}</dt><dd className="money" style={dim}>{gAplic === null ? "—" : fmtMoney(gAplic, cur)}{tentativo}</dd>
        </>}
      </dl>
    </Card>
  );

  const xmlCard = (
    <Card title={FYGARO.xml}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        <button className="btn btn--soft btn--sm" onClick={() => bajar("document", `${d.clave}.xml`)}><Icon d={I.download} />{FYGARO.bajarXml}</button>
        <button className="btn btn--soft btn--sm" disabled={!d.has_hacienda_answer} title={d.has_hacienda_answer ? "La respuesta de Hacienda a su confirmación" : ensayo ? "Ensayo en Pruebas: Hacienda no ha respondido porque no se envió" : "Hacienda todavía no ha respondido su confirmación"} onClick={() => bajar("hacienda", `${d.clave}-respuesta-hacienda${d.hacienda_mode === "pruebas" ? "-PRUEBAS" : ""}.xml`)}><Icon d={I.download} />{FYGARO.bajarRespuesta}</button>
        <button className="btn btn--soft btn--sm" onClick={() => bajar("todo", `${d.clave}.zip`)}><Icon d={I.download} />{FYGARO.bajarTodos}</button>
        <button className="btn btn--ghost btn--sm" disabled={!d.has_pdf} onClick={() => openFile(`/reception/${d.id}/file/pdf`).catch((e) => toast(e.message, "bad"))}>{d.has_pdf ? "Ver PDF" : "Sin PDF"}</button>
      </div>
      <details style={{ marginTop: 10 }}>
        <summary className="meta" style={{ cursor: "pointer" }}>Otros archivos</summary>
        <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button className="btn btn--ghost btn--sm" disabled={!d.has_supplier_response} onClick={() => bajar("supplier_response", `${d.clave}-respuesta-proveedor.xml`)}>Acuse de Hacienda al proveedor</button>
            <span className="muted" style={{ fontSize: 12.5 }}>{d.has_supplier_response ? (d.supplier_hacienda_status === "aceptado" ? "Hacienda aceptó la factura al proveedor" : d.supplier_hacienda_status === "rechazado" ? "Hacienda rechazó la factura al proveedor" : "") : "El proveedor no lo adjuntó"}</span>
          </div>
          {d.supplier_hacienda_detail && <HaciendaMensaje msg={d.supplier_hacienda_detail} status={d.supplier_hacienda_status === "aceptado" ? "aceptada" : d.supplier_hacienda_status} />}
          <div><button className="btn btn--ghost btn--sm" disabled={!d.has_response} onClick={() => bajar("mr", ensayo ? `${d.clave}-mensaje-receptor-ENSAYO-sin-validez.xml` : `${d.clave}-mensaje-receptor.xml`)}>{ensayo ? "Mensaje receptor (ensayo, sin validez)" : "Mensaje receptor enviado"}</button></div>
          {allows("accounting.crear") && (
            <div>
              <input ref={attachRef} type="file" accept=".pdf,.xml,application/pdf,application/xml,text/xml" hidden onChange={(e) => attach(e.target.files?.[0])} />
              <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => attachRef.current?.click()}>Adjuntar PDF o acuse de Hacienda</button>
            </div>
          )}
        </div>
      </details>
    </Card>
  );

  const masDatos = (
    <Card>
      <details>
        <summary className="meta" style={{ cursor: "pointer" }}>Más datos del comprobante</summary>
        <dl className="acc-kv" style={{ marginTop: 10 }}>
          <dt>Tipo</dt><dd>{DOC_TYPES[d.doc_type] || d.doc_type}{d.schema_version ? ` · v${d.schema_version}` : ""}</dd>
          <dt>Consecutivo del proveedor</dt><dd className="mono">{d.consecutive || "—"}</dd>
          <dt>Fecha</dt><dd>{fmtDate(d.issue_date)}</dd>
          <dt>Moneda</dt><dd>{cur}{cur !== "CRC" ? ` · TC ${n(d.fx_rate)}` : ""}</dd>
          <dt>Venta neta</dt><dd className="money">{fmtMoney(d.subtotal, cur)}</dd>
          {n(d.iva_total) !== n(d.tax_total) && <><dt>de los impuestos, IVA</dt><dd className="money">{fmtMoney(d.iva_total, cur)}</dd></>}
          {n(d.other_charges) > 0 && <><dt>Otros cargos</dt><dd className="money">{fmtMoney(d.other_charges, cur)}</dd></>}
          {d.reference_clave && <><dt>Referencia</dt><dd className="mono" style={{ fontSize: 11.5, wordBreak: "break-all" }}>{d.reference_clave}</dd></>}
          <dt>Receptor</dt><dd className="mono">{d.receiver_id || "sin identificar"}{d.receiver_ok === false ? " (no es la empresa)" : ""}</dd>
          <dt>Correo del emisor</dt><dd>{d.issuer_email || "—"}</dd>
          <dt>Actividad del proveedor</dt><dd className="mono">{d.issuer_activity_code || "—"}</dd>
          {d.message && <><dt>Llegó por correo</dt><dd>{d.message.sender} · {d.message.subject}</dd></>}
        </dl>
        {d.lines.length > 0 && (
          <div style={{ overflowX: "auto", marginTop: 10 }}><table className="table"><thead><tr><th>#</th><th>Detalle</th><th>CABYS</th><th className="num">Cant.</th><th className="num">Precio</th><th className="num">IVA</th><th className="num">Total</th></tr></thead>
            <tbody>{d.lines.map((l, i) => <tr key={i}><td className="muted">{l.number}</td><td>{l.detail}</td><td className="mono muted" style={{ fontSize: 11 }}>{l.cabys}</td><td className="num">{n(l.quantity)} {l.unit}</td><td className="num money">{fmtMoney(l.price, cur)}</td><td className="num money">{fmtMoney(l.tax, cur)}{l.tax_rate ? ` (${n(l.tax_rate)}%)` : ""}</td><td className="num money">{fmtMoney(l.total, cur)}</td></tr>)}</tbody></table></div>
        )}
      </details>
    </Card>
  );

  const responder = (
    <>
      {showForm && d.responses.length > 0 && <Card title="Intentos anteriores">{timeline}</Card>}
      {showForm && d.proposal && (
        <Card title="Respuesta sugerida" extra={<span className={`badge badge--${d.proposal.ready ? "ok" : "warn"}`}>{d.proposal.ready ? "Lista para aprobar" : "Revísela a mano"}</span>}>
          <dl className="acc-kv">
            <dt>{FYGARO.accion}</dt><dd><b>{ACTION_LABEL[d.proposal.action]}</b></dd>
            <dt>{FYGARO.condicion}</dt><dd>{ivaLabel(d.proposal.iva_condition)}</dd>
            {d.proposal.activity_code && <><dt>{FYGARO.actividad}</dt><dd className="mono">{d.proposal.activity_code}</dd></>}
            <dt>Gasto</dt><dd>{d.proposal.link_expense_id ? "Se vincula al gasto ya registrado" : d.proposal.create_expense ? `Se registra${d.proposal.category ? ` en «${d.proposal.category}»` : " (sin categoría)"}` : "Sin gasto"}</dd>
          </dl>
          <ul className="muted" style={{ fontSize: 12.5, margin: "10px 0 0", paddingLeft: 18 }}>{d.proposal.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          {d.proposal.blockers.length > 0 && <div className="acc-warn" style={{ marginTop: 10 }}>{d.proposal.blockers.map((b) => <div key={b}>⚠ {b}</div>)}</div>}
          {d.proposal.ready && <button className="btn btn--crimson" style={{ marginTop: 12 }} disabled={busy} onClick={approve}>{busy ? "Guardando…" : approveLabel(d.mode)}</button>}
          <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>¿Algo no calza? Cámbielo abajo y use «{saveLabel}».</p>
        </Card>
      )}
      {showForm ? (
        <Card title="Responder">
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {d.mode === "pruebas" && <div className="acc-warn">Crimson está en Pruebas: su decisión queda guardada y no se envía a Hacienda. Después la envía con «Enviar en Producción».</div>}
            <div className="acc-seg" role="radiogroup" aria-label={FYGARO.accion}>{ACTIONS.map((a) => <button key={a.key} type="button" className={f.action === a.key ? "is-active" : ""} aria-pressed={f.action === a.key} onClick={() => setF({ ...f, action: a.key })}>{a.label}</button>)}</div>
            {f.action !== "rechazada" && (
              <Field label={FYGARO.condicion} hint={IVA_CONDITIONS.find((c) => c.key === f.cond)?.hint}>
                <select className="select" value={f.cond} onChange={(e) => setF({ ...f, cond: e.target.value as IvaCondition })}>{IVA_CONDITIONS.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</select>
              </Field>
            )}
            {showActivity && (
              <Field label={`${FYGARO.actividad} (de la empresa)`}>
                <select className="select" value={f.activity} onChange={(e) => setF({ ...f, activity: e.target.value })}>{acts.map((a) => <option key={a.id} value={a.code}>{a.code}{a.description ? ` · ${a.description}` : ""}{a.is_default ? " (por defecto)" : ""}</option>)}</select>
              </Field>
            )}
            {f.action === "parcial" && (
              <div className="grid-2">
                <Field label="Monto aceptado"><input className="input input--mono" inputMode="decimal" value={f.accepted_total} onChange={(e) => setF({ ...f, accepted_total: e.target.value })} /></Field>
                <Field label="IVA aceptado"><input className="input input--mono" inputMode="decimal" value={f.accepted_tax} onChange={(e) => setF({ ...f, accepted_tax: e.target.value })} /></Field>
              </div>
            )}
            {f.action !== "rechazada" && (f.cond === "credito_parcial" || f.cond === "bienes_capital") && (
              <Field label="IVA a acreditar" hint={f.cond === "bienes_capital" ? "Vacío = todo el IVA aceptado." : undefined}><input className="input input--mono" inputMode="decimal" value={f.credit} onChange={(e) => setF({ ...f, credit: e.target.value })} /></Field>
            )}
            <Field label={needsDetail ? "Detalle (obligatorio)" : "Detalle (opcional)"} hint={`${f.detail.trim().length}/160 · mínimo 5 si se escribe`}>
              <textarea className="textarea" maxLength={160} value={f.detail} onChange={(e) => setF({ ...f, detail: e.target.value })} placeholder={f.action === "rechazada" ? "Motivo del rechazo" : f.action === "parcial" ? "Qué no se acepta y por qué" : ""} />
            </Field>
            {f.action !== "rechazada" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {d.expense_candidates.length > 0 && (
                  <>
                    <label className="acc-check"><input type="radio" checked={f.expense === "link"} onChange={() => setF({ ...f, expense: "link" })} /><span><b>Vincular al gasto existente</b> (no se duplica el gasto)</span></label>
                    {f.expense === "link" && d.expense_candidates.map((c) => (
                      <label key={c.id} className="acc-check" style={{ paddingLeft: 24 }}><input type="radio" name="cand" checked={f.link === String(c.id)} onChange={() => setF({ ...f, link: String(c.id) })} /><span>{fmtDate(c.date)} · {c.description} · <b>{fmtMoney(c.total, c.currency)}</b>{c.recurrence_id ? " · recurrente" : ""}</span></label>
                    ))}
                  </>
                )}
                <label className="acc-check"><input type="radio" checked={f.expense === "create"} onChange={() => setF({ ...f, expense: "create" })} /><span>Registrar el gasto{d.expense_candidates.length ? " (hay un gasto que parece ser este)" : ""}</span></label>
                {f.expense === "create" && <Field label="Categoría"><select className="select" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}><option value="">—</option>{cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>}
                <label className="acc-check"><input type="radio" checked={f.expense === "none"} onChange={() => setF({ ...f, expense: "none" })} /><span>Sin gasto</span></label>
              </div>
            )}
            {error && <div className="acc-warn" role="alert" style={{ borderColor: "var(--bad)" }}>{error}</div>}
            <button className="btn btn--crimson" disabled={busy || (needsDetail && f.detail.trim().length < 5)} onClick={respond}>{busy ? "Guardando…" : saveLabel}</button>
          </div>
        </Card>
      ) : (
        <Card title="Respuesta ante Hacienda">
          {d.responses.length === 0 ? <p className="muted" style={{ margin: 0 }}>{d.status === "sin_respuesta" ? "Este comprobante no lleva mensaje receptor." : d.status === "ajeno" ? "No es para la empresa: no se responde." : d.status === "historico" ? "Respondido en Fygaro." : "Sin respuesta."}</p> : timeline}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
            {d.can_send_production && allows("accounting.editar") && <button className="btn btn--crimson btn--sm" disabled={busy} onClick={() => setModal("produccion")}>Enviar en Producción</button>}
            {d.can_refresh && allows("accounting.editar") && <button className="btn btn--soft btn--sm" disabled={busy} onClick={() => act("refresh", {}, "Estado consultado")}>Consultar estado</button>}
            {d.can_send && allows("accounting.editar") && <button className="btn btn--soft btn--sm" disabled={busy} onClick={() => act("send", {}, "Enviado")}>Enviar ahora</button>}
            {d.can_retry && allows("accounting.editar") && <button className={`btn btn--sm ${ensayo ? "btn--soft" : "btn--crimson"}`} disabled={busy} onClick={() => act("retry", {}, "Listo para responder de nuevo")}>{ensayo ? "Cambiar la decisión" : "Responder de nuevo"}</button>}
            {d.can_reopen && allows("accounting.editar") && <button className="btn btn--ghost btn--sm" onClick={() => setModal("reopen")}>Cambiar la respuesta antes de enviarla</button>}
            {d.expense_id && <Link className="btn btn--ghost btn--sm" to={`/contabilidad/contable?expense=${d.expense_id}`}>Ver gasto</Link>}
            {d.can_expense && allows("accounting.crear") && <button className="btn btn--soft btn--sm" disabled={busy} onClick={addExpense}>Registrar gasto</button>}
          </div>
          {canLink && (
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--hair)" }}>
              <span className="meta" style={{ textTransform: "none" }}>Hay un gasto registrado que parece ser este comprobante: vincúlelo en vez de registrar otro.</span>
              {d.expense_candidates.map((c) => (
                <label key={c.id} className="acc-check"><input type="radio" name="cand-link" checked={f.link === String(c.id)} onChange={() => setF({ ...f, link: String(c.id) })} /><span>{fmtDate(c.date)} · {c.description} · <b>{fmtMoney(c.total, c.currency)}</b>{c.recurrence_id ? " · recurrente" : ""}</span></label>
              ))}
              <div><button className="btn btn--crimson btn--sm" disabled={busy || !f.link} onClick={() => act("link-expense", { expense_id: Number(f.link) }, "Gasto vinculado")}>Vincular al gasto existente</button></div>
            </div>
          )}
        </Card>
      )}
    </>
  );

  const inventario = (d.action === "aceptada" || d.action === "parcial") && (allows("inventory.ajustar") || allows("inventory.crear")) ? <StockIn key={`${d.id}-${d.responses.length}`} docId={d.id} /> : null;
  const descartar = allows("accounting.editar") && (["por_revisar", "ajeno", "invalido", "sin_respuesta"].includes(d.status) || d.status === "descartado") ? (
    <Card title={d.status === "descartado" ? "Descartado" : "Descartar"}>
      {d.status === "descartado" ? (
        <><p className="muted" style={{ marginTop: 0 }}>Motivo: {d.discarded_reason}</p><button className="btn btn--soft btn--sm" disabled={busy} onClick={() => act("restore", {}, "Restaurado")}>Restaurar</button></>
      ) : (
        <><p className="muted" style={{ marginTop: 0, fontSize: 13 }}>No se borra (se conserva 5 años): sale de la bandeja con su motivo.</p>{d.can_expense && d.status === "sin_respuesta" && allows("accounting.crear") && <button className="btn btn--soft btn--sm" style={{ marginRight: 8 }} disabled={busy} onClick={addExpense}>Registrar gasto</button>}<button className="btn btn--ghost btn--sm" onClick={() => setDiscard("")}>Descartar…</button></>
      )}
    </Card>
  ) : null;

  return (
    <>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button className="btn btn--ghost btn--sm" onClick={() => nav(-1)}><Icon d={I.arrow} size={14} />Volver</button>
        <h2 className="h3" style={{ margin: 0 }}>{FYGARO.ficha}</h2>
      </div>
      {d.warnings.length > 0 && <div className="acc-warn">{d.warnings.map((w) => <div key={w}>⚠ {w}</div>)}</div>}

      {/* escritorio: dos columnas; celular y tablet: una, en el orden de uso (Estado, Responder, Gasto, Emisor, XML...) */}
      <div className="acc-detail">
        {/* escritorio: el orden de Fygaro (emisor, estado, gasto, XML); --o es el orden en una sola columna */}
        <div className="acc-col">
          <div style={o(4)}>{emisorCard}</div>
          <div style={o(1)}>{estadoCard}</div>
          <div style={o(3)}>{gastoCard}</div>
          <div style={o(5)}>{xmlCard}</div>
          <div style={o(9)}>{masDatos}</div>
        </div>
        <div className="acc-col">
          <div style={{ ...o(2), display: "flex", flexDirection: "column", gap: 14 }}>{responder}</div>
          {inventario && <div style={o(7)}>{inventario}</div>}
          {descartar && <div style={o(8)}>{descartar}</div>}
        </div>
      </div>

      {discard !== null && (
        <Modal title="Descartar comprobante" onClose={() => setDiscard(null)} foot={<><button className="btn btn--ghost" onClick={() => setDiscard(null)}>Cancelar</button><button className="btn btn--danger" disabled={discard.trim().length < 3} onClick={async () => { await act("discard", { reason: discard.trim() }, "Descartado"); setDiscard(null); }}>Descartar</button></>}>
          <Field label="Motivo"><input className="input" autoFocus value={discard} onChange={(e) => setDiscard(e.target.value)} placeholder="Duplicado de otro proveedor, no corresponde…" /></Field>
        </Modal>
      )}
      {modal === "produccion" && (
        <Modal title="Enviar en Producción" onClose={closeModal} foot={<><button className="btn btn--ghost" onClick={closeModal}>Cancelar</button><button className="btn btn--crimson" disabled={!confirmFygaro || busy} onClick={async () => { if (await act("send-production", { confirm_not_in_fygaro: true }, "Enviada en Producción")) closeModal(); }}>{busy ? "Enviando…" : "Enviar"}</button></>}>
          <p className="muted" style={{ fontSize: 13 }}>Se envía a Hacienda la misma decisión que guardó ({d.accion.label}{d.iva_condition ? ` · ${ivaLabel(d.iva_condition)}` : ""}), sin responderla de nuevo. Si ya se respondió en Fygaro, no la envíe: Crimson no puede saberlo y Hacienda podría registrar dos respuestas.</p>
          <label className="acc-check"><input type="checkbox" checked={confirmFygaro} onChange={(e) => setConfirmFygaro(e.target.checked)} /><span>Confirmo que esta factura NO se respondió en Fygaro</span></label>
        </Modal>
      )}
      {modal === "reopen" && (
        <Modal title="Cambiar la respuesta antes de enviarla" onClose={closeModal} foot={<><button className="btn btn--ghost" onClick={closeModal}>Cancelar</button><button className="btn btn--crimson" disabled={!confirmFygaro || busy} onClick={async () => { if (await act("reopen", { confirm_not_in_fygaro: true }, "Lista para responder")) closeModal(); }}>Cambiar la respuesta</button></>}>
          <p className="muted" style={{ fontSize: 13 }}>Lo que se respondió en simulado o en Pruebas no llegó a Hacienda. Reábralo para cambiar la decisión solo si esta factura <b>no</b> se respondió en Fygaro: si se responde dos veces, Hacienda rechaza la segunda.</p>
          <label className="acc-check"><input type="checkbox" checked={confirmFygaro} onChange={(e) => setConfirmFygaro(e.target.checked)} /><span>Confirmo que esta factura NO se respondió en Fygaro</span></label>
        </Modal>
      )}
    </>
  );
}
