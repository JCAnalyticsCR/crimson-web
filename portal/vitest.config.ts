/* Pruebas unitarias del portal (`npm test`). Config propia para no tocar la del build (vite.config.ts): aqui no
   hay plugin de React ni navegador, solo modulos puros en Node. Las pruebas viven junto al codigo, en carpetas
   __tests__ dentro de src/, y `tsc -b` las compila igual que al resto (noUnusedLocals incluido).
   Lo que necesita un navegador de verdad (pantallas, scroll, capturas, axe) va en Playwright: ver e2e/README.md. */
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["src/**/__tests__/**/*.test.{ts,tsx}"],
    environment: "node",
    // una prueba marcada con .only que se cuela al repo no puede dejar al CI corriendo una sola prueba
    allowOnly: !process.env.CI,
  },
});
