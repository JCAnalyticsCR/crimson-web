/* Tarjeta de la bandeja: lo que Andres pidio ver de un vistazo (cliente, tipo, prioridad, responsable) mas lo que
   faltaba para "dar los seguimientos": cuanto queda del plazo, el proximo paso y el ultimo seguimiento. El
   seguimiento se registra aqui mismo, sin abrir nada (1 clic). */
import { useEffect, useState } from "react";
import { parseTs } from "../../lib/api";
import { TrashButton } from "../../ui/TrashButton";
import { FollowComposer } from "./FollowComposer";
import { cuandoCR, haceCR, plazoTexto, semaforoVivo } from "./ticketTime";
import { ESTADO, KIND, PRIO, PRIO_LABEL, esAbierto, type Ticket } from "./ticketTypes";

export function PlazoChip({ t, now }: { t: Ticket; now: number }) {
  const s = semaforoVivo(t.semaforo, t.status, now);
  const texto = esAbierto(t) ? plazoTexto(s) : ESTADO[t.status]?.label || t.status;
  return (
    <span className="tk-plazo" data-color={s.color}>
      <span className="tk-dot" aria-label={`Semáforo: ${s.color}`} role="img" />
      {texto}
    </span>
  );
}

export function TicketCard({ t, now, canFollow, canTake, detailOpen, onOpen, onTake, onChanged, onTrashed }: {
  t: Ticket; now: number;
  /** el detalle de ESTE ticket esta abierto: su compositor es el del detalle (mismo borrador), el de aqui se cierra */
  detailOpen?: boolean;
  /** support_desk.seguir|editar */
  canFollow: boolean;
  /** editar + ver_todo + sin responsable */
  canTake: boolean;
  onOpen: () => void;
  onTake: () => void;
  onChanged: (t: Ticket) => void;
  onTrashed: () => void;
}) {
  const [compose, setCompose] = useState<null | "texto" | "paso">(null);
  /* Un solo compositor por ticket a la vez: tarjeta y detalle comparten el borrador del dispositivo, y si los dos
     quedaran montados, al publicar en el detalle el de la tarjeta volveria a guardar el texto viejo como borrador.
     Al cerrarse aqui, lo escrito queda en el dispositivo y el detalle ofrece recuperarlo. */
  const abrir = () => { setCompose(null); onOpen(); };
  useEffect(() => { if (detailOpen) setCompose(null); }, [detailOpen]);
  const s = semaforoVivo(t.semaforo, t.status, now);
  const abierto = esAbierto(t);
  const atrasado = !!t.next_step_at && abierto && parseTs(t.next_step_at).getTime() < now;
  const lf = t.last_follow;
  return (
    <article className="tk-card" data-color={abierto ? s.color : "neutro"}>
      <div className="tk-card__top">
        <button type="button" className="tk-card__num" onClick={abrir}>{t.number}</button>
        <span>·</span>
        <span style={{ color: PRIO[t.priority], fontWeight: 700 }}>{PRIO_LABEL[t.priority] || t.priority}</span>
        <span>· {KIND[t.kind] || t.kind}{t.level > 1 ? ` · N${t.level}` : ""}</span>
        {!["nuevo", "asignado"].includes(t.status) && <span className={`badge badge--${ESTADO[t.status]?.tone || "muted"}`}>{ESTADO[t.status]?.label || t.status}</span>}
        <PlazoChip t={t} now={now} />
      </div>
      <button type="button" className="tk-card__subject" onClick={abrir}>{t.subject}</button>
      <div className="tk-card__who">
        <span>{t.customer_id ? t.customer : `Sin ficha${t.customer ? ` (reporte web: ${t.customer})` : ""}`}</span>
        <span>·</span>
        {t.assigned ? <span>Responsable: {t.assigned}</span> : <span className="badge badge--warn">Sin responsable</span>}
        {t.allies.map((a) => <span key={a.key} className="badge badge--muted" title="Empresa aliada del proyecto">{a.name}</span>)}
      </div>
      {abierto && (t.next_step || t.next_step_at ? (
        <div className={`tk-card__next${atrasado ? " is-late" : ""}`}>
          → {t.next_step || "Seguimiento"}{t.next_step_at ? ` · ${cuandoCR(t.next_step_at, now)}` : ""}{atrasado ? " · atrasado" : ""}
        </div>
      ) : (
        <div className="tk-card__next muted">Sin próximo paso{canFollow && <> · <button type="button" onClick={() => setCompose("paso")}>Definir</button></>}</div>
      ))}
      {lf ? (
        <div className="tk-card__last">
          Último: {haceCR(lf.at, now)} · {lf.user || "Sistema"}: <q>{lf.body}</q>{" "}
          {lf.from_client ? <span className="badge badge--info">Cliente respondió</span> : lf.internal ? <span className="badge badge--muted">interna</span> : null}
        </div>
      ) : (
        <div className="tk-card__last">Sin seguimientos todavía · abierto {haceCR(t.created_at, now)}</div>
      )}
      <div className="tk-card__acts">
        {canFollow && abierto && !compose && <button type="button" className="btn btn--soft btn--sm" onClick={() => setCompose("texto")}>Registrar seguimiento</button>}
        {canTake && <button type="button" className="btn btn--ghost btn--sm" onClick={onTake}>Tomar</button>}
        <button type="button" className="btn btn--ghost btn--sm" onClick={abrir}>Abrir</button>
        <span className="row-end"><TrashButton kind="ticket" id={t.id} number={t.number} onDone={onTrashed} /></span>
      </div>
      {compose && (
        <div className="tk-card__compose">
          <FollowComposer ticket={t} autoFocus={compose === "texto"} focusNext={compose === "paso"} onCancel={() => setCompose(null)} onSaved={(x) => { setCompose(null); onChanged(x); }} />
        </div>
      )}
    </article>
  );
}
