/* Archivar / Enviar a la papelera dentro del detalle (levantamientos, proyectos, oportunidades, ordenes de trabajo...).
   Andres: "que haya un icono de basurero para que no tenga tanta info ahi": dos iconos en vez de dos botones con
   texto. Las reglas (dependencias vivas -> 409) viven en la API; aqui solo se muestra su mensaje. */
import { useState } from "react";
import { api } from "../lib/api";
import { useSession } from "../app/session";
import { Icon } from "./components";
import { ARCHIVE_ICON, TrashButton, useCanTrash } from "./TrashButton";
import type { TrashKind } from "./trash";
import "./trash.css";

export type ArchiveKind = TrashKind;

export function ArchiveActions({ kind, id, number, archivedAt, trashedAt, onDone }: {
  kind: ArchiveKind; id: number; number?: string; archivedAt?: string | null; trashedAt?: string | null; onDone: () => void;
}) {
  const { toast } = useSession();
  const can = useCanTrash(kind);
  const [busy, setBusy] = useState(false);
  if (!can) return null;
  const what = number || "este registro";

  const run = async (action: "archive" | "restore") => {
    setBusy(true);
    try {
      await api(`/archive/${kind}/${id}/${action}`, { method: "POST" });
      onDone();
      if (action === "archive") {
        toast(`${what} archivado: sale de las listas y se conserva`, "ok", {
          label: "Deshacer",
          run: () => { api(`/archive/${kind}/${id}/restore`, { method: "POST" }).then(() => { toast(`${what} restaurado`); onDone(); }, (e) => toast(e instanceof Error ? e.message : "No se pudo", "bad")); },
        });
      } else toast(`${what} restaurado`);
    } catch (e) {
      toast(e instanceof Error ? e.message : "No se pudo", "bad");
    } finally { setBusy(false); }
  };

  if (archivedAt || trashedAt) {
    // abierto desde Archivo: se dice donde esta y se ofrece volverlo a las listas
    return (
      <>
        <span className={`badge badge--${trashedAt ? "bad" : "muted"}`}>{trashedAt ? "En la papelera" : "Archivado"}</span>
        <button className="btn btn--soft btn--sm" disabled={busy} onClick={() => run("restore")}>Restaurar</button>
      </>
    );
  }

  return (
    <>
      <button type="button" className="icon-btn" disabled={busy} onClick={() => run("archive")} aria-label="Archivar" title="Archivar: sale de las listas, se conserva y se restaura desde Archivo">
        <Icon d={ARCHIVE_ICON} size={18} />
      </button>
      <TrashButton kind={kind} id={id} number={number} onDone={onDone} />
    </>
  );
}
