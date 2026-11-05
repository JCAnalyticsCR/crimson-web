/* Correos y cargas: que llego y que se hizo con cada adjunto. "Reprocesar" los que fallaron (guardaron su .eml) y
   "Asignar PDF" a los que quedaron sin pareja. Los correos sin comprobantes (boletines) se ocultan por defecto, pero se
   dice cuantos son. 05/11: cada correo dice en claro POR QUE quedo asi (motivo de la API), y el que trae solo un enlace
   con la clave en el texto (estilo HIOPOS) se ve con «Subir el XML». */
import { useEffect, useState } from "react";
import { api, parseTs } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Link } from "react-router-dom";
import { Badge, Card, Empty, Field, Loading, Modal } from "../../../ui/components";
import type { InboxStatus, Reception } from "../hacienda";
import UploadDialog from "./UploadDialog";

type Entry = { name: string; kind: string; result: string | null; clave: string | null; received_document_id: number | null; error: string | null; media_id?: number };
type Msg = { id: number; source: string; sender: string | null; subject: string | null; received_at: string | null; size: number; status: string; summary: Entry[]; error: string | null; motivo: string | null; created_at: string; can_reprocess: boolean };

const CHIP: Record<string, [string, string]> = {
  nuevo: ["Nuevo", "acc-chip--ok"],
  duplicado: ["Duplicado", ""],
  respuesta: ["Respuesta adjuntada", "acc-chip--ok"],
  respuesta_cliente: ["Respuesta de cliente", "acc-chip--warn"],
  pdf: ["PDF adjuntado", "acc-chip--ok"],
  sin_asignar: ["PDF sin asignar", "acc-chip--warn"],
  ignorado: ["Ignorado", ""],
  error: ["Error", "acc-chip--bad"],
  solo_enlace: ["Solo un enlace", "acc-chip--warn"],
  enlace: ["Enlaces, sin factura", ""],
  no_fiscal: ["No es XML ni PDF", ""],
};
const STATUS_TONE: Record<string, string> = { procesado: "confirmado", sin_comprobantes: "anulada", parcial: "parcial", error: "fallido", solo_enlace: "solo_enlace" };
const ago = (s: string | null) => {
  if (!s) return "nunca";
  const m = Math.round((Date.now() - parseTs(s).getTime()) / 60000);
  return m < 1 ? "hace un momento" : m < 60 ? `hace ${m} min` : m < 1440 ? `hace ${Math.round(m / 60)} h` : parseTs(s).toLocaleDateString("es-CR");
};

