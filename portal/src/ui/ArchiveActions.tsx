/* Archivar / Mover a papelera: mismos dos botones en el detalle de levantamientos, proyectos, oportunidades y
   ordenes de trabajo. Las reglas (dependencias vivas -> 409) viven en la API; aqui solo se muestra su mensaje. */
import { useState } from "react";
import { api } from "../lib/api";
import { useSession } from "../app/session";

export type ArchiveKind = "survey" | "project" | "opportunity" | "work_order";

const MODULE: Record<ArchiveKind, string> = { survey: "field", project: "projects", opportunity: "crm_pipeline", work_order: "field" };

export function ArchiveActions({ kind, id, number, archivedAt, trashedAt, onDone }: {
  kind: ArchiveKind; id: number; number?: string; archivedAt?: string | null; trashedAt?: string | null; onDone: () => void;
}) {
  const { allows, toast } = useSession();
  const [busy, setBusy] = useState(false);
  if (!allows(`${MODULE[kind]}.editar`)) return null;

  const run = async (action: "archive" | "trash" | "restore") => {
    const what = number || "este registro";
    const ok = action === "restore" ? true : action === "archive"
      ? window.confirm(`¿Archivar ${what}? Sale de las listas activas y se puede restaurar desde Administración › Archivo.`)
      : window.confirm(`¿Mover ${what} a la papelera? Se borra definitivamente cuando cumpla los días de la papelera; mientras tanto se puede restaurar.`);
    if (!ok) return;
    setBusy(true);
    try {
      await api(`/archive/${kind}/${id}/${action}`, { method: "POST" });
      toast(action === "archive" ? `${what} archivado` : action === "trash" ? `${what} en la papelera` : `${what} restaurado`);
      onDone();
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
      <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => run("archive")} title="Sale de las listas; se conserva y se restaura">Archivar</button>
      <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => run("trash")} title="Próximo a borrar: se restaura mientras esté en la papelera" style={{ color: "var(--crimson, #e2233a)" }}>Mover a papelera</button>
    </>
  );
}
