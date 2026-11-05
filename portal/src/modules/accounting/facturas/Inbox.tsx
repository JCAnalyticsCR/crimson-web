/* Facturas de proveedores con los dos menus de Fygaro: «Bandeja de entrada» (lo que llego y falta responder, con el
   plazo y la respuesta sugerida para aprobarla con un clic) y «Recepción de facturas» (todo: emisor, clave del documento,
   totales, «Acción realizada» y «Notificación a Hacienda»). Las palabras y colores de los estados los decide la API
   (services/reception_state): aqui solo se pintan. La clave va completa y tal cual, sin marcas (05/11).
   En modo Pruebas nada sale a Hacienda: la decision queda como ensayo y se envia despues con «Enviar en Producción». */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { STATUS, api, fmtDate, fmtMoney, parseTs } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Card, Empty, I, Icon, Loading, Modal } from "../../../ui/components";
import { ACTION_LABEL, DOC_TYPES, FYGARO, ivaLabel, plazo, type Estado, type InboxStatus, type Proposal, type Reception } from "../hacienda";
import type { ReceptionSummary } from "../useReceptionCount";
import UploadDialog from "./UploadDialog";

type Page = { items: Reception[]; total: number; counts: Record<string, number> };
const PAGE = 50;
const LIST_STATUSES = ["por_revisar", "respondido", "sin_respuesta", "ajeno", "invalido", "descartado", "historico"];
const ddmm = (s: string | null | undefined) => (s ? new Date(s + "T12:00:00").toLocaleDateString("es-CR", { day: "2-digit", month: "2-digit" }) : "—");

/** Un estado de reception_state: la etiqueta con su tono y, debajo, la pista corta. */
export function EstadoCell({ e, hint = true }: { e: Estado | undefined; hint?: boolean }) {
  if (!e) return <span className="muted">—</span>;
  return (
    <span style={{ display: "inline-grid", gap: 3 }}>
      <span className={`badge badge--${e.tone}`} title={e.motivo || e.hint || undefined}>{e.label}</span>
      {hint && (e.motivo || e.hint) && <span className="meta" style={{ textTransform: "none", whiteSpace: "normal", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }} title={e.motivo || e.hint || undefined}>{e.motivo || e.hint}</span>}
      {e.alerta && <span className="meta" style={{ textTransform: "none", color: "var(--bad)", whiteSpace: "normal" }}>{e.alerta}</span>}
    </span>
  );
}

/** La clave del documento: los 50 digitos tal cual, sin marcas, y debajo fecha y tipo; avisa si falta un archivo. */
function ClaveCell({ r }: { r: Reception }) {
  const faltan: string[] = [];
  if (!r.has_pdf) faltan.push("Falta el PDF");
  if (["por_revisar", "respondido"].includes(r.status) && !r.has_supplier_response) faltan.push("Sin acuse de Hacienda");
  return (
    <div style={{ minWidth: 0 }}>
      <div className="mono" style={{ fontSize: 11.5, wordBreak: "break-all", lineHeight: 1.35 }} title="Clave del comprobante del proveedor, tal cual viene en su XML">{r.clave}</div>
      <div className="meta" style={{ textTransform: "none" }}>{fmtDate(r.issue_date)} · {DOC_TYPES[r.doc_type] || r.doc_type}</div>
      {faltan.length > 0 && <div className="meta" style={{ textTransform: "none", color: "var(--warn)" }}>{faltan.join(" · ")}</div>}
    </div>
  );
}

function Emisor({ r }: { r: Reception }) {
  return <><b>{r.issuer_name || "—"}</b><div className="meta mono" style={{ textTransform: "none" }}>{r.issuer_id || "—"}</div></>;
}

function Sugerida({ p }: { p: Proposal | null | undefined }) {
  if (!p) return <span className="muted">—</span>;
  const txt = `${ACTION_LABEL[p.action] || p.action} · ${ivaLabel(p.iva_condition)}${p.link_expense_id ? " · vincula su gasto" : p.create_expense ? ` · gasto${p.category ? ` en ${p.category}` : ""}` : " · sin gasto"}`;
  return (
    <span style={{ fontSize: 12.5 }} title={[...p.reasons, ...p.blockers].join(" · ")}>
      {txt}
      {!p.ready && <div className="meta" style={{ textTransform: "none", color: "var(--warn)" }}>{p.blockers[0] || "Revísela a mano"}</div>}
    </span>
  );
}

