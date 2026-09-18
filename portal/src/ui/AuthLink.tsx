/* Enlace a un recurso de la API que requiere sesion (Excel, PDF, XML, colillas). */
import type { MouseEvent, ReactNode } from "react";
import { downloadFile, openFile } from "../lib/api";
import { useSession } from "../app/session";

export default function AuthLink({ path, download, className = "btn btn--ghost btn--sm", children, title }: { path: string; download?: string; className?: string; children: ReactNode; title?: string }) {
  const { toast } = useSession();
  const go = async (e: MouseEvent) => {
    e.preventDefault();
    try { if (download) await downloadFile(path, download); else await openFile(path); }
    catch (err) { toast(err instanceof Error ? err.message : "No se pudo abrir", "bad"); }
  };
  return <a href={`/api${path}`} className={className} onClick={go} title={title}>{children}</a>;
}
