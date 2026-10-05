/* Reportar una falla desde el portal: entra como ticket de canal "portal", ya ligado a la empresa del
   cliente (eso lo pone el servidor), con el SLA de su contrato o el de la empresa. */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { I, Icon } from "../../ui/components";
import { PhotoStrip } from "../../ui/MediaPicker";
import type { Asset, TicketFull } from "./shared";

const TIPOS = [
  { k: "falla", l: "Falla", h: "Algo dejó de funcionar" },
  { k: "garantia", l: "Garantía", h: "Un equipo en garantía" },
  { k: "mantenimiento", l: "Mantenimiento", h: "Pedir una visita" },
  { k: "otro", l: "Consulta", h: "Otra cosa" },
];
const PRIOS = [
  { k: "baja", l: "Baja", h: "Puede esperar" },
  { k: "media", l: "Media", h: "Afecta, pero hay cómo seguir" },
  { k: "alta", l: "Alta", h: "Afecta la operación" },
  { k: "critica", l: "Crítica", h: "Sin seguridad o sin servicio" },
];

export default function ClientNewTicket() {
  const nav = useNavigate();
  const { toast } = useSession();
  const [f, setF] = useState({ kind: "falla", priority: "media", subject: "", description: "", location: "", asset_id: "" });
  const [fotos, setFotos] = useState<string[]>([]);
  const [equipos, setEquipos] = useState<Asset[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<Asset[]>("/cliente/equipos").then(setEquipos).catch(() => setEquipos([])); }, []);

  const listo = f.subject.trim().length >= 3 && f.description.trim().length >= 10;
  const enviar = async () => {
    setBusy(true);
    try {
      const t = await api<TicketFull>("/cliente/tickets", {
        method: "POST",
        json: { kind: f.kind, priority: f.priority, subject: f.subject, description: f.description, location: f.location || null, asset_id: f.asset_id ? Number(f.asset_id) : null, photos: fotos },
      });
      toast(`Recibimos su reporte: ${t.number}`);
      nav(`/mis-tickets/${t.id}`, { replace: true });
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo enviar", "bad"); }
    finally { setBusy(false); }
  };

  return (
    <>
      <div className="cp-h"><div><div className="meta">Soporte</div><h1 className="h1">Reportar una falla</h1></div></div>
      <div className="cp-card cp-form" style={{ maxWidth: 640 }}>
        <div><span className="meta">¿Qué necesita?</span>
          <div className="cp-choices" style={{ marginTop: 6 }}>
            {TIPOS.map((t) => <button key={t.k} type="button" className={f.kind === t.k ? "is-on" : ""} onClick={() => setF({ ...f, kind: t.k })}>{t.l}<small>{t.h}</small></button>)}
          </div>
        </div>
        <div><span className="meta">¿Qué tan urgente es?</span>
          <div className="cp-choices" style={{ marginTop: 6 }}>
            {PRIOS.map((t) => <button key={t.k} type="button" className={f.priority === t.k ? "is-on" : ""} onClick={() => setF({ ...f, priority: t.k })}>{t.l}<small>{t.h}</small></button>)}
          </div>
        </div>
        <label className="field" style={{ margin: 0 }}><span className="meta">Resumen</span>
          <input className="input" maxLength={150} value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} placeholder="Ej.: la cámara del portón no graba" />
        </label>
        <label className="field" style={{ margin: 0 }}><span className="meta">Cuéntenos qué pasa</span>
          <textarea className="textarea" rows={4} maxLength={3000} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} placeholder="Desde cuándo, qué se ve, si ya probó reiniciar…" />
        </label>
        {equipos.length > 0 && (
          <label className="field" style={{ margin: 0 }}><span className="meta">Equipo (opcional)</span>
            <select className="input" value={f.asset_id} onChange={(e) => setF({ ...f, asset_id: e.target.value })}>
              <option value="">No sé / no aplica</option>
              {equipos.map((a) => <option key={a.id} value={a.id}>{a.name}{a.location ? ` · ${a.location}` : ""}</option>)}
            </select>
          </label>
        )}
        <label className="field" style={{ margin: 0 }}><span className="meta">Ubicación (opcional)</span>
          <input className="input" maxLength={200} value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} placeholder="Ej.: casa 14, oficina del 2.º piso" />
        </label>
        <div><span className="meta">Fotos (opcional)</span><div style={{ marginTop: 6 }}><PhotoStrip value={fotos} onChange={setFotos} uploadPath="/cliente/media" label="Tomar o subir foto" /></div></div>
        <button className="btn btn--crimson" disabled={!listo || busy} onClick={enviar}><Icon d={I.check} />{busy ? "Enviando…" : "Enviar reporte"}</button>
        {!listo && <span className="meta">Escriba un resumen y al menos una frase de lo que pasa.</span>}
      </div>
    </>
  );
}
