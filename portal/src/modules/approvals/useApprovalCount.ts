/* Numero del menu "Aprobaciones". Se pide con la cuenta real; en vista previa se suman solo las secciones que el rol
   previsualizado puede aprobar (needs), asi el numero coincide con lo que veria esa persona. Un fallo de red no
   muestra error: solo no hay numero. */
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";

/** Un pendiente de la bandeja: `to` abre la pantalla donde se aprueba (aqui no se aprueba nada, salvo `action`). */
export type ApprovalItem = {
  id: number; ref: string; title: string; subtitle: string | null; who: string | null;
  /** ISO de fecha y hora, o solo fecha (AAAA-MM-DD) en comisiones */
  since: string | null; badge: string | null;
  /** solo donde el permiso de la seccion ya implica ver ese dinero (cotizaciones con precios, aliados con costos, comisiones) */
  amount: string | number | null; currency: string | null; to: string;
  /** solo "precios_por_revisar" (plan 1.12): el precio sugerido se aplica aqui con un clic (POST a `path` con `body`,
      el precio que se muestra: si el de hoy es otro la API responde 409 y no aplica otro monto) */
  action?: { label: string; path: string; confirm?: string; body?: unknown };
};
export type ApprovalSection = {
  key: string; label: string; hint: string; needs: string[]; to: string; count: number; items: ApprovalItem[];
  /** solo "levantamientos_por_revisar": a quien le llega el aviso ("¿quién es la oficina?") */
  reviewers?: string[];
  /** solo "precios_por_revisar": la ultima revision quedo frenada por una posible falla de datos */
  brake?: { reason: string; frenados: number; at?: string | null } | null;
};
export type ApprovalsSummary = { total: number; sections: Pick<ApprovalSection, "key" | "label" | "needs" | "to" | "count">[] };
export type ApprovalsFull = { total: number; sections: ApprovalSection[] };

export function useApprovalCount(): number | null {
  const { me, allows, realAllows } = useSession();
  const loc = useLocation();
  const [sum, setSum] = useState<ApprovalsSummary | null>(null);
  const can = !!me && realAllows("approvals.ver");
  useEffect(() => {
    if (!can) { setSum(null); return; }
    let alive = true;
    const load = () => api<ApprovalsSummary>("/approvals/summary").then((r) => { if (alive) setSum(r); }).catch(() => undefined);
    load(); // al entrar y al cambiar de pantalla (despues de aprobar algo, el numero baja)
    const id = window.setInterval(() => { if (document.visibilityState === "visible") load(); }, 120_000);
    return () => { alive = false; window.clearInterval(id); };
  }, [can, loc.pathname]);
  if (!sum) return null;
  return sum.sections.filter((s) => s.needs.every((n) => allows(n))).reduce((a, s) => a + s.count, 0);
}
