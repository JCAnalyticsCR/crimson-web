/* Portal REAL para la compra de punta a punta (`npm run e2e:compra`, plan de la tienda O0-03): el mismo index.html y
   el mismo src/main.tsx que produccion, hablando con el API local de pruebas (api/scripts/tienda_e2e_local.py) a
   traves de /api, igual que el servidor de desarrollo de siempre (vite.config.ts). Sin nginx: no hay SSI ni las
   cabeceras de la tienda; eso se prueba con el docker-compose (infra/docker-compose.yml).

   Se abre como http://tienda.localhost:5301: un nombre que empieza con «tienda.» monta la tienda en la raiz
   (StoreHost.tsx), como tienda.crimsoncr.com, y *.localhost es esta misma maquina.
   El build sale a e2e/.dist-compra (fuera de git); no toca dist/ ni el build normal. */
import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const PUERTO = Number(process.env.E2E_PUERTO) || 5301;
const API = process.env.E2E_API_URL || `http://127.0.0.1:${Number(process.env.E2E_API_PUERTO) || 8101}`;
/* el proxy crea pedidos en el API al que apunta: solo esta maquina, igual que el candado de playwright.compra.config.ts
   (este proceso corre en Node y no lo cubre el --host-resolver-rules del navegador) */
if (!/^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?\/?$/.test(API)) {
  throw new Error(`E2E_API_URL=${API}: solo puede apuntar a esta máquina (http://127.0.0.1:<puerto>).`);
}
const proxy = { "/api": { target: API, changeOrigin: true, rewrite: (p: string) => p.replace(/^\/api/, "") } };

export default defineConfig({
  plugins: [react()],
  // solo esta maquina: ni el servidor de desarrollo ni el de vista previa escuchan en la red
  server: { host: "127.0.0.1", port: PUERTO, strictPort: true, proxy },
  preview: { host: "127.0.0.1", port: PUERTO, strictPort: true, proxy },
  build: { outDir: resolve(__dirname, "e2e/.dist-compra"), emptyOutDir: true, sourcemap: false, target: "es2022" },
});
