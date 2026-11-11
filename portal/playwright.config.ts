/* Pruebas de pantalla de la tienda (`npm run e2e`). Corren contra la TIENDA DE PRUEBAS: la vitrina real con el API
   simulado en el navegador y un catalogo ficticio (e2e/app/, build propio en vite.e2e.config.ts). No hace falta API,
   base de datos ni Docker, y nada sale a internet. Guia completa: e2e/README.md.
   La compra real contra el API local es otra configuracion: playwright.compra.config.ts (`npm run e2e:compra`).

   Un proyecto por ancho. Los cinco anchos son los del criterio de salida del plan de la tienda (sin scroll
   horizontal a 320, 360, 375, 768 y 1280 px); capturas y axe corren solo en los anchos que les tocan. */
import { defineConfig } from "@playwright/test";

const PUERTO = Number(process.env.E2E_PUERTO) || 5301;
const CI = !!process.env.CI;

/* alto de ventana de un equipo tipico de ese ancho; hasta 768 se emula tactil (como un celular o una tableta) */
const ANCHOS: [number, number][] = [[320, 568], [360, 740], [375, 667], [768, 1024], [1280, 800]];
const SOLO_CAPTURAS = [375, 768, 1280]; // capturas base (toHaveScreenshot)
const SOLO_AXE = [375, 1280]; // informe de accesibilidad y recorrido de compra
/* la compra real contra el API local (O0-03) tiene su propia configuracion y sus servidores: `npm run e2e:compra` */
const COMPRA_REAL = "**/compra.spec.ts";
/* Prueba de humo en WebKit (O1-18: Safari de iPhone, el navegador que mas se rompe con sintaxis nueva). En CI corre
   siempre (la imagen de Playwright trae WebKit); en una maquina local solo con E2E_WEBKIT=1, despues de
   `npx playwright install webkit`. */
const WEBKIT = CI || process.env.E2E_WEBKIT === "1";
const HUMO = "**/humo.spec.ts";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  outputDir: "./test-results",
  // capturas base por sistema: las letras no se dibujan igual en Windows que en Linux (ver e2e/README.md)
  snapshotPathTemplate: "{testDir}/capturas-base/{platform}/{arg}-{projectName}{ext}",
  fullyParallel: true,
  forbidOnly: CI,
  retries: CI ? 2 : 0,
  workers: CI ? 2 : undefined,
  timeout: 45_000,
  expect: {
    timeout: 8_000,
    toHaveScreenshot: { animations: "disabled", caret: "hide", scale: "css", maxDiffPixelRatio: 0.001 },
  },
  reporter: [["list"], ["./e2e/soporte/informe.ts"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    baseURL: `http://127.0.0.1:${PUERTO}`,
    browserName: "chromium",
    locale: "es-CR",
    timezoneId: "America/Costa_Rica",
    colorScheme: "light",
    deviceScaleFactor: 1,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [...ANCHOS.map(([w, h]) => ({
    name: `w${w}`,
    use: { viewport: { width: w, height: h }, isMobile: w < 1024, hasTouch: w < 1024 },
    testIgnore: [
      COMPRA_REAL,
      ...(SOLO_CAPTURAS.includes(w) ? [] : ["**/capturas.spec.ts"]),
      ...(SOLO_AXE.includes(w) ? [] : ["**/axe.spec.ts", "**/compra-mock.spec.ts"]),
    ],
  })),
  ...(WEBKIT ? [{ name: "webkit-375", testMatch: HUMO, use: { browserName: "webkit" as const, viewport: { width: 375, height: 667 }, isMobile: true, hasTouch: true } }] : []),
  ],
  webServer: {
    command: "npm run e2e:servir",
    url: `http://127.0.0.1:${PUERTO}/`,
    // nunca se reutiliza lo que ya escuche en el puerto: podria ser otro servidor (el portal real en desarrollo)
    reuseExistingServer: false,
    timeout: 180_000,
    stdout: "ignore",
    stderr: "pipe",
  },
});
