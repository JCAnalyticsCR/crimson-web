/* Mejora 3 de la demo: la guia (paso de pago) y «Ver dónde va» de las etiquetas del pago necesitan un carrito con algo.
   PublicStore lee el carrito UNA vez al montar (localStorage "crimson-shop:crimson", forma Line[]) y no escucha
   "storage": por eso se escribe el carrito y se avisa con EVENTOS.recargarTienda para que main-demo vuelva a montar
   la tienda. Si el almacenamiento no guarda, NO se avisa (volver a montar borraria el carrito que vive en memoria). */
import { CLAVES, EVENTOS } from "../registro";

export const NOTA_CARRITO = "Pusimos 2 × Cámara bullet Hikvision DS-2CD2043G2-LI2U 4MP AcuSense de ejemplo en tu carrito para mostrarte el pago completo.";

type Line = { key: string; id: number; variant_id: number | null; name: string; image: string | null; price: number; currency: string; qty: number };

const RESPALDO: Line = {
  key: "68:0", id: 68, variant_id: null, name: "Cámara bullet Hikvision DS-2CD2043G2-LI2U(2.8MM) 4MP AcuSense smart hybrid light", image: null, price: 63054, currency: "CRC", qty: 2,
};

function leer(): Line[] | null {
  try { const raw = localStorage.getItem(CLAVES.carrito); const v = raw ? JSON.parse(raw) : []; return Array.isArray(v) ? v : []; } catch { return null; }
}

/** Siembra 2 camaras de ejemplo si el carrito esta vacio. Devuelve la nota para mostrar, o null si no sembro. */
export async function sembrarCarritoSiVacio(): Promise<string | null> {
  const actual = leer();
  if (actual === null) return null; // almacenamiento bloqueado
  if (actual.length > 0) return null;
  let image: string | null = null;
  let price = RESPALDO.price;
  try {
    const r = await fetch("/api/public/store/crimson/item/x-68"); // lo responde mockApi: nada sale a la red
    if (r.ok) {
      const d = (await r.json()) as { image?: string | null; display_price?: number };
      image = typeof d.image === "string" ? d.image : null;
      if (Number(d.display_price) > 0) price = Number(d.display_price);
    }
  } catch { /* respaldo escrito a mano */ }
  const linea: Line = { ...RESPALDO, image, price };
  const texto = JSON.stringify([linea]);
  try { localStorage.setItem(CLAVES.carrito, texto); } catch { return null; }
  let guardado = false;
  try { guardado = localStorage.getItem(CLAVES.carrito) === texto; } catch { guardado = false; }
  if (!guardado) return null;
  window.dispatchEvent(new CustomEvent(EVENTOS.recargarTienda));
  return NOTA_CARRITO;
}