export default function MailLog() {
  const { toast, allows } = useSession();
  const [rows, setRows] = useState<Msg[] | null>(null);
  const [ocultos, setOcultos] = useState(0);
  const [upload, setUpload] = useState(false);
  const [all, setAll] = useState(false);
  const [inbox, setInbox] = useState<InboxStatus | null>(null);
  const [assign, setAssign] = useState<{ msg: Msg; media_id: number; name: string } | null>(null);
  const [docs, setDocs] = useState<Reception[]>([]);
  const [target, setTarget] = useState("");
  const load = () => api<{ items: Msg[]; sin_comprobantes?: number }>(`/reception/messages?limit=100${all ? "&all=1" : ""}`).then((r) => { setRows(r.items); setOcultos(r.sin_comprobantes ?? 0); }).catch((e) => toast(e.message, "bad"));
  useEffect(() => { load(); }, [all]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api<InboxStatus>("/reception/inbox/status").then(setInbox).catch(() => setInbox(null)); }, []);

  const reprocess = async (m: Msg) => {
    try { await api(`/reception/messages/${m.id}/reprocess`, { method: "POST" }); toast("Correo reprocesado"); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const openAssign = (msg: Msg, e: Entry) => {
    setAssign({ msg, media_id: e.media_id!, name: e.name });
    setTarget("");
    api<{ items: Reception[] }>("/reception/documents?limit=100").then((r) => setDocs(r.items.filter((x) => !x.has_pdf))).catch(() => setDocs([]));
  };
  const doAssign = async () => {
    if (!assign || !target) return;
    try { await api(`/reception/messages/${assign.msg.id}/assign-pdf`, { method: "POST", json: { media_id: assign.media_id, received_document_id: Number(target) } }); toast("PDF asignado"); setAssign(null); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  return (
    <>
      <div className="card"><div className="card__body" style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
        {inbox && inbox.source !== "ninguno" ? (
          <span><b>Buzón {inbox.mailbox}</b> · revisado {ago(inbox.last_run_at)}{inbox.source === "env" ? " · configurado en el servidor" : ""}</span>
        ) : (
          <span className="muted">Apagado: configúrelo en Ajustes → XML de proveedores o en las variables del servidor. Mientras tanto, cargue los archivos a mano.</span>
        )}
        <label style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />Mostrar también los que no traían comprobantes</label>
      </div></div>
      <Card flush>
        {rows === null ? <Loading /> : rows.length === 0 ? <Empty title="Sin correos" hint="Cada correo del buzón y cada carga manual queda aquí con lo que se hizo con sus adjuntos." /> : (
          <table className="table table--stack"><thead><tr><th>Fecha</th><th>Remitente</th><th>Asunto y motivo</th><th>Adjuntos</th><th>Estado</th><th /></tr></thead>
            <tbody>{rows.map((m) => {
              const cuerpo = m.summary.find((e) => e.result === "solo_enlace");
              return (
                <tr key={m.id}>
                  <td data-label="Fecha" className="muted" style={{ whiteSpace: "nowrap" }}>{parseTs(m.received_at || m.created_at).toLocaleString("es-CR", { dateStyle: "short", timeStyle: "short" })}</td>
                  <td data-label="Remitente" style={{ maxWidth: 220, overflowWrap: "anywhere" }}>{m.sender || (m.source === "manual" ? "Carga manual" : "—")}</td>
                  <td data-label="Asunto y motivo" className="stack-full" style={{ minWidth: 220, overflowWrap: "anywhere" }}>{m.subject || "—"}
                    {m.motivo && <div className="meta" style={{ textTransform: "none", color: m.status === "error" || m.status === "parcial" ? "var(--bad)" : m.status === "solo_enlace" ? "var(--warn)" : undefined }}>{m.motivo}</div>}
                    {cuerpo?.clave && <div className="mono" style={{ fontSize: 11, wordBreak: "break-all", marginTop: 2 }}>{cuerpo.clave}</div>}
                  </td>
                  <td data-label="Adjuntos"><div className="acc-chips">{m.summary.map((e, i) => {
                    const [label, tone] = CHIP[e.result || ""] ?? [e.result || "—", ""];
                    return <span key={i} className={`acc-chip ${tone}`} title={`${e.name}${e.error ? ` · ${e.error}` : ""}`}>{label}{e.result === "sin_asignar" && allows("accounting.crear") && <button className="btn btn--ghost btn--sm" style={{ height: 20, padding: "0 6px" }} onClick={() => openAssign(m, e)}>Asignar PDF</button>}</span>;
                  })}{m.summary.length === 0 && <span className="muted">—</span>}</div></td>
                  <td data-label="Estado"><Badge status={STATUS_TONE[m.status] || m.status} /></td>
                  <td className="num stack-actions">
                    {m.can_reprocess && allows("accounting.crear") && <button className="btn btn--soft btn--sm" onClick={() => reprocess(m)}>Reprocesar</button>}
                    {cuerpo && !cuerpo.received_document_id && allows("accounting.crear") && <button className="btn btn--crimson btn--sm" onClick={() => setUpload(true)}>Subir el XML</button>}
                    {cuerpo?.received_document_id && <Link className="btn btn--ghost btn--sm" to={`/contabilidad/facturas/${cuerpo.received_document_id}`}>Ver la factura</Link>}
                  </td>
                </tr>
              );
            })}</tbody></table>
        )}
      </Card>
      {!all && ocultos > 0 && (
        <button className="btn btn--ghost btn--sm" style={{ alignSelf: "flex-start" }} onClick={() => setAll(true)}>Ver los {ocultos} correos sin comprobantes (publicidad, avisos…) y por qué</button>
      )}
      {upload && <UploadDialog onClose={() => setUpload(false)} onDone={load} />}
      {assign && (
        <Modal title="Asignar PDF" onClose={() => setAssign(null)} foot={<><button className="btn btn--ghost" onClick={() => setAssign(null)}>Cancelar</button><button className="btn btn--crimson" disabled={!target} onClick={doAssign}>Asignar</button></>}>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>{assign.name}</p>
          <Field label="Comprobante">
            <select className="select" value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">Elija el comprobante…</option>
              {docs.map((x) => <option key={x.id} value={x.id}>{x.issuer_name} · {x.consecutive || x.clave.slice(-10)} · {x.total}</option>)}
            </select>
          </Field>
        </Modal>
      )}
    </>
  );
}
