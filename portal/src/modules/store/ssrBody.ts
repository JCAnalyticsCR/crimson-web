/* Relevo del contenido del servidor a la vitrina (O2-01b). Con settings.store.ssr_body encendido para el tipo de ruta,
   nginx incrusta en el HTML un <div id="sh-ssr"> con el contenido real (H1, precios, enlaces) y la hoja de la tienda.
   React monta igual en #root, oculto detras (index.html: #sh-ssr:not(:empty) ~ #root { visibility: hidden }), y cuando la vitrina
   ya pinto lo suyo (cabecera y sin esqueletos ni ruedas de carga) o pasan TOPE_MS, se quita #sh-ssr de una vez:
   - la posicion de lectura se conserva (mismo desplazamiento vertical);
   - si el foco estaba en un enlace del contenido del servidor, pasa al enlace con la misma direccion en la vitrina.
   Sin #sh-ssr (lo normal hoy) no hace nada. */

const LISTA = ".shop .sh-head";
const CARGANDO = ".spinner, .sh-skel, .vt-spin, .vt-kit--skel";
export const TOPE_MS = 6000;

let hecho = false;

function relevo(ssr: HTMLElement, root: HTMLElement) {
  if (hecho) return;
  hecho = true;
  const y = window.scrollY;
  const activo = document.activeElement;
  const href = activo instanceof HTMLAnchorElement && ssr.contains(activo) ? activo.getAttribute("href") : null;
  ssr.remove();
  document.documentElement.setAttribute("data-sh-ssr", "relevado");
  if (y > 0) window.scrollTo({ top: y, behavior: "auto" });
  if (href) {
    const mismo = [...root.querySelectorAll<HTMLAnchorElement>("a[href]")].find((a) => a.getAttribute("href") === href);
    mismo?.focus({ preventScroll: true });
  }
}

/** Llamar una vez al montar la tienda (StoreHost). Devuelve la funcion para dejar de mirar. */
export function relevoDelServidor(): () => void {
  const ssr = typeof document !== "undefined" ? document.getElementById("sh-ssr") : null;
  const root = typeof document !== "undefined" ? document.getElementById("root") : null;
  if (!ssr || !root || hecho) return () => {};
  // sin nginx (servidor de desarrollo) el bloque llega sin procesar: #sh-ssr vacio (solo un comentario). Se quita y listo.
  if (!ssr.firstElementChild) { ssr.remove(); return () => {}; }
  const listo = () => !!root.querySelector(LISTA) && !root.querySelector(CARGANDO);
  let obs: MutationObserver | null = null;
  let t: number | undefined;
  const fin = () => { obs?.disconnect(); window.clearTimeout(t); };
  const revisar = () => { if (listo()) { fin(); relevo(ssr, root); } };
  obs = new MutationObserver(revisar);
  obs.observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });
  t = window.setTimeout(() => { fin(); relevo(ssr, root); }, TOPE_MS);
  revisar();
  return fin;
}
