/* Piezas propias del equipo venta (no son de la API comun): foto de producto por la API simulada, estado guardado
   en la sesion, fila "sello + que es" y avisos de demostracion. Todo importa lo comun SOLO desde ./comun. */
import { useEffect, useState, type ReactNode } from "react";
import { notifyDemo } from "../../mockApi";
import { IC, Ico, Viewfinder } from "../../../modules/store/storeKit";
import { almacen, SelloPropuesta, useProducto } from "./comun";

/** Foto real del snapshot (la pide useProducto a la API simulada). Sin foto: el visor con el icono de camara. */
export function FotoProducto({ id, label, right, className = "", tam = 400 }: { id: number; label?: string; right?: string; className?: string; tam?: number }) {
  const p = useProducto(id);
  return (
    <Viewfinder className={`ven-foto ${className}`} label={label} right={right}>
      {p?.image ? <img src={p.image} alt="" loading="lazy" decoding="async" width={tam} height={tam} /> : <span className="ven-foto__vacia"><Ico d={IC.cam} size={22} /></span>}
    </Viewfinder>
  );
}

/** Estado de la maqueta guardado en sessionStorage (crimson-demo:venta:*). Si el almacenamiento falla, vive en memoria. */
export function useGuardado<T>(clave: string, inicial: () => T, validar: (x: unknown) => T | null): [T, (f: (prev: T) => T) => void] {
  const [v, setV] = useState<T>(() => {
    try { const raw = almacen.sesion.get(clave); return (raw ? validar(JSON.parse(raw)) : null) ?? inicial(); } catch { return inicial(); }
  });
  useEffect(() => { almacen.sesion.set(clave, JSON.stringify(v)); }, [clave, v]);
  return [v, (f) => setV((prev) => f(prev))];
}

/** "[sello] texto": dice que parte de la pantalla ya existe y que parte es la propuesta. */
export function SelloLinea({ estado, id, children }: { estado: "propuesta" | "existente"; id?: string; children: ReactNode }) {
  return (
    <li className={`ven-sl ven-sl--${estado}`}>
      <SelloPropuesta estado={estado} id={id} tamano="chico" />
      <span>{children}</span>
    </li>
  );
}

export const avisar = (title: string, text: string) => notifyDemo({ title, text });

/** numero con 2 decimales sin simbolo («55 603,20»), para tablas de documentos */
const nf2 = new Intl.NumberFormat("es-CR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const num2 = (n: number) => nf2.format(n);
/** fecha numerica «13/10/2026» para sellos y documentos */
export const fechaNum = (d: Date) => new Intl.DateTimeFormat("es-CR", { day: "2-digit", month: "2-digit", year: "numeric" }).format(d);
