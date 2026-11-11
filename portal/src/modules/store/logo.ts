/* Logo de la cabecera de la tienda (O2-01d). El de Mi Tienda suele ser /logo.png del portal: 1024 x 237 px y 43 KB, sin
   hash y con no-cache (index de nginx), para pintarlo a 30 px de alto. Esta copia es la misma imagen a 2x (259 x 60 px,
   ~3 KB, 256 colores con transparencia), con hash en el nombre y cache de un ano (/assets/). ?no-inline: un archivo
   aparte, no base64 dentro del JavaScript. Un logo propio subido (otra direccion) se usa tal cual. */
import LOGO_2X from "./logo-tienda.png?no-inline";

const PORTAL = /^(?:https?:\/\/[^/]+)?\/logo\.png(?:\?.*)?$/i;

export function logoSrc(url: string | null | undefined): string | null {
  if (!url) return null;
  const propio = PORTAL.test(url) && (!/^https?:/i.test(url) || (typeof location !== "undefined" && url.startsWith(location.origin)));
  return propio ? LOGO_2X : url;
}
