/* Piezas de formulario de Mi Tienda por secciones (O4-02): todas controladas por el borrador de la seccion. Nada se
   guarda al perder el foco: lo guarda la barra de abajo. */
import { useEffect, useState } from "react";

const HEX = /^#[0-9a-fA-F]{6}$/;
export const isHex = (v: string) => HEX.test(v);

/** Color: el selector y el texto cambian el borrador; un texto que no es #RRGGBB se marca y no se guarda. */
export function ColorInput({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  const [txt, setTxt] = useState(value);
  useEffect(() => setTxt(value), [value]);
  const bad = !isHex(txt);
  return (
    <div style={{ display: "flex", gap: 8, minWidth: 0 }}>
      <input type="color" value={isHex(txt) ? txt : value} onChange={(e) => { setTxt(e.target.value); onChange(e.target.value); }} style={{ width: 44, height: 38, border: 0, background: "none", flex: "0 0 auto" }} aria-label={`${label}: elegir color`} />
      <input className="input input--mono" style={{ textAlign: "left", minWidth: 0 }} value={txt} aria-label={`${label}: código`} aria-invalid={bad || undefined}
        onChange={(e) => { setTxt(e.target.value); if (isHex(e.target.value)) onChange(e.target.value); }} />
    </div>
  );
}

/** Mover un elemento de una lista (subir o bajar), sin animar. */
export function moveItem<T>(list: T[], i: number, dir: -1 | 1): T[] {
  const j = i + dir;
  if (j < 0 || j >= list.length) return list;
  const b = [...list];
  [b[i], b[j]] = [b[j], b[i]];
  return b;
}

/** Contador de caracteres para textos con tope (Google: 60 en el titulo, 155 en la descripcion). */
export function Counter({ value, max }: { value: string; max: number }) {
  const n = value.length;
  return <small className="meta" style={{ textTransform: "none", color: n > max ? "var(--warn)" : undefined }} aria-live="polite">{n} de {max} caracteres{n > max ? " · Google lo corta" : ""}</small>;
}
