/* Compra REAL de punta a punta en local (`npm run e2e:compra`, plan de la tienda O0-03): el portal de verdad contra
   el API de verdad, con una base desechable, datos semilla ficticios y pago simulado. Nunca contra produccion.
   Guia completa: e2e/README.md, seccion «Compra real en local».

   Dos formas de correr:
   - Sola (por defecto, sin Docker): Playwright levanta y apaga el API (api/scripts/tienda_e2e_local.py: SQLite nueva
     en la carpeta temporal, migraciones, semilla y uvicorn en 127.0.0.1:8101) y el portal (vite.compra.config.ts en
     127.0.0.1:5301). Con E2E_DATABASE_URL el API usa esa base local (el CI pasa una Postgres de servicio).
   - Contra el docker-compose ya levantado y sembrado: E2E_COMPRA_URL=http://tienda.localhost:5301. No levanta nada.

   Una compra a la vez (workers: 1): comparten la misma base y cada una aparta existencia. */
import { defineConfig } from "@playwright/test";

const PUERTO = Number(process.env.E2E_PUERTO) || 5301;
const API_PUERTO = Number(process.env.E2E_API_PUERTO) || 8101;
const YA_LEVANTADA = (process.env.E2E_COMPRA_URL || "").replace(/\/+$/, "");
const TIENDA = YA_LEVANTADA || `http://tienda.localhost:${PUERTO}`;
const CI = !!process.env.CI;
/* solo se aceptan direcciones de esta maquina: esta prueba crea pedidos */
if (!/^http:\/\/(127\.0\.0\.1|([a-z0-9-]+\.)*localhost)(:\d+)?$/.test(TIENDA)) {
  throw new Error(`E2E_COMPRA_URL=${TIENDA}: la compra de punta a punta solo corre contra esta máquina (http://tienda.localhost:<puerto>).`);
}
/* E2E_PYTHON: otro interprete con las dependencias del API (por defecto, el del proyecto con uv) */
const PYTHON = process.env.E2E_PYTHON || "uv run --locked python";
/* E2E_COMPRA_PORTAL=dev: el servidor de desarrollo de Vite en vez del build (para depurar con el codigo sin empaquetar) */
/* O1-11b: contrasena del administrador de prueba del panel (e2e/mi-tienda.spec.ts), al azar en cada corrida. Se fija
   en el proceso principal antes de levantar el API (que la recibe por el entorno) y los trabajadores la heredan. No
   queda escrita en ningun archivo. Contra un docker-compose ya levantado no existe y esas pruebas se saltan. */
if (!YA_LEVANTADA && !process.env.E2E_PANEL_PASSWORD) {
  const azar = Array.from({ length: 4 }, () => Math.random().toString(36).slice(2, 10)).join("");
  process.env.E2E_PANEL_PASSWORD = `Pn1-${azar}`;
}
/* O2-01d: secreto TOTP (base32, al azar en cada corrida) del segundo administrador de prueba, que entra con codigo 2FA */
if (!YA_LEVANTADA && !process.env.E2E_PANEL_TOTP) {
  const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  process.env.E2E_PANEL_TOTP = Array.from({ length: 32 }, () => B32[Math.floor(Math.random() * 32)]).join("");
}
const PORTAL = process.env.E2E_COMPRA_PORTAL === "dev"
  ? "npx vite --config vite.compra.config.ts"
  : "npx vite build --config vite.compra.config.ts && npx vite preview --config vite.compra.config.ts";

export default defineConfig({
  testDir: "./e2e",
  testMatch: /(compra|mi-tienda)\.spec\.ts$/,
  outputDir: "./test-results/compra",
  fullyParallel: false,
  workers: 1,
  forbidOnly: CI,
  retries: CI ? 1 : 0,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report/compra" }]],
  use: {
    baseURL: TIENDA,
    browserName: "chromium",
    locale: "es-CR",
    timezoneId: "America/Costa_Rica",
    colorScheme: "light",
    deviceScaleFactor: 1,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    /* El navegador no resuelve ningun nombre que no sea de esta maquina: ni un <link rel="preconnect"> (index.html
       trae uno al sitio del proveedor de fotos) abre una conexion hacia afuera. Los pedidos a otros sitios, ademas,
       se cortan y hacen fallar la prueba (soporte/tienda.ts). */
    launchOptions: { args: ["--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE *.localhost, EXCLUDE 127.0.0.1"] },
  },
  /* los dos anchos del criterio de la tarea: celular y tableta, tactiles como en playwright.config.ts */
  projects: [[375, 667], [768, 1024]].map(([w, h]) => ({
    name: `compra-w${w}`,
    use: { viewport: { width: w, height: h }, isMobile: true, hasTouch: true },
  })),
  webServer: YA_LEVANTADA ? undefined : [
    {
      command: `${PYTHON} -m scripts.tienda_e2e_local --puerto ${API_PUERTO} --base-url ${TIENDA}`,
      cwd: "../api",
      url: `http://127.0.0.1:${API_PUERTO}/health`,
      // nunca se reutiliza lo que ya escuche en el puerto: podria ser un API con datos de verdad
      reuseExistingServer: false,
      timeout: 240_000,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      command: PORTAL,
      url: `http://127.0.0.1:${PUERTO}/`,
      reuseExistingServer: false,
      timeout: 240_000,
      stdout: "ignore",
      stderr: "pipe",
      // E2E_API_URL fijo al API que levanta esta misma corrida: una variable heredada no puede desviar el proxy /api
      env: { E2E_PUERTO: String(PUERTO), E2E_API_PUERTO: String(API_PUERTO), E2E_API_URL: `http://127.0.0.1:${API_PUERTO}` },
    },
  ],
});
