import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En desarrollo el portal habla con la API a traves de /api (sin CORS ni secretos en el front).
// API_PROXY permite apuntar a otro puerto cuando el 8000 esta ocupado.
const API = process.env.API_PROXY || "http://127.0.0.1:8000";
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
  build: { sourcemap: false, target: "es2022" },
});
