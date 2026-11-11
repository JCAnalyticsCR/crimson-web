/* Las hojas de la tienda (shop.css y vitrina.css) para las vistas previas del panel (cajon del producto y Apariencia).
   Van como texto (?inline) y no como import de CSS: importarlas desde el panel hacia que Rollup las sacara a un trozo
   compartido con la vitrina y partia su trozo (scripts/peso-tienda.mjs y cssDeLaTienda lo necesitan entero). Todas
   sus reglas cuelgan de .shop, asi que no tocan el resto del panel. */
import shop from "../shop.css?inline";
import vitrina from "../vitrina.css?inline";

export function EstilosTienda() {
  return <style>{shop + vitrina}</style>;
}
