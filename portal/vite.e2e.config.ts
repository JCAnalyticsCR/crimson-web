/* Build de la TIENDA DE PRUEBAS que usa Playwright (`npm run e2e`): la vitrina real con el API simulado en el
   navegador y un catalogo ficticio (e2e/app/). Sale a e2e/.dist (fuera de git) y se sirve en 127.0.0.1:5301.
   No toca el build normal (vite.config.ts / index.html / src/main.tsx) ni el de la demo (vite.demo.config.ts). */
import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const PUERTO = Number(process.env.E2E_PUERTO) || 5301;

export default defineConfig({
  root: resolve(__dirname, "e2e/app"),
  publicDir: resolve(__dirname, "public"),
  plugins: [react()],
  // solo esta maquina: ni el servidor de desarrollo ni el de vista previa escuchan en la red
  server: { host: "127.0.0.1", port: PUERTO, strictPort: true, fs: { allow: [resolve(__dirname)] } },
  preview: { host: "127.0.0.1", port: PUERTO, strictPort: true },
  build: {
    outDir: resolve(__dirname, "e2e/.dist"),
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
  },
});
