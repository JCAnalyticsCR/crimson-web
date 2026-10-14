/* Vista Tablero (fase 1b): columnas por estado con los abiertos y lo resuelto en la ultima semana. Con
   support_desk.editar se arrastra para cambiar el estado (en el celular, "Mover a"); soltar en Resuelto abre el
   modal de resolver, que exige escribir la solucion. Mismo patron que el embudo de oportunidades. */
import { useState } from "react";
import { useSession } from "../../app/session";
import { PlazoChip } from "./TicketCard";
import { semaforoVivo } from "./ticketTime";
import { ESTADO, PRIO, type Ticket } from "./ticketTypes";

const COLUMNAS = ["nuevo", "asignado", "en_proceso", "esperando_cliente", "resuelto"];

export function TicketBoard({ rows, now, onOpen, onMove, onVerCerrados }: {
  rows: Ticket[]; now: number;
  onOpen: (t: Ticket, resolver?: boolean) => void;
  onMove: (t: Ticket, status: string) => void;
  onVerCerrados: () => void;
}) {
  const { allows } = useSession();
  const canEdit = allows("support_desk.editar");
  const [drag, setDrag] = useState<number | null>(null);
  const [over, setOver] = useState<string | null>(null);

  const soltar = (id: number, status: string) => {
    const t = rows.find((x) => x.id === id);
    if (!t || t.status === status) return;
    if (status === "resuelto") onOpen(t, true); // resolver exige la solucion: se abre el modal
    else onMove(t, status);
  };

  return (
    <>
      <p className="muted" style={{ fontSize: 12.5, margin: "0 0 8px" }}>
        {canEdit ? "Arrastrá una tarjeta para cambiar su estado." : "Tu rol no cambia el estado de los tickets."} Resueltos: los de los últimos 7 días ·{" "}
        <button type="button" className="btn btn--ghost btn--sm" onClick={onVerCerrados}>Ver cerrados</button>
      </p>
      <div className="board tk-board">
        {COLUMNAS.map((c) => {
          const lista = rows.filter((t) => t.status === c);
          return (
            <section
              key={c}
              className={`board__col${over === c && drag !== null ? " is-over" : ""}`}
              aria-label={ESTADO[c]?.label || c}
              onDragOver={(e) => { if (drag === null) return; e.preventDefault(); e.dataTransfer.dropEffect = "move"; if (over !== c) setOver(c); }}
              onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(null); }}
              onDrop={(e) => { e.preventDefault(); const id = Number(e.dataTransfer.getData("text/plain")) || drag; setOver(null); setDrag(null); if (id) soltar(id, c); }}
            >
              <div className="board__head"><b>{ESTADO[c]?.label || c}{c === "resuelto" ? " (7 días)" : ""}</b><span>{lista.length}</span></div>
              {lista.map((t) => {
                const s = semaforoVivo(t.semaforo, t.status, now);
                return (
                  <div
                    key={t.id}
                    role="button"
                    tabIndex={0}
                    className={`tk-mini${drag === t.id ? " is-dragging" : ""}`}
                    data-color={s.color}
                    draggable={canEdit}
                    title={canEdit ? undefined : "Tu rol no cambia el estado"}
                    onDragStart={(e) => { e.dataTransfer.setData("text/plain", String(t.id)); e.dataTransfer.effectAllowed = "move"; setDrag(t.id); }}
                    onDragEnd={() => { setDrag(null); setOver(null); }}
                    onClick={() => onOpen(t)}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(t); } }}
                  >
                    <b>{t.subject}</b>
                    <small><span className="mono">{t.number}</span><span style={{ color: PRIO[t.priority] }}>{t.priority}</span></small>
                    <small><span>{t.assigned || "Sin responsable"}</span></small>
                    <PlazoChip t={t} now={now} />
                    {canEdit && (
                      <select className="select tk-mini__move" value={t.status} aria-label="Mover a otro estado" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()} onChange={(e) => soltar(t.id, e.target.value)}>
                        {COLUMNAS.map((x) => <option key={x} value={x}>{x === t.status ? "Mover a…" : ESTADO[x]?.label || x}</option>)}
                      </select>
                    )}
                  </div>
                );
              })}
            </section>
          );
        })}
      </div>
    </>
  );
}
