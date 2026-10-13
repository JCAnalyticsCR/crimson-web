/* Hooks compartidos de las maquetas. Nada sale a la red: useProducto pide al mockApi de la demo. */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { CLAVES, EVENTOS, PROPUESTAS, type PropuestaId } from "../registro";
import { almacen } from "./pedidos";

/** Paso actual leido de ?paso=<id> (un id desconocido da 0). irA hace push: «atrás» del navegador retrocede un paso. */
export function usePaso(ids: readonly string[]): [indice: number, irA: (indice: number) => void] {
  const [sp, setSp] = useSearchParams();
  const actual = sp.get("paso");
  const i = actual ? ids.indexOf(actual) : 0;
  const indice = i < 0 ? 0 : i;
  const clave = ids.join("|");
  const irA = useCallback((n: number) => {
    const lista = clave.split("|");
    const destino = lista[Math.max(0, Math.min(lista.length - 1, n))];
    if (!destino) return;
    setSp((prev) => { const next = new URLSearchParams(prev); next.set("paso", destino); return next; });
  }, [clave, setSp]);
  return [indice, irA];
}

// ---------------------------------------------------------------- «Me interesa»
const VALIDOS = new Set<string>(PROPUESTAS.map((p) => p.id));
const leerInteres = (): PropuestaId[] => {
  try {
    const raw = JSON.parse(almacen.local.get(CLAVES.interes) || "[]");
    return Array.isArray(raw) ? (raw.filter((x) => typeof x === "string" && VALIDOS.has(x)) as PropuestaId[]) : [];
  } catch { return []; }
};
const ordenar = (ids: PropuestaId[]) => PROPUESTAS.map((p) => p.id).filter((id) => ids.includes(id));
function escribirInteres(ids: PropuestaId[]) {
  const limpio = ordenar([...new Set(ids)]);
  almacen.local.set(CLAVES.interes, JSON.stringify(limpio));
  window.dispatchEvent(new CustomEvent<PropuestaId[]>(EVENTOS.interes, { detail: limpio }));
}

export function useInteres(): {
  ids: PropuestaId[]; tiene: (id: PropuestaId) => boolean; alternar: (id: PropuestaId) => void;
  agregar: (ids: PropuestaId[]) => void; quitar: (ids: PropuestaId[]) => void;
} {
  const [ids, setIds] = useState<PropuestaId[]>(leerInteres);
  useEffect(() => {
    const on = () => setIds(leerInteres());
    const onStorage = (e: StorageEvent) => { if (e.key === null || e.key === CLAVES.interes) on(); };
    window.addEventListener(EVENTOS.interes, on);
    window.addEventListener("storage", onStorage);
    return () => { window.removeEventListener(EVENTOS.interes, on); window.removeEventListener("storage", onStorage); };
  }, []);
  return useMemo(() => ({
    ids,
    tiene: (id: PropuestaId) => ids.includes(id),
    alternar: (id: PropuestaId) => { const cur = leerInteres(); escribirInteres(cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]); },
    agregar: (nuevos: PropuestaId[]) => escribirInteres([...leerInteres(), ...nuevos.filter((x) => VALIDOS.has(x))]),
    quitar: (fuera: PropuestaId[]) => escribirInteres(leerInteres().filter((x) => !fuera.includes(x))),
  }), [ids]);
}

// ---------------------------------------------------------------- producto del catalogo de la demo
export type ProductoDemo = {
  id: number; name: string; code: string; image: string | null; display_price: number;
  path: string; availability: { state: string; label: string };
};
const cache = new Map<number, Promise<ProductoDemo | null>>();
function pedirProducto(id: number): Promise<ProductoDemo | null> {
  let p = cache.get(id);
  if (!p) {
    p = fetch(`/api/public/store/crimson/item/x-${id}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: Record<string, unknown> | null) => (d ? {
        id: Number(d.id), name: String(d.name || ""), code: String(d.code || ""), image: typeof d.image === "string" ? d.image : null,
        display_price: Number(d.display_price) || 0, path: String(d.path || ""),
        availability: (d.availability as { state: string; label: string }) || { state: "", label: "" },
      } : null))
      .catch(() => null);
    cache.set(id, p);
    p.then((v) => { if (!v) cache.delete(id); }); // un fallo se puede reintentar
  }
  return p;
}

/** Datos de un producto real del snapshot (lo responde mockApi; nada sale a la red). null mientras carga o si falla. */
export function useProducto(id: number): ProductoDemo | null {
  const [p, setP] = useState<ProductoDemo | null>(null);
  useEffect(() => {
    let vivo = true;
    setP(null);
    pedirProducto(id).then((v) => { if (vivo) setP(v); });
    return () => { vivo = false; };
  }, [id]);
  return p;
}
