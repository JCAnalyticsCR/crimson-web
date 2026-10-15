/* Detalle del ticket para el aliado: conversación (lo público, lo que Crimson compartió con su empresa y lo de su
   equipo), nota nueva (por defecto solo Crimson la ve; "Visible para el cliente" apagada), estado (en proceso,
   esperando al cliente o resuelto con solución), horas y reasignación dentro de su equipo. Cerrar es de Crimson. */
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, I, Icon } from "../../ui/components";
import { PhotoStrip } from "../../ui/MediaPicker";
import { ESTADO_TICKET, PRIORIDAD, TIPO_TICKET, errorText, fmtWhen, useAlly, type Tech, type TicketFull } from "./shared";

export default function AllyTicket() {
  const { id } = useParams();
  const { toast } = useSession();
  const { readOnly, can } = useAlly();
  const [t, setT] = useState<TicketFull | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [body, setBody] = useState("");
  const [fotos, setFotos] = useState<string[]>([]);
  const [publica, setPublica] = useState(false);
  const [horas, setHoras] = useState("");
  const [solucion, setSolucion] = useState("");
  const [techs, setTechs] = useState<Tech[]>([]);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { api<TicketFull>(`/aliado/tickets/${id}`).then((x) => { setT(x); setSolucion(x.solution || ""); }).catch((e) => setErr(errorText(e))); }, [id]);
  useEffect(load, [load]);
  useEffect(() => { if (can("asignar")) api<Tech[]>("/aliado/tecnicos").then(setTechs).catch(() => setTechs([])); }, [can]);
  if (err) return <p className="muted">{err}</p>;
  if (!t) return <div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  const act = t.can_act && !readOnly;
  const nota = async () => {
    setBusy(true);
    try { setT(await api<TicketFull>(`/aliado/tickets/${t.id}/notas`, { method: "POST", json: { body, photos: fotos, visible_al_cliente: publica, hours: horas || null } })); setBody(""); setFotos([]); setHoras(""); toast("Nota agregada"); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo guardar", "bad"); }
    finally { setBusy(false); }
  };
  const cambiar = async (json: Record<string, unknown>) => {
    setBusy(true);
    try { setT(await api<TicketFull>(`/aliado/tickets/${t.id}`, { method: "PATCH", json })); toast("Ticket actualizado"); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo actualizar", "bad"); }
    finally { setBusy(false); }
  };
  return (
    <>
      <div className="cp-h">
        <div><div className="meta">{t.number} · {TIPO_TICKET[t.kind] || t.kind} · prioridad {PRIORIDAD[t.priority] || t.priority}</div><h1 className="h1">{t.subject}</h1></div>
        <span className={`badge badge--${t.fuera_de_tiempo ? "bad" : "info"}`}>{ESTADO_TICKET[t.status] || t.status}</span>
      </div>
      <div className="cp-grid cp-grid--2">
        <div className="cp-card">
          <dl className="al-kv">
            <dt>Cliente</dt><dd>{t.customer || "—"}</dd>
            {t.project && <><dt>Proyecto</dt><dd>{t.project}</dd></>}
            <dt>Contacto</dt><dd>{t.contact.name || "—"}{t.contact.phone ? ` · ${t.contact.phone}` : ""}</dd>
            {t.asset && <><dt>Equipo</dt><dd>{t.asset.name}{t.asset.model ? ` · ${t.asset.model}` : ""}{t.asset.serial ? ` · serie ${t.asset.serial}` : ""}{t.asset.location ? ` · ${t.asset.location}` : ""}</dd></>}
            <dt>Responsable</dt><dd>{t.assigned || "Sin responsable"}</dd>
            <dt>Primera respuesta</dt><dd>{t.first_reply_at ? fmtWhen(t.first_reply_at) : `antes de ${fmtWhen(t.due_at)}`}</dd>
            <dt>Resolver antes de</dt><dd>{fmtWhen(t.resolve_due_at)}</dd>
            <dt>Horas</dt><dd>{Number(t.hours || 0)}</dd>
          </dl>
          {t.description && <p style={{ whiteSpace: "pre-wrap" }}>{t.description}</p>}
          {t.photos.length > 0 && <PhotoStrip value={t.photos} disabled uploadPath="/aliado/media" onChange={() => {}} />}
        </div>
        {act && (
          <div className="cp-card cp-form">
            <b>Estado</b>
            <div className="al-actions">
              <button className="btn btn--soft btn--sm" disabled={busy} onClick={() => cambiar({ status: "en_proceso" })}>En proceso</button>
              <button className="btn btn--soft btn--sm" disabled={busy} onClick={() => cambiar({ status: "esperando_cliente" })}>Esperando al cliente</button>
            </div>
            <Field label="Solución (para resolver)"><textarea className="textarea" rows={2} value={solucion} onChange={(e) => setSolucion(e.target.value)} /></Field>
            <button className="btn btn--crimson btn--sm" style={{ alignSelf: "flex-start" }} disabled={busy || !solucion.trim()} onClick={() => cambiar({ status: "resuelto", solution: solucion })}><Icon d={I.check} />Marcar resuelto</button>
            {can("asignar") && techs.length > 0 && (
              <Field label="Responsable de su equipo">
                <select className="select" value={t.assigned_to ?? ""} onChange={(e) => cambiar({ assigned_to: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">Sin responsable</option>
                  {techs.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                </select>
              </Field>
            )}
            <span className="meta">Cerrar el ticket lo hace Crimson.</span>
          </div>
        )}
      </div>
      <h2 className="h3 al-sec">Conversación</h2>
      <div className="cp-msgs">
        {t.notes.map((n) => (
          <div key={n.id} className={`cp-msg${n.team === "aliado" ? " cp-msg--me" : ""}`}>
            <small>{n.author} · {fmtWhen(n.created_at)}{n.internal ? " · solo Crimson y su empresa" : ""}</small>
            {n.body}
            {n.photos.length > 0 && <PhotoStrip value={n.photos} disabled uploadPath="/aliado/media" onChange={() => {}} />}
          </div>
        ))}
        {!t.notes.length && <p className="muted">Sin movimientos todavía.</p>}
      </div>
      {act && (
        <div className="cp-card cp-form" style={{ marginTop: 12 }}>
          <textarea className="textarea" rows={3} placeholder="Escribí lo que hicieron o encontraron" value={body} maxLength={4000} onChange={(e) => setBody(e.target.value)} />
          <PhotoStrip value={fotos} uploadPath="/aliado/media" onChange={setFotos} />
          <div className="cp-grid cp-grid--2">
            <Field label="Horas (opcional)"><input className="input input--mono" inputMode="decimal" value={horas} onChange={(e) => setHoras(e.target.value)} /></Field>
            <label className="pf-bool" style={{ alignSelf: "end" }}><input type="checkbox" checked={publica} onChange={(e) => setPublica(e.target.checked)} />Visible para el cliente</label>
          </div>
          <button className="btn btn--crimson" style={{ alignSelf: "flex-start" }} disabled={busy || !body.trim()} onClick={nota}><Icon d={I.check} />Agregar nota</button>
        </div>
      )}
    </>
  );
}
