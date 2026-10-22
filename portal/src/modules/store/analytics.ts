/* Eventos de comercio de GA4 (add_to_cart, begin_checkout, purchase). Solo salen si la persona acepto la medicion en
   el aviso de Consent.tsx: sin consentimiento gtag no existe y esto no hace nada. La medicion nunca rompe la compra.
   "purchase" se manda al crear el pedido (numero ORD-…): GA4 mide el embudo; las ventas cobradas salen de la
   plataforma (solo pedidos pagados), nunca de GA4. */
type GtagWin = Window & { gtag?: (...a: unknown[]) => void };

/* Parametros de la URL que llevan un token firmado (el enlace de seguimiento ?pedido=… abre el pedido, el historial
   con notas y el comprobante subido). Nunca llegan a GA4: quien tenga acceso a la propiedad podria abrir el pedido. */
const SECRET_PARAMS = ["pedido", "token"];

/** URL sin los parametros con token; vacia si no se puede leer. Se usa para page_location / page_referrer / page_path. */
export function cleanUrl(href: string): string {
  if (!href) return "";
  try {
    const u = new URL(href, window.location.origin);
    for (const k of SECRET_PARAMS) u.searchParams.delete(k);
    u.hash = "";
    return u.toString();
  } catch {
    return "";
  }
}

/** Ruta + query sin token (page_path). */
export function cleanPath(pathname: string, search: string): string {
  const sp = new URLSearchParams(search);
  for (const k of SECRET_PARAMS) sp.delete(k);
  const q = sp.toString();
  return q ? `${pathname}?${q}` : pathname;
}

/** Contexto de pagina limpio que acompana cada hit (gtag usaria document.location.href si no se le pasa). */
export function cleanPage(): { page_location: string; page_referrer: string } {
  return { page_location: cleanUrl(window.location.href), page_referrer: cleanUrl(document.referrer) };
}

export type GaItem = { item_id: string; item_name: string; price: number; quantity: number };

export function track(event: "add_to_cart" | "begin_checkout" | "purchase", params: { currency: string; value: number; items: GaItem[]; transaction_id?: string }) {
  try {
    (window as GtagWin).gtag?.("event", event, { ...params, ...cleanPage() });
  } catch {
    /* sin medicion */
  }
}
