/* Detalle de un comprobante recibido: datos, archivos y la respuesta ante Hacienda (aceptar / parcial / rechazar con
   actividad y condicion del IVA), con el calculo en vivo del IVA acreditable y el gasto aplicable. Despues de responder:
   linea de tiempo por intento y acciones (consultar, enviar, responder de nuevo, reabrir, gasto, vincular un gasto que
   ya existia, descartar). Se puede adjuntar el PDF o la respuesta de Hacienda de esa clave cuando llegan aparte. */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, downloadFile, fmtDate, fmtMoney, openFile, parseTs, uploadFile } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Badge, Card, Field, I, Icon, Loading, Modal } from "../../../ui/components";
import { ACTIONS, ACTION_LABEL, DOC_TYPES, IVA_CONDITIONS, calcLive, ivaLabel, plazo, type IvaCondition, type ReceptionDetail as Detail } from "../hacienda";

type Activity = { id: number; code: string; description: string | null; is_default: boolean };
type Cat = { id: number; name: string };
const n = (v: string | number | null | undefined) => Number(v ?? 0) || 0;
const when = (s: string | null) => (s ? parseTs(s).toLocaleString("es-CR", { dateStyle: "medium", timeStyle: "short" }) : "");

const FILES: [string, string, string][] = [
  ["document", "XML", "{c}.xml"],
  ["pdf", "PDF", "{c}.pdf"],
  ["supplier_response", "Respuesta de Hacienda al proveedor", "{c}-respuesta-proveedor.xml"],
  ["mr", "Mensaje receptor", "{c}-mensaje-receptor.xml"],
  ["hacienda", "Respuesta de Hacienda a nuestro mensaje", "{c}-respuesta-hacienda.xml"],
];

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
  const [reopen, setReopen] = useState(false);
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
    setF((x) => ({
      ...x,
      activity: x.activity || def,
      expense: d.proposal ? "none" : cand ? "link" : "create",
      link: cand ? String(cand.id) : "",
      action: d.proposal?.action || x.action,
      cond: (d.proposal?.iva_condition as IvaCondition) || x.cond,
      accepted_total: x.accepted_total || String(n(d.total)),
      accepted_tax: x.accepted_tax || String(n(d.iva_total)),
    }));
  }, [d, acts]);

  const live = useMemo(() => d ? calcLive(f.action, f.cond, n(d.iva_total), n(d.total), n(f.accepted_total), n(f.accepted_tax), f.credit === "" ? null : n(f.credit)) : null, [d, f]);
  if (!d) return <Loading />;
  const cur = d.currency;
  const real = d.mode === "pruebas" || d.mode === "produccion";
  const needsDetail = f.action !== "aceptada";
  const showActivity = f.action !== "rechazada" && f.cond !== "proporcional";

  const act = async (path: string, body?: unknown, okMsg?: string) => {
    setBusy(true);
    try { setD(await api<Detail>(`/reception/${d.id}/${path}`, { method: "POST", json: body ?? {} })); if (okMsg) toast(okMsg); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
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
      toast(r.hacienda_status === "simulado" ? "Respuesta guardada (simulado: no se envió a Hacienda)" : `Respuesta guardada · Hacienda: ${r.hacienda_status}`);
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
  // respondido (o historico) aceptado sin gasto: un recurrente o manual que ya existia se vincula en vez de duplicarse (R13)
  const canLink = !d.can_respond && !d.expense_id && (d.action === "aceptada" || d.action === "parcial") && d.expense_candidates.length > 0 && allows("accounting.editar");
  const file = (which: string, name: string) => (which === "pdf" ? openFile(`/reception/${d.id}/file/pdf`) : downloadFile(`/reception/${d.id}/file/${which}`, name.replace("{c}", d.clave))).catch((e) => toast(e.message, "bad"));
  const available: Record<string, boolean> = { document: true, pdf: d.has_pdf, supplier_response: d.has_supplier_response, mr: d.has_response, hacienda: d.has_hacienda_answer };
  // §8.3: ya respondido (aunque el ultimo mensaje quedara rechazado o con error) se ve primero la linea de tiempo con el
  // motivo de Hacienda; el formulario aparece despues de "Responder de nuevo" (retry -> por_revisar), con los intentos
  // anteriores arriba para no responder sin saber por que se rechazo.
  const showForm = d.can_respond && d.status !== "respondido" && allows("accounting.editar");
  const timeline = (
    <ol className="acc-timeline">
      {d.responses.map((r) => (
        <li key={r.id}>
          <b>Intento {r.attempt}</b> · {ACTION_LABEL[{ "1": "aceptada", "2": "parcial", "3": "rechazada" }[r.message_code] || ""]} · {ivaLabel(r.iva_condition)} · <span className="mono">{r.consecutive}</span> · <span className="badge badge--muted">{r.mode}</span>
          <div className="muted">Respondido · {when(r.created_at)}{r.sent_at ? ` → Enviado · ${when(r.sent_at)}` : ""}</div>
          <div>Hacienda: <Badge status={r.status} />{r.hacienda_detail ? ` ${r.hacienda_detail}` : ""}{r.last_error ? <span style={{ color: "var(--bad)" }}> · {r.last_error}</span> : null}</div>
          {r.credit_amount !== null && <div className="muted">IVA acreditable {fmtMoney(r.credit_amount, cur)} · gasto aplicable {fmtMoney(r.applicable_amount, cur)}</div>}
          {r.detail && <div className="muted">“{r.detail}”</div>}
        </li>
      ))}
    </ol>
  );

  return (
    <>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button className="btn btn--ghost btn--sm" onClick={() => nav(-1)}><Icon d={I.arrow} size={14} />Volver</button>
        <Badge status={d.status} />
        {d.action && <span className="badge badge--info">{ACTION_LABEL[d.action]}</span>}
        <Badge status={d.hacienda_status} />
        {d.status === "por_revisar" && <span className="meta" style={{ textTransform: "none", color: (d.business_days_left ?? 9) <= 2 ? "var(--bad)" : undefined }}>Fecha límite estimada {fmtDate(d.deadline)} · {plazo(d.business_days_left)}</span>}
      </div>
      {d.warnings.length > 0 && <div className="acc-warn">{d.warnings.map((w) => <div key={w}>⚠ {w}</div>)}</div>}

      <div className="acc-detail">
        <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
          <Card title="Emisor">
            <dl className="acc-kv">
              <dt>Nombre</dt><dd><b>{d.issuer_name || "—"}</b></dd>
              <dt>Identificación</dt><dd className="mono">{d.issuer_id || "—"}</dd>
              <dt>Correo</dt><dd>{d.issuer_email || "—"}</dd>
              <dt>Actividad del proveedor</dt><dd className="mono">{d.issuer_activity_code || "—"}</dd>
              {d.message && <><dt>Llegó por correo</dt><dd>{d.message.sender} · {d.message.subject}</dd></>}
            </dl>
          </Card>
          <Card title="Documento">
            <dl className="acc-kv">
              <dt>Tipo</dt><dd>{DOC_TYPES[d.doc_type] || d.doc_type}{d.schema_version ? ` · v${d.schema_version}` : ""}</dd>
              <dt>Clave</dt><dd className="mono" style={{ fontSize: 11.5 }}>{d.clave} <button className="btn btn--ghost btn--sm" onClick={() => { navigator.clipboard.writeText(d.clave); toast("Clave copiada"); }}><Icon d={I.copy} size={12} /></button></dd>
              <dt>Consecutivo</dt><dd className="mono">{d.consecutive || "—"}</dd>
              <dt>Fecha</dt><dd>{fmtDate(d.issue_date)}</dd>
              <dt>Moneda</dt><dd>{cur}{cur !== "CRC" ? ` · TC ${n(d.fx_rate)}` : ""}</dd>
              {d.reference_clave && <><dt>Referencia</dt><dd className="mono" style={{ fontSize: 11.5 }}>{d.reference_clave}</dd></>}
              <dt>Receptor</dt><dd className="mono">{d.receiver_id || "sin identificar"}{d.receiver_ok === false ? " (no es la empresa)" : ""}</dd>
            </dl>
          </Card>
          <Card title="Totales">
            <dl className="acc-kv">
              <dt>Venta neta</dt><dd className="money">{fmtMoney(d.subtotal, cur)}</dd>
              <dt>Impuesto</dt><dd className="money">{fmtMoney(d.tax_total, cur)}</dd>
              {n(d.iva_total) !== n(d.tax_total) && <><dt>de él, IVA</dt><dd className="money">{fmtMoney(d.iva_total, cur)}</dd></>}
              {n(d.other_charges) > 0 && <><dt>Otros cargos</dt><dd className="money">{fmtMoney(d.other_charges, cur)}</dd></>}
              <dt>Total</dt><dd className="money"><b>{fmtMoney(d.total, cur)}</b>{cur !== "CRC" && <span className="muted"> · {fmtMoney(d.total_crc)}</span>}</dd>
            </dl>
            {d.lines.length > 0 && (
              <details style={{ marginTop: 10 }}>
                <summary className="meta" style={{ cursor: "pointer" }}>Líneas ({d.lines.length})</summary>
                <div style={{ overflowX: "auto" }}><table className="table"><thead><tr><th>#</th><th>Detalle</th><th>CABYS</th><th className="num">Cant.</th><th className="num">Precio</th><th className="num">IVA</th><th className="num">Total</th></tr></thead>
                  <tbody>{d.lines.map((l, i) => <tr key={i}><td className="muted">{l.number}</td><td>{l.detail}</td><td className="mono muted" style={{ fontSize: 11 }}>{l.cabys}</td><td className="num">{n(l.quantity)} {l.unit}</td><td className="num money">{fmtMoney(l.price, cur)}</td><td className="num money">{fmtMoney(l.tax, cur)}{l.tax_rate ? ` (${n(l.tax_rate)}%)` : ""}</td><td className="num money">{fmtMoney(l.total, cur)}</td></tr>)}</tbody></table></div>
              </details>
            )}
          </Card>
          <Card title="Archivos">
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {FILES.map(([w, label, name]) => <button key={w} className="btn btn--soft btn--sm" disabled={!available[w]} onClick={() => file(w, name)}>{label}</button>)}
              <button className="btn btn--ghost btn--sm" onClick={() => downloadFile(`/reception/${d.id}/file/todo`, `${d.clave}.zip`).catch((e) => toast(e.message, "bad"))}><Icon d={I.upload} />Descargar todo (.zip)</button>
              {allows("accounting.crear") && (
                <>
                  <input ref={attachRef} type="file" accept=".pdf,.xml,application/pdf,application/xml,text/xml" hidden onChange={(e) => attach(e.target.files?.[0])} />
                  <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => attachRef.current?.click()}>Adjuntar PDF o respuesta de Hacienda</button>
                </>
              )}
            </div>
            {d.supplier_hacienda_detail && <p className="muted" style={{ fontSize: 12.5, marginBottom: 0 }}>Respuesta al proveedor (según el archivo del proveedor): {d.supplier_hacienda_detail}</p>}
          </Card>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
          {showForm && d.responses.length > 0 && <Card title="Intentos anteriores">{timeline}</Card>}
          {showForm ? (
            <Card title="Respuesta">
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div className="acc-seg" role="radiogroup" aria-label="Acción">{ACTIONS.map((a) => <button key={a.key} type="button" className={f.action === a.key ? "is-active" : ""} aria-pressed={f.action === a.key} onClick={() => setF({ ...f, action: a.key })}>{a.label}</button>)}</div>
                {f.action !== "rechazada" && (
                  <Field label="Condición del IVA" hint={IVA_CONDITIONS.find((c) => c.key === f.cond)?.hint}>
                    <select className="select" value={f.cond} onChange={(e) => setF({ ...f, cond: e.target.value as IvaCondition })}>{IVA_CONDITIONS.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</select>
                  </Field>
                )}
                {showActivity && (
                  <Field label="Código de actividad (de la empresa)">
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
                {live && f.action !== "rechazada" && (
                  <dl className="acc-kv" style={{ background: "var(--bg-2)", padding: 10, borderRadius: 10 }}>
                    <dt>IVA acreditable</dt><dd className="money"><b>{live.credit === null ? "prorrata del periodo" : fmtMoney(live.credit, cur)}</b></dd>
                    <dt>Gasto aplicable</dt><dd className="money">{live.applicable === null ? "—" : fmtMoney(live.applicable, cur)}</dd>
                  </dl>
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
                <button className="btn btn--crimson" disabled={busy || (needsDetail && f.detail.trim().length < 5)} onClick={respond}>{busy ? "Guardando…" : real ? "Guardar y enviar a Hacienda" : "Guardar (simulado)"}</button>
              </div>
            </Card>
          ) : (
            <Card title="Respuesta ante Hacienda">
              {d.responses.length === 0 ? <p className="muted" style={{ margin: 0 }}>{d.status === "sin_respuesta" ? "Este comprobante no lleva mensaje receptor." : d.status === "ajeno" ? "No es para la empresa: no se responde." : "Sin respuesta."}</p> : timeline}
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
                {d.can_refresh && allows("accounting.editar") && <button className="btn btn--soft btn--sm" disabled={busy} onClick={() => act("refresh", {}, "Estado consultado")}>Consultar estado</button>}
                {d.can_send && allows("accounting.editar") && <button className="btn btn--soft btn--sm" disabled={busy} onClick={() => act("send", {}, "Enviado")}>Enviar ahora</button>}
                {d.can_retry && allows("accounting.editar") && <button className="btn btn--crimson btn--sm" disabled={busy} onClick={() => act("retry", {}, "Listo para responder de nuevo")}>Responder de nuevo</button>}
                {d.can_reopen && allows("accounting.editar") && <button className="btn btn--soft btn--sm" onClick={() => setReopen(true)}>Reabrir para producción</button>}
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
          {allows("accounting.editar") && (["por_revisar", "ajeno", "invalido", "sin_respuesta"].includes(d.status) || d.status === "descartado") && (
            <Card title={d.status === "descartado" ? "Descartado" : "Descartar"}>
              {d.status === "descartado" ? (
                <><p className="muted" style={{ marginTop: 0 }}>Motivo: {d.discarded_reason}</p><button className="btn btn--soft btn--sm" disabled={busy} onClick={() => act("restore", {}, "Restaurado")}>Restaurar</button></>
              ) : (
                <><p className="muted" style={{ marginTop: 0, fontSize: 13 }}>No se borra (se conserva 5 años): sale de la bandeja con su motivo.</p>{d.can_expense && d.status === "sin_respuesta" && allows("accounting.crear") && <button className="btn btn--soft btn--sm" style={{ marginRight: 8 }} disabled={busy} onClick={addExpense}>Registrar gasto</button>}<button className="btn btn--ghost btn--sm" onClick={() => setDiscard("")}>Descartar…</button></>
              )}
            </Card>
          )}
        </div>
      </div>

      {discard !== null && (
        <Modal title="Descartar comprobante" onClose={() => setDiscard(null)} foot={<><button className="btn btn--ghost" onClick={() => setDiscard(null)}>Cancelar</button><button className="btn btn--danger" disabled={discard.trim().length < 3} onClick={async () => { await act("discard", { reason: discard.trim() }, "Descartado"); setDiscard(null); }}>Descartar</button></>}>
          <Field label="Motivo"><input className="input" autoFocus value={discard} onChange={(e) => setDiscard(e.target.value)} placeholder="Duplicado de otro proveedor, no corresponde…" /></Field>
        </Modal>
      )}
      {reopen && (
        <Modal title="Reabrir para producción" onClose={() => setReopen(false)} foot={<><button className="btn btn--ghost" onClick={() => setReopen(false)}>Cancelar</button><button className="btn btn--crimson" disabled={!confirmFygaro} onClick={async () => { await act("reopen", { confirm_not_in_fygaro: true }, "Reabierto"); setReopen(false); }}>Reabrir</button></>}>
          <p className="muted" style={{ fontSize: 13 }}>Lo que se respondió en simulado o en pruebas no tuvo validez. Reabrilo solo si este comprobante <b>no</b> se respondió en Fygaro: si se responde dos veces, Hacienda rechaza el segundo.</p>
          <label className="acc-check"><input type="checkbox" checked={confirmFygaro} onChange={(e) => setConfirmFygaro(e.target.checked)} /><span>Confirmo que este comprobante NO se respondió en Fygaro</span></label>
        </Modal>
      )}
    </>
  );
}
