/* Gramatica de patrones de ruta de las etiquetas (registro.Etiqueta.rutas):
   "/"            solo la portada, y sin ?v= ni ?pedido= (la tienda pinta el pago y «Mi pedido» en cualquier ruta)
   "/catalogo"    exacta (misma exigencia sin v/pedido en rutas de tienda)
   "/p/*"         prefijo con al menos un caracter despues de "/p/" (misma exigencia)
   "?v=pagar"     compara searchParams.get("v"); el pathname no importa
   "?pedido=*"    comprueba has("pedido"); el pathname no importa
   Rutas de propuestas / Detras de escena / rentabilidad: exactas o con /*, sin la exigencia de v/pedido. */

export const PREFIJO_PROPUESTAS = /^\/(propuestas|detras-de-escena|rentabilidad)(\/|$)/;
export const esRutaDeTienda = (pathname: string) => !PREFIJO_PROPUESTAS.test(pathname);

export function coincideRuta(patron: string, pathname: string, search: string): boolean {
  const sp = new URLSearchParams(search);
  if (patron.startsWith("?")) {
    const [k, v = ""] = patron.slice(1).split("=");
    if (v === "*" || v === "") return sp.has(k);
    return sp.get(k) === v;
  }
  const tienda = esRutaDeTienda(pathname);
  if (tienda && (sp.has("v") || sp.has("pedido"))) return false;
  const p = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  if (patron.endsWith("/*")) {
    const base = patron.slice(0, -2);
    return p.startsWith(`${base}/`) && p.length > base.length + 1;
  }
  return p === patron;
}

export const coincideAlguna = (patrones: string[], pathname: string, search: string) => patrones.some((x) => coincideRuta(x, pathname, search));
