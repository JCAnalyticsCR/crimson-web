/* Cargar archivos a mano: varios XML, PDF o zip a la vez (lo mismo que llega por el buzon). Resumen por archivo. */
import { useRef, useState } from "react";
import { api } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { I, Icon, Modal } from "../../../ui/components";
import type { IntakeResult } from "../hacienda";

const RESULT: Record<string, { label: string; tone: string }> = {
  nuevo: { label: "Nuevo", tone: "acc-chip--ok" },
  duplicado: { label: "Ya estaba", tone: "" },
  respuesta: { label: "Respuesta de Hacienda adjuntada", tone: "acc-chip--ok" },
  respuesta_cliente: { label: "Respuesta de un cliente", tone: "acc-chip--warn" },
  pdf: { label: "PDF adjuntado", tone: "acc-chip--ok" },
  sin_asignar: { label: "PDF sin asignar", tone: "acc-chip--warn" },
  ignorado: { label: "Ignorado", tone: "" },
  error: { label: "Error", tone: "acc-chip--bad" },
};

export default function UploadDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { toast } = useSession();
  const input = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<IntakeResult | null>(null);
  const add = (list: FileList | null) => { if (list) setFiles((f) => [...f, ...Array.from(list)].slice(0, 20)); };
  const send = async () => {
    if (!files.length) return;
    setBusy(true);
    try {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      const r = await api<IntakeResult>("/reception/upload-batch", { method: "POST", body: fd });
      setRes(r); setFiles([]); onDone();
      toast(`${r.nuevos.length} comprobante${r.nuevos.length === 1 ? "" : "s"} nuevo${r.nuevos.length === 1 ? "" : "s"}${r.errores.length ? ` · ${r.errores.length} con error` : ""}`, r.errores.length ? "bad" : "ok");
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  return (
    <Modal title="Cargar archivos" onClose={onClose} wide foot={<><button className="btn btn--ghost" onClick={onClose}>Cerrar</button><button className="btn btn--crimson" disabled={!files.length || busy} onClick={send}>{busy ? "Cargando…" : `Cargar ${files.length || ""}`}</button></>}>
      <p className="muted" style={{ fontSize: 13, margin: 0 }}>XML del comprobante, PDF y la respuesta de Hacienda del proveedor (o un .zip con todo). Hasta 20 archivos de 10 MB. Se emparejan por clave o consecutivo.</p>
      <div
        className={`acc-drop ${over ? "is-over" : ""}`}
        role="button"
        tabIndex={0}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") input.current?.click(); }}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); add(e.dataTransfer.files); }}
      >
        <input ref={input} type="file" multiple hidden accept=".xml,.pdf,.zip,application/xml,text/xml,application/pdf,application/zip" onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
        <Icon d={I.upload} /> Arrastrá o hacé clic para elegir .xml, .pdf o .zip
      </div>
      {files.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
          {files.map((f, i) => <li key={i}>{f.name} <span className="muted">· {(f.size / 1024).toFixed(0)} KB</span> <button className="btn btn--ghost btn--sm" onClick={() => setFiles(files.filter((_, k) => k !== i))}>Quitar</button></li>)}
        </ul>
      )}
      {res?.summary && res.summary.length > 0 && (
        <table className="table"><thead><tr><th>Archivo</th><th>Resultado</th><th>Detalle</th></tr></thead>
          <tbody>{res.summary.map((s, i) => {
            const r = RESULT[s.result || ""] ?? { label: s.result || "—", tone: "" };
            return <tr key={i}><td className="mono" style={{ fontSize: 12, wordBreak: "break-all" }}>{s.name}</td><td><span className={`acc-chip ${r.tone}`}>{r.label}</span></td><td className="muted" style={{ fontSize: 12.5 }}>{s.error || (s.clave ? `Clave …${s.clave.slice(-12)}` : "")}</td></tr>;
          })}</tbody></table>
      )}
    </Modal>
  );
}
