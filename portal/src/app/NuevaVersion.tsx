// Aviso de version nueva. El portal es una sola pagina: si alguien lo deja abierto todo el dia, sigue con el
// codigo viejo aunque ya se haya publicado una correccion (Andres seguia viendo las fotos en otra pestana).
// Cada 5 minutos, y al volver a la pestana, compara el archivo principal publicado con el que esta corriendo.
import { useEffect, useState } from "react";

const actual = () => document.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/index-"]')?.src.split("/assets/")[1] || null;

export function NuevaVersion() {
  const [hay, setHay] = useState(false);
  useEffect(() => {
    const mio = actual();
    if (!mio || import.meta.env.DEV) return; // en desarrollo Vite recarga solo
    let vivo = true;
    const revisar = async () => {
      try {
        const html = await (await fetch("/", { cache: "no-store" })).text();
        const pub = html.match(/\/assets\/(index-[\w-]+\.js)/)?.[1];
        if (vivo && pub && pub !== mio) setHay(true);
      } catch { /* sin red: se intenta en la proxima */ }
    };
    const t = setInterval(revisar, 5 * 60 * 1000);
    const alVolver = () => document.visibilityState === "visible" && revisar();
    document.addEventListener("visibilitychange", alVolver);
    return () => { vivo = false; clearInterval(t); document.removeEventListener("visibilitychange", alVolver); };
  }, []);
  if (!hay) return null;
  return (
    <div className="nueva-version" role="status">
      <span>Hay una versión nueva del sistema.</span>
      <button type="button" className="btn btn--crimson btn--sm" onClick={() => location.reload()}>Actualizar</button>
    </div>
  );
}
