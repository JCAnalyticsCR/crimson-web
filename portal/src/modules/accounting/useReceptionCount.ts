/* Numero del menu "Contabilidad": comprobantes de proveedores por revisar (rojo si alguno vence en <= 2 dias habiles).
   Se pide cada 5 minutos con la cuenta real; un fallo de red no muestra error, solo no hay numero. */
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";

export type ReceptionSummary = {
  por_revisar: number; vencen_pronto: number; vencidos: number; mensajes_rechazados: number; errores: number;
  /** reunion 2, pedido 4: cuantas tienen la respuesta lista para aprobar y el estado del buzon en palabras */
  listas_para_aprobar?: number; buzon?: { state: "apagado" | "pendiente" | "error" | "ok"; label: string; last_run_at: string | null };
};

export function useReceptionCount(): { count: number; urgent: boolean } | null {
  const { me, realAllows } = useSession();
  const [sum, setSum] = useState<ReceptionSummary | null>(null);
  const can = !!me && realAllows("accounting.ver");
  useEffect(() => {
    if (!can) { setSum(null); return; }
    let alive = true;
    const load = () => api<ReceptionSummary>("/reception/summary").then((r) => { if (alive) setSum(r); }).catch(() => undefined);
    load();
    const id = window.setInterval(() => { if (document.visibilityState === "visible") load(); }, 300_000);
    return () => { alive = false; window.clearInterval(id); };
  }, [can]);
  if (!sum || !sum.por_revisar) return null;
  return { count: sum.por_revisar, urgent: sum.vencen_pronto + sum.vencidos > 0 };
}
