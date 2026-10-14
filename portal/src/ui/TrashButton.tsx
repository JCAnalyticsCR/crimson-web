/* Icono de basurero en cada fila (Andres: "en cada apartado se puedan borrar... que haya un icono de basurero").
   No borra: manda a la papelera, de donde se restaura durante N dias (Archivo › Papelera). Antes de confirmar
   pregunta a la API si se puede: si algo lo impide (factura, inventario, pedidos...) lo dice y ofrece la
   alternativa (archivar o desactivar). Despues de mandarlo, el aviso trae "Deshacer". */
import { useState, type MouseEvent, type SyntheticEvent } from "react";
import { createPortal } from "react-dom";
import { api } from "../lib/api";
import { useSession } from "../app/session";
import { Icon, Modal } from "./components";
import { trashNeed, type TrashKind } from "./trash";
import "./trash.css";

export const TRASH_ICON = "M4 7h16|M10 11v6M14 11v6|M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13|M9 7V4h6v3";
export const ARCHIVE_ICON = "M3 4h18v4H3z|M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8|M10 12h4";

type Alt = { action: string; label: string; hint: string };
type Check = {
  number: string; title: string; purge_days: number;
  trash: string[]; archive: string[] | null; alt: Alt | null;
};

/** Quien puede mandar este tipo a la papelera (la API vuelve a verificarlo). */
export function useCanTrash(kind: TrashKind) {
  const { allows } = useSession();
  return trashNeed(kind).every((n) => allows(n));
}

export function TrashButton({ kind, id, number, onDone, onRestored, className = "" }: {
  kind: TrashKind; id: number; number?: string;
  /** Despues de mandarlo a la papelera (recargar la lista, cerrar el detalle). */
  onDone: () => void;
  /** Despues de "Deshacer" (por defecto, lo mismo que onDone). */
  onRestored?: () => void;
  className?: string;
}) {
  const { toast } = useSession();
  const can = useCanTrash(kind);
  const [check, setCheck] = useState<Check | null>(null);
  const [busy, setBusy] = useState(false);
  if (!can) return null;
  const ref = number || "este registro";

  const ask = async (e: MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    try { setCheck(await api<Check>(`/archive/${kind}/${id}/check`)); }
    catch (err) { toast(err instanceof Error ? err.message : "No se pudo", "bad"); }
    finally { setBusy(false); }
  };

  const restore = async () => {
    try {
      await api(`/archive/${kind}/${id}/restore`, { method: "POST" });
      toast(`${ref} restaurado`);
      (onRestored || onDone)();
    } catch (err) { toast(err instanceof Error ? err.message : "No se pudo restaurar", "bad"); }
  };

  const run = async (action: string) => {
    setBusy(true);
    try {
      await api(`/archive/${kind}/${id}/${action}`, { method: "POST" });
      setCheck(null);
      onDone();
      if (action === "trash") toast(`${ref} en la papelera`, "ok", { label: "Deshacer", run: restore });
      else if (action === "archive") toast(`${ref} ${check?.alt?.label === "Desactivar" ? "desactivado" : "archivado"}: se conserva en Archivo`, "ok", { label: "Deshacer", run: restore });
      else toast(`${ref}: listo`);
    } catch (err) { toast(err instanceof Error ? err.message : "No se pudo", "bad"); }
    finally { setBusy(false); }
  };

  const blocked = !!check && check.trash.length > 0;
  const stop = (e: SyntheticEvent) => e.stopPropagation();
  return (
    <>
      <button type="button" className={`trash-btn ${className}`} aria-label="Enviar a la papelera" title="Enviar a la papelera" disabled={busy} onClick={ask} onKeyDown={(e) => e.stopPropagation()}>
        <Icon d={TRASH_ICON} size={18} />
      </button>
      {check && createPortal(
        // fuera de la tabla (la celda fija del telefono no lo tapa) y sin que los clics lleguen a la fila o la tarjeta
        <div className="trash-dlg" onClick={stop} onMouseDown={stop} onKeyDown={stop} onPointerDown={stop}>
        <Modal
          title={blocked ? `${check.number || ref} no puede ir a la papelera` : `¿Enviar ${check.number || ref} a la papelera?`}
          onClose={() => setCheck(null)}
          foot={<>
            <button className="btn btn--ghost" onClick={() => setCheck(null)}>{blocked ? "Entendido" : "Cancelar"}</button>
            {!blocked && <button className="btn btn--crimson" autoFocus disabled={busy} onClick={() => run("trash")}><Icon d={TRASH_ICON} size={16} />Enviar a la papelera</button>}
            {blocked && check.alt && <button className="btn btn--soft" disabled={busy} onClick={() => run(check.alt!.action)}>{check.alt.label}</button>}
          </>}
        >
          {check.title && <p className="trash-dlg__what">{check.title}</p>}
          {!blocked ? (
            <p className="muted trash-dlg__text">
              Sale de las listas. Se puede restaurar desde <b>Archivo › Papelera</b> durante {check.purge_days} día{check.purge_days === 1 ? "" : "s"}; después se borra definitivamente.
            </p>
          ) : (
            <>
              <p className="trash-dlg__text">No se borra porque:</p>
              <ul className="trash-dlg__why">{check.trash.map((w) => <li key={w}>{w}</li>)}</ul>
              {check.alt ? <p className="muted trash-dlg__text">{check.alt.hint}</p> : <p className="muted trash-dlg__text">Se conserva tal cual; nada se modificó.</p>}
            </>
          )}
        </Modal>
        </div>,
        document.body,
      )}
    </>
  );
}

/** Para lo que nunca se borra (facturas, pagos, bitacora...): el icono se ve apagado y al tocarlo explica que hacer. */
export function TrashLocked({ reason }: { reason: string }) {
  const { toast } = useSession();
  return (
    <button type="button" className="trash-btn is-locked" aria-disabled="true" aria-label={`No se puede borrar: ${reason}`} title={reason}
      onClick={(e) => { e.stopPropagation(); e.preventDefault(); toast(reason, "bad"); }}>
      <Icon d={TRASH_ICON} size={18} />
    </button>
  );
}
