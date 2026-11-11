/* Tema de la tienda (O4-06): lo que el administrador elige en Mi Tienda -> Apariencia, como variables CSS del
   contenedor .shop (shop.css las define: --crimson, --crimson-dark, --crimson-soft, --r, --bg, --serif). Modulo puro:
   lo usan la vista previa del panel y la tienda publica, y se prueba en Vitest (__tests__/tema.test.ts).

   Enganche para la tienda publica (PublicStore.tsx / StoreHost.tsx, de la Ola 2): el API ya manda `theme` dentro de
   `store` (GET /public/store/{slug} y /public/store-domain); basta con
     <div className="shop" style={themeVars(store.theme)}>
   Sin `theme` (API viejo o sin elegir) themeVars devuelve {} y la tienda queda exactamente como hoy. */

export type Theme = {
  /** clasico = Arial (el de hoy); crimson = Archivo, solo si el dueno lo aprueba (O2-04) */
  preset?: "clasico" | "crimson";
  accent?: string;
  /** radio de marcos y botones, en px (0 a 16) */
  radius?: number;
  /** fondo de pagina: crema (el de hoy) o blanco */
  background?: "crema" | "blanco";
};

export const THEME_DEFAULT: Required<Theme> = { preset: "clasico", accent: "#e2233a", radius: 6, background: "crema" };
const HEX = /^#[0-9a-fA-F]{6}$/;
const FONDOS = { crema: { bg: "#f6f2ee", bg2: "#efe9e3" }, blanco: { bg: "#ffffff", bg2: "#f4f4f5" } };

function rgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
const lin = (c: number) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
export function luminance(hex: string): number {
  const [r, g, b] = rgb(hex);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
/** contraste WCAG entre dos colores (1 a 21) */
export function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((m, n) => n - m);
  return Math.round(((x + 0.05) / (y + 0.05)) * 100) / 100;
}
/** el color de acento lleva texto blanco (botones): AA pide 4,5 */
export const accentOk = (accent: string) => HEX.test(accent) && contrast(accent, "#ffffff") >= 4.5;

/** un tono mas oscuro (hover) */
export function darker(hex: string, f = 0.86): string {
  const [r, g, b] = rgb(hex).map((c) => Math.max(0, Math.round(c * f)));
  return `#${[r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

/** variables CSS del tema para el contenedor .shop; {} si no hay nada elegido (la tienda queda como hoy) */
export function themeVars(t?: Theme | null): Record<string, string> {
  if (!t) return {};
  const out: Record<string, string> = {};
  if (t.accent && HEX.test(t.accent)) {
    const [r, g, b] = rgb(t.accent);
    out["--crimson"] = t.accent;
    out["--crimson-dark"] = darker(t.accent);
    out["--crimson-soft"] = `rgba(${r}, ${g}, ${b}, .10)`;
  }
  if (typeof t.radius === "number" && t.radius >= 0 && t.radius <= 16) out["--r"] = `${t.radius}px`;
  if (t.background && FONDOS[t.background]) { out["--bg"] = FONDOS[t.background].bg; out["--bg-2"] = FONDOS[t.background].bg2; }
  if (t.preset === "crimson") out["--serif"] = "Archivo, Arial, Helvetica, sans-serif";
  return out;
}
