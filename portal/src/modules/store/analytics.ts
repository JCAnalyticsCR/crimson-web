/* Eventos de comercio de GA4 (add_to_cart, begin_checkout, purchase). Solo salen si la persona acepto la medicion en
   el aviso de Consent.tsx: sin consentimiento gtag no existe y esto no hace nada. La medicion nunca rompe la compra.
   "purchase" se manda al crear el pedido (numero ORD-…): GA4 mide el embudo; las ventas cobradas salen de la
   plataforma (solo pedidos pagados), nunca de GA4. */
type GtagWin = Window & { gtag?: (...a: unknown[]) => void };

export type GaItem = { item_id: string; item_name: string; price: number; quantity: number };

export function track(event: "add_to_cart" | "begin_checkout" | "purchase", params: { currency: string; value: number; items: GaItem[]; transaction_id?: string }) {
  try {
    (window as GtagWin).gtag?.("event", event, params);
  } catch {
    /* sin medicion */
  }
}
