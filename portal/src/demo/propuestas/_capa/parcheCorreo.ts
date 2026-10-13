/* Mejora 2 de la demo. OrderStatus (tienda real, intocable) dice «No pudimos enviarte el correo de confirmación…»
   cuando el correo no salio; en la demo nunca sale (email.status "demo", que es lo cierto). Aqui se reemplaza ESE
   texto por el de la demostracion, en el mismo nodo de texto. `.ck-thanks > span` coincide con dos spans
   («¡Gracias…!» y el aviso), por eso se ubica por el texto. Idempotente: si React lo repinta, se vuelve a aplicar. */

const ORIGINAL = "No pudimos enviarte el correo de confirmación";
export const TEXTO_CORREO_DEMO = "Pedido de demostración: en la tienda real aquí te llega la confirmación por correo, con el enlace para seguir tu pedido.";

function aplicar() {
  document.querySelectorAll<HTMLSpanElement>(".ck-thanks > span").forEach((s) => {
    const t = s.firstChild;
    if (t && t.nodeType === Node.TEXT_NODE && (t.nodeValue || "").startsWith(ORIGINAL)) t.nodeValue = TEXTO_CORREO_DEMO;
  });
}

/** Instala el observador; devuelve la funcion que lo quita. El MutationObserver ya agrupa los cambios por microtarea
    y corre ANTES de pintar: el texto original nunca llega a verse (con rAF se veria un cuadro). La consulta es una
    sola y barata (.ck-thanks > span). */
export function instalarParcheCorreo(): () => void {
  const mo = new MutationObserver(aplicar);
  mo.observe(document.body, { childList: true, subtree: true, characterData: true });
  aplicar();
  return () => mo.disconnect();
}
