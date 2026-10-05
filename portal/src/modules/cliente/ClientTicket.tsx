/* Detalle del ticket: la conversacion PUBLICA con el equipo de Crimson. Las notas internas ni siquiera
   llegan al navegador (el servidor las filtra). */
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { I, Icon } from "../../ui/components";
import { Lightbox } from "../../ui/Lightbox";
import { PhotoStrip } from "../../ui/MediaPicker";
import { EstadoBadge } from "./ClientTickets";
import { PRIORIDAD, fmtWhen, type TicketFull } from "./shared";

function Fotos({ urls, caption }: { urls: string[]; caption: string }) {
  const [ver, setVer] = useState<number | null>(null);
  if (!urls.length) return null;
  return (
    <div className="photos" style={{ marginTop: 8 }}>
      {urls.map((u, i) => (
        <div className="photos__it" key={u + i} style={{ background: `center/cover no-repeat url("${u}")` }}>
          <button type="button" className="photos__open" onClick={() => setVer(i)} aria-label={`Ver foto ${i + 1} en grande`} />
        </div>
      ))}
      {ver !== null && <Lightbox fotos={urls.map((url) => ({ url, caption }))} start={ver} onClose={() => setVer(null)} />}
    </div>
  );
}

export default function ClientTicket() {
  const { id } = useParams();
  const { toast } = useSession();
  const [t, setT] = useState<TicketFull | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [body, setBody] = useState("");
  const [fotos, setFotos] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api<TicketFull>(`/cliente/tickets/${id}`).then(setT).catch((e) => setErr(e.message)), [id]);
  useEffect(() => { load(); }, [load]);

  const enviar = async () => {
    if (!body.trim()) return;
    setBusy(true);
    try {
      setT(await api<TicketFull>(`/cliente/tickets/${id}/comments`, { method: "POST", json: { body, photos: fotos } }));
      setBody(""); setFotos([]); toast("Mensaje enviado al equipo de Crimson");
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo enviar", "bad"); }
    finally { setBusy(false); }
  };

  if (err) return <><Link to="/mis-tickets" className="btn btn--ghost btn--sm">← Mis tickets</Link><p className="muted" style={{ marginTop: 12 }}>{err}</p></>;
  if (!t) return <span className="spinner" />;
  return (
    <>
      <Link to="/mis-tickets" className="btn btn--ghost btn--sm" style={{ marginBottom: 10 }}>← Mis tickets</Link>
      <div className="cp-h">
        <div style={{ minWidth: 0 }}><div className="meta">{t.number} · prioridad {PRIORIDAD[t.priority]?.toLowerCase()}</div><h1 className="h1" style={{ overflowWrap: "anywhere" }}>{t.subject}</h1></div>
        <EstadoBadge status={t.status} />
      </div>
      <div className="cp-card" style={{ marginBottom: 14 }}>
        <div className="meta">Reportado por {t.opened_by} · {fmtWhen(t.created_at)}{t.asset ? ` · equipo: ${t.asset}` : ""}</div>
        <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", margin: "8px 0 0" }}>{t.description}</p>
        <Fotos urls={t.photos} caption={t.subject} />
        {t.respuesta_antes_de && t.abierto && <p className="meta" style={{ marginTop: 10 }}>Le respondemos antes de: {fmtWhen(t.respuesta_antes_de)}</p>}
        {t.solution && <p style={{ marginTop: 10 }}><b>Solución:</b> {t.solution}</p>}
      </div>

      <h2 className="h3" style={{ margin: "8px 0" }}>Conversación</h2>
      <div className="cp-msgs" style={{ marginBottom: 14 }}>
        {t.messages.length === 0 && <p className="muted">Todavía no hay respuestas. Aquí va a ver lo que le escriba el equipo.</p>}
        {t.messages.map((m) => (
          <div key={m.id} className={`cp-msg ${m.team ? "" : "cp-msg--me"}`}>
            <small>{m.author} · {fmtWhen(m.created_at)}</small>
            {m.body}
            <Fotos urls={m.photos} caption={`${t.number} · ${m.author}`} />
          </div>
        ))}
      </div>

      {t.can_comment ? (
        <div className="cp-card cp-form">
          <label className="field" style={{ margin: 0 }}>
            <span className="meta">{t.status === "esperando_cliente" ? "El equipo espera su respuesta" : "Escribir al equipo de Crimson"}</span>
            <textarea className="textarea" rows={3} maxLength={3000} value={body} onChange={(e) => setBody(e.target.value)} placeholder="Escriba aquí su mensaje" />
          </label>
          <PhotoStrip value={fotos} onChange={setFotos} uploadPath="/cliente/media" label="Agregar foto" />
          <button className="btn btn--crimson" onClick={enviar} disabled={busy || !body.trim()} style={{ alignSelf: "flex-start" }}><Icon d={I.arrow} />{busy ? "Enviando…" : "Enviar"}</button>
        </div>
      ) : (
        <p className="muted" style={{ fontSize: 13 }}>{t.status === "cerrado" ? "Este ticket está cerrado. Si el problema sigue, reporte uno nuevo." : "Puede seguir este ticket. Escribir en él le corresponde a quien lo reportó o al encargado de su empresa."}</p>
      )}
    </>
  );
}
