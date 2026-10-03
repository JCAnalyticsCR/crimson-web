// Boton que abre un adjunto (foto o PDF) en el visor de la misma pantalla, en vez de otra pestana.
import { useState, type ReactNode } from "react";
import { Lightbox } from "./Lightbox";

export function VerArchivo({ url, caption, className, children }: { url: string; caption?: string; className?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)}>{children}</button>
      {open && <Lightbox fotos={[{ url, caption }]} start={0} onClose={() => setOpen(false)} />}
    </>
  );
}