function Deadline({ r }: { r: Reception }) {
  const d = r.business_days_left;
  const color = d !== null && d <= 2 ? "var(--bad)" : undefined;
  return <span style={{ color, fontWeight: color ? 700 : undefined }}>{fmtDate(r.deadline)}<div className="meta" style={{ textTransform: "none", color }}>{plazo(d)}</div></span>;
}

/** Texto del boton de aprobar segun el modo: en Pruebas no se envia nada. */
export const approveLabel = (mode: string | undefined) => (mode === "produccion" ? "Aprobar y enviar" : mode === "pruebas" ? "Aprobar (ensayo, no se envía)" : "Aprobar (simulado)");
export const approvedToast = (status: string) =>
  status === "ensayo" ? "Decisión guardada en Pruebas: no se envió a Hacienda" : status === "simulado" ? "Respuesta aprobada (simulado: no se envió a Hacienda)" : "Respuesta aprobada y enviada a Hacienda";

export default function Inbox({ all = false }: { all?: boolean }) {
  const { toast, allows } = useSession();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [page, setPage] = useState<Page | null>(null);
  const [sum, setSum] = useState<ReceptionSummary | null>(null);
  const [inbox, setInbox] = useState<InboxStatus | null>(null);
  const [upload, setUpload] = useState(false);
  const [busy, setBusy] = useState(false);
  const [prodModal, setProdModal] = useState<number[] | null>(null);
  const [confirmFygaro, setConfirmFygaro] = useState(false);
  const [f, setF] = useState({ status: params.get("pend") === "1" ? "__pend" : "", doc_type: "", month: "", q: "", offset: 0 });

  const load = useCallback(() => {
    const qs = new URLSearchParams({ limit: String(PAGE), offset: String(all ? f.offset : 0) });
    if (!all) qs.set("status", "por_revisar");
    else {
      if (f.status === "__pend") qs.set("pend_prod", "1");
      else if (f.status) qs.set("status", f.status);
      if (f.doc_type) qs.set("doc_type", f.doc_type);
      if (f.q.trim()) qs.set("q", f.q.trim());
      if (f.month) {
        const [y, m] = f.month.split("-").map(Number);
        qs.set("from", `${f.month}-01`);
        qs.set("to", new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10));
      }
    }
    api<Page>(`/reception/documents?${qs}`).then(setPage).catch((e) => toast(e.message, "bad"));
    api<ReceptionSummary>("/reception/summary").then(setSum).catch(() => setSum(null));
  }, [all, f, toast]);
  useEffect(load, [load]);
  useEffect(() => { api<InboxStatus>("/reception/inbox/status").then(setInbox).catch(() => setInbox(null)); }, []);

  const runInbox = async () => {
    setBusy(true);
    try {
      const r = await api<{ messages: number; nuevos: number; errores: string[] }>("/reception/inbox/run", { method: "POST" });
      toast(`${r.messages} correos revisados · ${r.nuevos} comprobantes nuevos${r.errores.length ? ` · ${r.errores.length} con error` : ""}`);
      load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  // la huella de la sugerencia que se ve en la bandeja (si cambio, el servidor no la envia)
  const huella = (id: number) => page?.items.find((r) => r.id === id)?.proposal?.fingerprint ?? null;
  const approve = async (ids: number[]) => {
    if (!ids.length) return;
    setBusy(true);
    try {
      if (ids.length === 1) {
        const r = await api<Reception>(`/reception/${ids[0]}/approve`, { method: "POST", json: { fingerprint: huella(ids[0]) } });
        toast(approvedToast(r.hacienda_status));
      } else {
        const r = await api<{ aprobadas: { id: number; hacienda_status: string }[]; omitidas: { id: number; motivo: string }[] }>("/reception/approve-batch", { method: "POST", json: { ids, fingerprints: Object.fromEntries(ids.map((id) => [id, huella(id)]).filter(([, h]) => h)) } });
        const ensayo = r.aprobadas.some((a) => a.hacienda_status === "ensayo");
        toast(`${r.aprobadas.length} respuesta(s) aprobada(s)${ensayo ? " · guardadas en Pruebas, no se enviaron" : ""}${r.omitidas.length ? ` · ${r.omitidas.length} quedaron para revisar a mano: ${r.omitidas[0].motivo}` : ""}`, r.omitidas.length ? "bad" : undefined);
      }
      load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  const sendProduction = async (ids: number[]) => {
    setBusy(true);
    try {
      /* de 10 en 10 (tope del API: cada una pide token y envia en linea) */
      const r: { enviadas: { id: number }[]; omitidas: { id: number; motivo: string }[] } = { enviadas: [], omitidas: [] };
      for (let i = 0; i < ids.length; i += 10) {
        const p = await api<typeof r>("/reception/send-production", { method: "POST", json: { ids: ids.slice(i, i + 10), confirm_not_in_fygaro: true } });
        r.enviadas.push(...p.enviadas); r.omitidas.push(...p.omitidas);
      }
      toast(`${r.enviadas.length === 1 ? "1 enviada" : `${r.enviadas.length} enviadas`} en Producción${r.omitidas.length ? ` · ${r.omitidas.length} no: ${r.omitidas[0].motivo}` : ""}`, r.omitidas.length ? "bad" : undefined);
      setProdModal(null); setConfirmFygaro(false);
      load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  const items = page?.items ?? [];
  const listas = !all ? items.filter((r) => r.proposal?.ready).map((r) => r.id) : [];
  const puedeAprobar = allows("accounting.editar");
  const sp = sum?.solo_pruebas;
  const pendView = all && f.status === "__pend";
  const enProduccion = sum?.mode === "produccion";
  const cards: [string, number, string | undefined, (() => void) | undefined][] = sum ? [
    ["Por revisar", sum.por_revisar, undefined, undefined],
    ["Vencen pronto", sum.vencen_pronto, sum.vencen_pronto ? "var(--bad)" : undefined, undefined],
    ["Vencidos", sum.vencidos, sum.vencidos ? "var(--bad)" : undefined, undefined],
    ["Rechazados por Hacienda", sum.mensajes_rechazados, sum.mensajes_rechazados ? "var(--bad)" : undefined, () => nav("/contabilidad/facturas/recibidas")],
    ["Solo en Pruebas", sp?.n ?? 0, sp?.n ? "var(--warn)" : undefined, () => nav("/contabilidad/facturas/recibidas?pend=1")],
  ] : [];
  return (
    <>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "flex-end" }}>
        {allows("accounting.crear") && <Link className="btn btn--ghost btn--sm" to="/importar?kind=recepciones"><Icon d={I.upload} />Importar histórico</Link>}
        {allows("accounting.crear") && (
          <button className="btn btn--soft btn--sm" onClick={runInbox} disabled={busy || !inbox || inbox.source === "ninguno"} title={inbox?.source === "ninguno" ? "Apagado: configúrelo en Ajustes → XML de proveedores o en las variables del servidor" : undefined}>
            <Icon d={I.refresh} />{busy ? "Revisando…" : inbox?.source === "ninguno" ? "Buzón apagado" : "Revisar buzón ahora"}
          </button>
        )}
        {allows("accounting.crear") && <button className="btn btn--crimson btn--sm" onClick={() => setUpload(true)}><Icon d={I.plus} />Cargar archivos</button>}
      </div>

      {sum?.mode === "pruebas" && (
        <div className="acc-warn" role="status">
          <b>Crimson está en Pruebas: sus respuestas no llegan a Hacienda.</b>{" "}
          {sp && sp.n > 0
            ? <>{sp.n} factura{sp.n === 1 ? "" : "s"} respondida{sp.n === 1 ? "" : "s"} solo en Pruebas{sp.primera_fecha_limite ? `; la primera vence el ${ddmm(sp.primera_fecha_limite)}` : ""}. Envíelas con «Enviar en Producción» cuando la active o, si vencen antes, respóndalas en Fygaro.</>
            : <>La decisión que tome queda guardada y se envía después con «Enviar en Producción».</>}
          {sp && sp.vencidos > 0 && <div style={{ marginTop: 4, color: "var(--bad)" }}>{sp.vencidos} ya venció sin respuesta en Producción: confirme que consta en Fygaro e importe ese mensaje («Importar histórico»).</div>}
        </div>
      )}
      {!all && sum?.buzon && (
        <div className="acc-warn" role="status" style={sum.buzon.state === "ok" ? { borderColor: "var(--hair)", background: "transparent", color: "var(--text-2)" } : undefined}>
          <b>Buzón:</b> {sum.buzon.label}{sum.buzon.last_run_at ? ` · última revisión ${parseTs(sum.buzon.last_run_at).toLocaleString("es-CR")}` : ""}
        </div>
      )}
      {!all && puedeAprobar && listas.length > 0 && (
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span className="muted" style={{ fontSize: 13, flex: 1, minWidth: 220 }}>{listas.length} comprobante(s) con la respuesta lista: revise la sugerencia y apruébela{sum?.mode === "pruebas" ? " (en Pruebas queda guardada, no se envía)" : enProduccion ? "; sale a Hacienda por el flujo de siempre" : ""}.</span>
          <button className="btn btn--crimson btn--sm" disabled={busy} onClick={() => approve(listas)}>{sum?.mode === "pruebas" ? `Aprobar las ${listas.length} (ensayo)` : `Aprobar las ${listas.length} sugeridas`}</button>
        </div>
      )}

      {!all && sum && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(140px,1fr))", gap: 10 }}>
          {cards.map(([l, n, c, go]) => (
            <div key={l} className="card" role={go ? "button" : undefined} tabIndex={go ? 0 : undefined} style={go && n ? { cursor: "pointer" } : undefined} onClick={go && n ? go : undefined} onKeyDown={go && n ? (e) => { if (e.key === "Enter") go(); } : undefined}>
              <div className="card__body"><div className="meta">{l}</div><div style={{ fontWeight: 700, fontSize: 22, color: c }}>{n}</div></div>
            </div>
          ))}
        </div>
      )}

      {all && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          <select className="select" style={{ maxWidth: 230 }} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value, offset: 0 })} aria-label="Estado">
            <option value="">Todos los estados</option>
            {LIST_STATUSES.map((s) => <option key={s} value={s}>{STATUS[s]?.label || s} {page?.counts[s] !== undefined ? `(${page.counts[s]})` : ""}</option>)}
            <option value="__pend">Solo en Pruebas (falta en Producción){page?.counts.solo_pruebas !== undefined ? ` (${page.counts.solo_pruebas})` : ""}</option>
          </select>
          <select className="select" style={{ maxWidth: 170 }} value={f.doc_type} onChange={(e) => setF({ ...f, doc_type: e.target.value, offset: 0 })} aria-label="Tipo"><option value="">Todos los tipos</option>{Object.entries(DOC_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          <input className="input" style={{ maxWidth: 160 }} type="month" value={f.month} onChange={(e) => setF({ ...f, month: e.target.value, offset: 0 })} aria-label="Mes" />
          <input className="input" style={{ maxWidth: 280, flex: "1 1 200px" }} placeholder="Proveedor, cédula, consecutivo o clave" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value, offset: 0 })} />
          {pendView && enProduccion && puedeAprobar && items.length > 0 && (
            <button className="btn btn--crimson btn--sm" disabled={busy} onClick={() => setProdModal(items.map((r) => r.id))}>Enviar en Producción ({items.length})</button>
          )}
        </div>
      )}

      <Card flush>
        {page === null ? <Loading /> : items.length === 0 ? <Empty title={all ? "Sin comprobantes" : "Nada por revisar"} hint="Los comprobantes llegan solos por el buzón o se cargan con «Cargar archivos»." /> : (
          <table className="table table--stack">
            {all ? (
              <thead><tr><th>Emisor</th><th style={{ width: "24%" }}>{FYGARO.clave}</th><th className="num">{FYGARO.impuestos}</th><th className="num">{FYGARO.total}</th><th>{FYGARO.accion}</th><th>{FYGARO.notificacion}</th><th /></tr></thead>
            ) : (
              <thead><tr><th>Emisor</th><th style={{ width: "26%" }}>{FYGARO.clave}</th><th className="num">{FYGARO.total}</th><th>Respuesta sugerida</th><th>Plazo</th><th /></tr></thead>
            )}
            <tbody>{items.map((r) => (
              <tr key={r.id}>
                <td data-label="Emisor"><Emisor r={r} /></td>
                <td data-label={FYGARO.clave} className="stack-full"><ClaveCell r={r} /></td>
                {all && <td data-label={FYGARO.impuestos} className="num money">{fmtMoney(r.tax_total, r.currency)}</td>}
                <td data-label={FYGARO.total} className="num money" style={{ fontWeight: 700 }}>{fmtMoney(r.total, r.currency)}{r.currency !== "CRC" && <div className="muted" style={{ fontWeight: 400 }}>{fmtMoney(r.total_crc)}</div>}</td>
                {all ? <>
                  <td data-label={FYGARO.accion} style={{ maxWidth: 200 }}><EstadoCell e={r.accion} /></td>
                  <td data-label={FYGARO.notificacion} style={{ maxWidth: 220 }}><EstadoCell e={r.notificacion} /></td>
                </> : <>
                  <td data-label="Respuesta sugerida"><Sugerida p={r.proposal} /></td>
                  <td data-label="Plazo"><Deadline r={r} /></td>
                </>}
                <td className="num stack-actions" style={{ whiteSpace: "nowrap" }}>
                  {!all && puedeAprobar && r.proposal?.ready && <button className="btn btn--sm btn--crimson" disabled={busy} onClick={() => approve([r.id])}>{approveLabel(sum?.mode)}</button>}
                  <button className={`btn btn--sm ${r.status === "por_revisar" && !r.proposal?.ready ? "btn--crimson" : "btn--ghost"}`} onClick={() => nav(`/contabilidad/facturas/${r.id}`)}>{r.status === "por_revisar" ? "Revisar" : "Ver"}</button>
                </td>
              </tr>
            ))}</tbody>
          </table>
        )}
        {all && page && page.total > PAGE && (
          <div className="list-head">
            <span className="muted" style={{ fontSize: 13 }}>{f.offset + 1}–{Math.min(f.offset + PAGE, page.total)} de {page.total}</span>
            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn btn--ghost btn--sm" disabled={f.offset === 0} onClick={() => setF({ ...f, offset: Math.max(0, f.offset - PAGE) })}>Anterior</button>
              <button className="btn btn--ghost btn--sm" disabled={f.offset + PAGE >= page.total} onClick={() => setF({ ...f, offset: f.offset + PAGE })}>Siguiente</button>
            </div>
          </div>
        )}
      </Card>
      {upload && <UploadDialog onClose={() => setUpload(false)} onDone={load} />}
      {prodModal && (
        <Modal title="Enviar en Producción" onClose={() => { setProdModal(null); setConfirmFygaro(false); }} foot={<><button className="btn btn--ghost" onClick={() => { setProdModal(null); setConfirmFygaro(false); }}>Cancelar</button><button className="btn btn--crimson" disabled={!confirmFygaro || busy} onClick={() => sendProduction(prodModal)}>{busy ? "Enviando…" : `Enviar ${prodModal.length}`}</button></>}>
          <p className="muted" style={{ fontSize: 13 }}>Se envía a Hacienda la misma decisión que guardó en Pruebas (acción, condición del IVA y montos), sin responderlas de nuevo. Si alguna ya se respondió en Fygaro, déjela fuera: Crimson no puede saberlo y Hacienda podría registrar dos respuestas.</p>
          <label className="acc-check"><input type="checkbox" checked={confirmFygaro} onChange={(e) => setConfirmFygaro(e.target.checked)} /><span>Confirmo que estas {prodModal.length} facturas NO se respondieron en Fygaro</span></label>
        </Modal>
      )}
    </>
  );
}
