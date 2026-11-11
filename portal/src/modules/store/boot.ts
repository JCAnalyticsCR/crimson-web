/* Datos de arranque de la vitrina (O2-01a). En el dominio de la tienda, nginx incrusta por SSI un
   <script type="application/json" id="sh-boot"> (api: /public/seo/boot) con el slug, los datos base de la tienda (los de
   /public/store/<slug> sin los textos legales) y, en la portada, la portada (/showcase). Con eso la tienda pinta cabecera
   y hero sin dos llamadas en serie (/store-domain y luego /store); igual las pide en segundo plano para quedar al dia.
   Si el bloque no esta (servidor de desarrollo, panel, API caido) o no se puede leer, todo sigue como antes. */

export type Boot = { v: number; slug: string; path: string; store?: unknown; showcase?: unknown };

let leido: Boot | null | undefined;
let portadaUsada = false;

/** El bloque de arranque de esta carga de pagina (se lee una sola vez). null si no hay o no sirve. */
export function readBoot(): Boot | null {
  if (leido !== undefined) return leido;
  leido = null;
  try {
    const el = typeof document !== "undefined" ? document.getElementById("sh-boot") : null;
    const b = el?.textContent ? (JSON.parse(el.textContent) as Boot) : null;
    if (b && typeof b === "object" && typeof b.slug === "string" && b.slug) leido = b;
  } catch { /* JSON danado: se ignora y la tienda pide sus datos como siempre */ }
  return leido;
}

/** Datos base de la tienda del arranque, si son de esta tienda. */
export function bootStore<T>(slug: string): T | null {
  const b = readBoot();
  return b && b.slug === slug && b.store && typeof b.store === "object" ? (b.store as T) : null;
}

/** Portada del arranque, solo si el servidor la armo para esta misma ruta y solo para la primera vista de la portada en
    esta carga: la portada llama a expireBootShowcase() al desmontarse y, al volver mas tarde, la pide como siempre.
    Se puede leer mas de una vez mientras tanto (StrictMode llama dos veces al inicializador de useState). */
export function takeBootShowcase<T>(slug: string, rel: string): T | null {
  const b = readBoot();
  if (portadaUsada || !b || b.slug !== slug || b.path !== rel || !b.showcase || typeof b.showcase !== "object") return null;
  return b.showcase as T;
}
export const expireBootShowcase = () => { portadaUsada = true; };

/** solo para las pruebas */
export const _resetBoot = () => { leido = undefined; portadaUsada = false; };
