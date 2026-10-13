/* Build de la DEMOSTRACION ESTATICA de la tienda: `npm run build:demo` -> ../docs/demo-tienda/ (GitHub Pages,
   https://crimsoncr.com/demo-tienda/). Solo la vitrina con la API simulada en el navegador (src/demo/).
   No toca el build normal (vite.config.ts / index.html / src/main.tsx). */
import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: resolve(__dirname, "src/demo"),
  base: "/demo-tienda/",
  publicDir: resolve(__dirname, "public"),
  plugins: [react()],
  build: {
    outDir: resolve(__dirname, "../docs/demo-tienda"),
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
    assetsInlineLimit: 0, // el snapshot JSON queda como archivo aparte (se pide con fetch al mismo origen)
  },
});
