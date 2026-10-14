/* Compositor de seguimiento (Andres 16:56: "que de verdad yo pueda dar los seguimientos"). Vive en la tarjeta de la
   bandeja (1 clic, sin modal) y en el detalle; los dos usan el MISMO borrador por ticket (copia en este dispositivo,
   modo "never"): una nota publica cuenta como primera respuesta, asi que nunca se publica sola, solo con Guardar. */
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { useRecordAutosave } from "../../lib/useRecordAutosave";
import { PhotoStrip } from "../../ui/MediaPicker";
import { atajosFecha, cuandoCR, isoToLocal, localToISO } from "./ticketTime";
import { esAbierto, type Ticket } from "./ticketTypes";

const ATAJOS = ["Llamé al cliente", "Le escribí por WhatsApp", "Sin respuesta del cliente", "Pedimos repuesto", "Visita agendada", "Probado y funcionando"];

type Draft = {
  body: string; internal: boolean; hours: string; photos: string[];
  next_step: string; next_step_at: string | null; clear: boolean; status: string;
};
const blank = (t: Ticket): Draft => ({
  body: "", internal: false, hours: "", photos: [],
  next_step: t.next_step || "", next_step_at: t.next_step_at, clear: false,
  // como antes: responder un ticket nuevo o asignado lo deja "en proceso"
  status: t.status === "nuevo" || t.status === "asignado" ? "en_proceso" : "",
});

