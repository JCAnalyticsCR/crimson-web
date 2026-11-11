import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { cssDeLaTienda, mapaDeTrozos } from "./vite.tienda";

// En desarrollo el portal habla con la API a traves de /api (sin CORS ni secretos en el front).
// API_PROXY permite apuntar a otro puerto cuando el 8000 esta ocupado.
const API = process.env.API_PROXY || "http://127.0.0.1:8000";

/* Navegadores que se soportan (O1-18). Antes era "es2022" a secas: un iPhone con Safari 14 o un Chrome viejo de
   Android recibian sintaxis que no entienden y la tienda quedaba en blanco. es2020 + Safari 14 + Chrome 87 cubre los
   equipos que todavia circulan en Costa Rica; subirlo solo a conciencia. La prueba de humo corre en WebKit
   (playwright.config.ts, proyecto "webkit"). El build de pruebas (vite.e2e.config.ts) usa el mismo objetivo. */
export const OBJETIVO_NAVEGADORES = ["es2020", "safari14", "chrome87"];
export default defineConfig({
  // mapaDeTrozos (O2-01d): deja .peso-tienda/trozos.json para scripts/peso-tienda.mjs (peso de la tienda y frontera
  // con el panel); no cambia lo que se publica. cssDeLaTienda (O2-01b): enlace con hash a la hoja de la tienda dentro
  // del bloque SSI del cuerpo de index.html.
  plugins: [react(), mapaDeTrozos(), cssDeLaTienda()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
  build: { sourcemap: false, target: OBJETIVO_NAVEGADORES },
});
