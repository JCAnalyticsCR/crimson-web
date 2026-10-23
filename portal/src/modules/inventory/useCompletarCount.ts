/* Numero junto a "Inventarios" y en su pestaña "Por completar": articulos que NO estan a la venta en la tienda por
   falta de datos (sin costo del proveedor o sin precio). Solo quien los completa (catalog.editar). Cada 5 minutos;
   un fallo de red no muestra error, solo no hay numero. */
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";

/* la pantalla Por completar avisa su total al recargar: el numero del menu no espera los 5 minutos */
export const COMPLETAR_EVENT = "crimson:completar-count";

export function useCompletarCount(): number {
  const { me, realAllows } = useSession();
  const [n, setN] = useState(0);
  const can = !!me && realAllows("catalog.editar") && realAllows("inventory.ver");
  useEffect(() => {
    if (!can) { setN(0); return; }
    let alive = true;
    const load = () => api<{ total: number }>("/catalog-completar/count").then((r) => { if (alive) setN(r.total); }).catch(() => undefined);
    load();
    const id = window.setInterval(() => { if (document.visibilityState === "visible") load(); }, 300_000);
    const onEvt = (e: Event) => { const v = Number((e as CustomEvent).detail); if (Number.isFinite(v)) setN(v); };
    window.addEventListener(COMPLETAR_EVENT, onEvt);
    return () => { alive = false; window.clearInterval(id); window.removeEventListener(COMPLETAR_EVENT, onEvt); };
  }, [can]);
  return n;
}
