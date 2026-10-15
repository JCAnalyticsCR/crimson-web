/* Abrir un ticket en un proyecto en común ("gestionar sus tickets", 15:59). El cliente sale del proyecto (nunca lo
   elige el navegador); el equipo, de los activos en común. Crimson recibe el aviso y aplica su SLA. */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, I, Icon } from "../../ui/components";
import { PhotoStrip } from "../../ui/MediaPicker";
import { errorText, type ProjectRow } from "./shared";

type Asset = { id: number; name: string; serial: string | null; project: string | null };

export default function AllyNewTicket() {
  const nav = useNavigate();
  const { toast } = useSession();
  const [prjs, setPrjs] = useState<ProjectRow[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [f, setF] = useState({ project_id: "", subject: "", kind: "soporte", priority: "media", description: "", asset_id: "" });
  const [fotos, setFotos] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api<ProjectRow[]>("/aliado/proyectos?estado=todos").then(setPrjs).catch((e) => setErr(errorText(e)));
    api<Asset[]>("/aliado/activos").then(setAssets).catch(() => setAssets([]));
  }, []);
  const pr = prjs.find((p) => String(p.id) === f.project_id);
  const crear = async () => {
    setBusy(true);
    try {
      const t = await api<{ id: number }>("/aliado/tickets", { method: "POST", json: { project_id: Number(f.project_id), subject: f.subject, kind: f.kind, priority: f.priority, description: f.description || null, asset_id: f.asset_id ? Number(f.asset_id) : null, photos: fotos } });
      toast("Ticket abierto");
      nav(`/soporte/${t.id}`, { replace: true });
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo abrir", "bad"); }
    finally { setBusy(false); }
  };
  if (err) return <p className="muted">{err}</p>;
  return (
    <>
      <div className="cp-h"><h1 className="h1">Abrir ticket</h1></div>
      <div className="cp-card cp-form" style={{ maxWidth: 640 }}>
        <Field label="Proyecto en común">
          <select className="select" value={f.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value, asset_id: "" })}>
            <option value="">Elegí el proyecto…</option>
            {prjs.map((p) => <option key={p.id} value={p.id}>{p.number} · {p.name}</option>)}
          </select>
        </Field>
        <Field label="Asunto"><input className="input" value={f.subject} maxLength={200} onChange={(e) => setF({ ...f, subject: e.target.value })} /></Field>
        <div className="cp-grid cp-grid--2">
          <Field label="Tipo">
            <select className="select" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
              <option value="soporte">Soporte</option><option value="garantia">Garantía</option><option value="mantenimiento">Mantenimiento</option><option value="visita">Visita</option><option value="consulta">Consulta</option>
            </select>
          </Field>
          <Field label="Prioridad">
            <select className="select" value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}>
              <option value="baja">Baja</option><option value="media">Media</option><option value="alta">Alta</option><option value="critica">Crítica</option>
            </select>
          </Field>
        </div>
        {pr && assets.some((a) => a.project === pr.number) && (
          <Field label="Equipo (opcional)">
            <select className="select" value={f.asset_id} onChange={(e) => setF({ ...f, asset_id: e.target.value })}>
              <option value="">—</option>
              {assets.filter((a) => a.project === pr.number).map((a) => <option key={a.id} value={a.id}>{a.name}{a.serial ? ` · ${a.serial}` : ""}</option>)}
            </select>
          </Field>
        )}
        <Field label="Descripción"><textarea className="textarea" rows={4} maxLength={4000} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <PhotoStrip value={fotos} uploadPath="/aliado/media" onChange={setFotos} />
        <button className="btn btn--crimson" style={{ alignSelf: "flex-start" }} disabled={busy || !f.project_id || f.subject.trim().length < 3} onClick={crear}><Icon d={I.check} />Abrir ticket</button>
      </div>
    </>
  );
}
