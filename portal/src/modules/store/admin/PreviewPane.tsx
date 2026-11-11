/* Vista previa en vivo de la Apariencia (O4-06). En vez de un iframe de la tienda (que obligaria a abrir
   X-Frame-Options en nginx para el panel), pinta los componentes REALES de la tienda (storeKit: la tarjeta de producto
   y el precio) dentro de un contenedor .shop con las variables del tema del borrador. Nada sale del navegador y nada
   se guarda. Conmutador de 375, 768 y escritorio; en tablet y telefono se abre a pantalla completa. Datos ficticios. */
import { ProductCard, StoreProvider, type StoreCtx, type VProd } from "../storeKit";
import { themeVars, type Theme } from "../theme";
import { EstilosTienda } from "./EstilosTienda";
import { useState } from "react";

const ANCHOS: [string, number | null][] = [["Teléfono", 375], ["Tablet", 768], ["Escritorio", null]];

const ejemplo = (id: number, name: string, price: number, state: string, label: string): VProd => ({
  id, code: `DEMO-${id}`, name, brand: "Marca de ejemplo", image: null, images: [], description: null, category: "Ejemplo", category_id: null,
  path: `/p/ejemplo-${id}`, tax_rate: 13, item_type: "producto", display_price: price, display_currency: "CRC",
  availability: { state, label, accion: state === "disponible" ? "comprar" : "whatsapp", disponible: state === "disponible" }, variants: [], price, currency: "CRC",
});

export function PreviewPane({ theme, name, tagline, logo, full, onFull }: { theme: Theme; name: string; tagline: string; logo: string | null; full: boolean; onFull: (v: boolean) => void }) {
  const [ancho, setAncho] = useState<number | null>(375);
  const ctx: StoreCtx = {
    slug: "", base: "", hostMode: true, isShop: true, compare: [], toggleCompare: () => {}, addToCart: () => {}, openCart: () => {},
    api: async () => { throw new Error("vista previa"); },
    store: { name, tagline, logo_url: logo, primary: theme.accent || "#e2233a", secondary: "#15131a", font: "Arial", kind: "tienda", whatsapp: "", currency: "CRC", legal: { privacy: "", terms: "" } },
  };
  const cuerpo = (
    <div className="ap-frame" style={{ width: ancho ? `${ancho}px` : "100%" }}>
      <EstilosTienda />
      <div className="shop ap-shop" style={themeVars(theme)} inert>
        <StoreProvider value={ctx}>
          <div className="ap-head">
            {logo ? <img src={logo} alt="" style={{ height: 28, width: "auto" }} /> : <b>{name || "Su tienda"}</b>}
            <span className="sh-meta">{tagline}</span>
          </div>
          <div className="ap-hero">
            <h3 style={{ margin: 0 }}>{name || "Su tienda"}</h3>
            <span className="sh-btn sh-btn--crimson">Ver catálogo</span>
          </div>
          <ul className="vt-grid" style={{ listStyle: "none", padding: 0 }}>
            <li><ProductCard p={ejemplo(1, "Cámara de ejemplo 2 MP", 1950, "disponible", "En bodega")} /></li>
            <li><ProductCard p={ejemplo(2, "Cable de red de ejemplo", 850, "consultar", "Disponibilidad por confirmar")} /></li>
          </ul>
        </StoreProvider>
      </div>
    </div>
  );
  return (
    <div className={full ? "ap-full" : "mt-box"} role={full ? "dialog" : undefined} aria-modal={full || undefined} aria-label="Vista previa de la tienda">
      <div className="mt-row" style={{ justifyContent: "space-between" }}>
        <b style={{ fontSize: 13 }}>Vista previa</b>
        <span className="mt-row" role="group" aria-label="Ancho de la vista previa">
          {ANCHOS.map(([l, w]) => <button key={l} className={`btn btn--sm ${ancho === w ? "btn--soft" : "btn--ghost"}`} aria-pressed={ancho === w} onClick={() => setAncho(w)}>{l}</button>)}
          <button className="btn btn--ghost btn--sm ap-fullbtn" onClick={() => onFull(!full)}>{full ? "Cerrar" : "Pantalla completa"}</button>
        </span>
      </div>
      <div className="ap-scroll">{cuerpo}</div>
      <p className="meta" style={{ textTransform: "none", margin: 0 }}>Así se ve con lo que está eligiendo; la tienda cambia al «Guardar y publicar».</p>
    </div>
  );
}
