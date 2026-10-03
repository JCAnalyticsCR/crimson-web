// Visor de fotos a pantalla completa, sin salir del sistema.
// Andres: "que las fotos del levantamiento se puedan ver ahi mismo y hacerlas grandes, para ensenarle a
// alguien: aqui va esto, aca va lo otro". Por eso cada foto lleva su rotulo (el punto al que pertenece),
// se pasa de una a otra con flechas, teclado o deslizando el dedo, y un toque amplia para ver el detalle.
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { I, Icon } from "./components";

export type Foto = { url: string; caption?: string };

export function Lightbox({ fotos, start, onClose }: { fotos: Foto[]; start: number; onClose: () => void }) {
  const [i, setI] = useState(start);
  const [zoom, setZoom] = useState(false);
  const toque = useRef<number | null>(null);
  const n = fotos.length;
  const ir = (d: number) => { setZoom(false); setI((x) => (x + d + n) % n); };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); onClose(); }
      else if (e.key === "ArrowRight") ir(1);
      else if (e.key === "ArrowLeft") ir(-1);
    };
    // en captura: el Escape cierra el visor, no el formulario que esta debajo
    window.addEventListener("keydown", onKey, true);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey, true); document.body.style.overflow = prev; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [n]);

  if (!n) return null;
  const f = fotos[i];
  return createPortal(
    <div className="lbox" role="dialog" aria-modal="true" aria-label="Fotografía ampliada" onClick={onClose}>
      <div className="lbox__top" onClick={(e) => e.stopPropagation()}>
        <span className="meta">{n > 1 ? `${i + 1} / ${n}` : "Fotografía"}</span>
        <div style={{ display: "flex", gap: 6 }}>
          <a className="lbox__btn" href={f.url} target="_blank" rel="noreferrer" title="Abrir original">Original</a>
          <button type="button" className="lbox__btn" onClick={onClose} aria-label="Cerrar"><Icon d={I.x} size={18} /></button>
        </div>
      </div>
      <div
        className={`lbox__stage${zoom ? " is-zoom" : ""}`}
        onClick={(e) => { e.stopPropagation(); setZoom((z) => !z); }}
        onTouchStart={(e) => { toque.current = e.touches[0].clientX; }}
        onTouchEnd={(e) => {
          if (zoom || toque.current === null) return;
          const dx = e.changedTouches[0].clientX - toque.current;
          toque.current = null;
          if (Math.abs(dx) > 50) ir(dx < 0 ? 1 : -1);
        }}
      >
        <img src={f.url} alt={f.caption || `Fotografía ${i + 1}`} draggable={false} />
      </div>
      {n > 1 && <button type="button" className="lbox__nav lbox__nav--prev" onClick={(e) => { e.stopPropagation(); ir(-1); }} aria-label="Anterior">‹</button>}
      {n > 1 && <button type="button" className="lbox__nav lbox__nav--next" onClick={(e) => { e.stopPropagation(); ir(1); }} aria-label="Siguiente">›</button>}
      <div className="lbox__cap" onClick={(e) => e.stopPropagation()}>
        {f.caption && <b>{f.caption}</b>}
        <span className="meta">{zoom ? "Tocá para ver completa" : "Tocá la foto para ampliar"}</span>
      </div>
    </div>,
    document.body,
  );
}
