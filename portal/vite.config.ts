import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En desarrollo el portal habla con la API en :8000 a traves de /api (sin CORS ni secretos en el front).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
  build: { sourcemap: false, target: "es2022" },
});
