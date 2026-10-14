/* ¿Cambio algo desde que se abrio? Para la guardia del Modal (formularios cortos sin autoguardado): compara el valor
   con la foto tomada al abrir (cuando pasa de null a algo o cambia el id). */
import { useRef } from "react";

export function useDirty<T>(value: T | null, idOf?: (v: T) => unknown): boolean {
  const snap = useRef<{ key: unknown; ser: string } | null>(null);
  if (value === null) {
    snap.current = null;
    return false;
  }
  const key = idOf ? idOf(value) : undefined;
  const ser = JSON.stringify(value);
  if (!snap.current || snap.current.key !== key) snap.current = { key, ser };
  return snap.current.ser !== ser;
}