export function FollowComposer({ ticket, onSaved, autoFocus, focusNext, onCancel }: {
  ticket: Ticket;
  onSaved: (t: Ticket) => void;
  autoFocus?: boolean;
  /** foco en el proximo paso: true al abrir ("Sin próximo paso · Definir") o un numero que cambia en cada "Cambiar"
      del detalle (mueve el foco sin volver a montar el compositor) */
  focusNext?: boolean | number;
  onCancel?: () => void;
}) {
  const { allows, toast } = useSession();
  const canEdit = allows("support_desk.editar");
  const canFollow = allows("support_desk.seguir|support_desk.editar");
  const [draft, setDraft] = useState<Draft>(() => blank(ticket));
  const [otra, setOtra] = useState(false);
  const [busy, setBusy] = useState(false);
  const txt = useRef<HTMLTextAreaElement>(null);
  const nxt = useRef<HTMLInputElement>(null);
  useEffect(() => { if (focusNext) nxt.current?.focus(); else if (autoFocus) txt.current?.focus(); }, [autoFocus, focusNext]);

  const auto = useRecordAutosave<Draft>({
    ns: "tck-nota",
    mode: "never",
    value: draft,
    idOf: () => ticket.id,
    body: (d) => d as unknown as Record<string, unknown>,
    enabled: canFollow && ticket.status !== "cerrado" && !ticket.trashed_at,
    label: () => ticket.number,
    apply: (d) => setDraft(d),
  });

  /* El ticket cambio en el servidor (se resolvio, se reabrio, otro guardado): si aqui no se escribio nada, el
     compositor vuelve a partir del ticket nuevo (si no, quedaria el proximo paso viejo como "cambio" por guardar). */
  const base = useRef(JSON.stringify(blank(ticket)));
  useEffect(() => {
    const nb = blank(ticket), ser = JSON.stringify(nb);
    if (ser === base.current) return;
    const limpio = JSON.stringify(draft) === base.current;
    base.current = ser;
    if (limpio) { auto.fromServer(); setDraft(nb); setOtra(false); }
  }, [ticket]); // eslint-disable-line react-hooks/exhaustive-deps

  const escrito = JSON.stringify(draft) !== JSON.stringify(blank(ticket));
  // resuelto (o cerrado): el servidor no planifica seguimientos ("reabrilo"), asi que ni se ofrece ni se manda
  const planifica = esAbierto(ticket);
  const pasoCambio = planifica && (draft.clear || (draft.next_step.trim() || null) !== (ticket.next_step || null) || (draft.next_step_at || null) !== (ticket.next_step_at || null));
  const puede = !busy && (draft.body.trim().length > 0 || pasoCambio);
  const fechas = useMemo(() => atajosFecha(), []);

  const guardar = async () => {
    if (!puede) return;
    setBusy(true);
    try {
      const t = await auto.exclusive(draft, async () => {
        const paso = !pasoCambio ? {} : draft.clear ? { clear_next_step: true } : { next_step: draft.next_step.trim() || null, next_step_at: draft.next_step_at };
        if (draft.body.trim()) {
          return api<Ticket>(`/tickets/${ticket.id}/notes`, {
            method: "POST",
            json: { body: draft.body.trim(), internal: draft.internal, photos: draft.photos, hours: draft.hours ? Number(draft.hours) : null, status: canEdit && draft.status && draft.status !== ticket.status ? draft.status : null, ...paso },
          });
        }
        // sin texto: solo cambia el proximo paso (no hay nota ni primera respuesta)
        const quitar = draft.clear || (!draft.next_step.trim() && !draft.next_step_at);
        return api<Ticket>(`/tickets/${ticket.id}/next-step`, { method: "PUT", json: quitar ? { next_step: null, next_step_at: null } : { next_step: draft.next_step.trim() || null, next_step_at: draft.next_step_at } });
      });
      auto.fromServer();
      setDraft(blank(t));
      setOtra(false);
      toast(draft.body.trim() ? "Seguimiento guardado" : "Próximo paso guardado");
      onSaved(t);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  const cancelar = () => {
    if (escrito && !confirm("¿Descartar lo escrito?")) return;
    auto.discard();
    auto.fromServer(); // el formulario vacio es la nueva referencia, no un cambio
    setDraft(blank(ticket));
    setOtra(false);
    onCancel?.();
  };

  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));
  const chipTexto = (x: string) => set({ body: draft.body.trim() ? `${draft.body.trim()}. ${x}` : x });

  return (
    <div className="tk-compose" onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void guardar(); } }}>
      <textarea ref={txt} className="textarea" maxLength={4000} aria-label="¿Qué pasó?" placeholder="¿Qué pasó? (Ctrl+Enter guarda)" value={draft.body} onChange={(e) => set({ body: e.target.value })} />
      <div className="chips" aria-label="Atajos de texto">
        {ATAJOS.map((x) => <button type="button" key={x} className="chip" onClick={() => chipTexto(x)}>{x}</button>)}
      </div>

      {!planifica ? <p className="tk-compose__hint">Reabrí el ticket para planificar un seguimiento.</p> : (
      <div className="tk-compose__next">
        <div className="tk-compose__row">
          <label htmlFor={`paso-${ticket.id}`} style={{ fontWeight: 600 }}>Próximo paso</label>
          {draft.clear ? <span className="muted">Se quita al guardar</span>
            : draft.next_step_at ? <span className="muted">→ {cuandoCR(draft.next_step_at)}</span> : null}
        </div>
        <input id={`paso-${ticket.id}`} ref={nxt} className="input" maxLength={200} placeholder="Llamar al cliente, visita, pedir repuesto…" value={draft.next_step} onChange={(e) => set({ next_step: e.target.value, clear: false })} />
        <div className="chips">
          {fechas.map((f) => <button type="button" key={f.label} className={`chip${draft.next_step_at === f.iso ? " is-on" : ""}`} onClick={() => { set({ next_step_at: f.iso, clear: false }); setOtra(false); }}>{f.label}</button>)}
          <button type="button" className={`chip${otra ? " is-on" : ""}`} onClick={() => setOtra(true)}>Otra fecha…</button>
          {(ticket.next_step || draft.next_step || draft.next_step_at) && <button type="button" className="chip" onClick={() => { set({ next_step: "", next_step_at: null, clear: !!(ticket.next_step || ticket.next_step_at) }); setOtra(false); }}>Quitar</button>}
        </div>
        {otra && <input className="input" type="datetime-local" aria-label="Fecha y hora del próximo paso" value={isoToLocal(draft.next_step_at)} onChange={(e) => set({ next_step_at: localToISO(e.target.value), clear: false })} />}
      </div>
      )}

      <PhotoStrip value={draft.photos} onChange={(photos) => set({ photos })} label="Foto" privado="support_desk" />
      <div className="tk-compose__row">
        <label style={{ display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={draft.internal} onChange={(e) => set({ internal: e.target.checked })} />Nota interna (no cuenta como respuesta al cliente)</label>
        <input className="input input--mono" style={{ maxWidth: 100 }} inputMode="decimal" placeholder="horas" aria-label="Horas" value={draft.hours} onChange={(e) => set({ hours: e.target.value })} />
        {canEdit && (
          <select className="select select--sm" aria-label="Dejar en" value={draft.status} onChange={(e) => set({ status: e.target.value })}>
            <option value="">Dejar en: sin cambio</option>
            <option value="en_proceso">Dejar en: En proceso</option>
            <option value="esperando_cliente">Dejar en: Esperando al cliente</option>
          </select>
        )}
      </div>
      {!canEdit && <p className="tk-compose__hint">Podés registrar seguimiento; resolver, reasignar o cambiar el estado lo hace soporte.</p>}
      <div className="tk-compose__row" style={{ justifyContent: "flex-end" }}>
        {(onCancel || escrito) && <button type="button" className="btn btn--ghost btn--sm" onClick={cancelar}>Cancelar</button>}
        <button type="button" className="btn btn--crimson btn--sm" onClick={guardar} disabled={!puede}>{busy ? "Guardando…" : "Guardar seguimiento"}</button>
      </div>
      {auto.bar}
    </div>
  );
}
