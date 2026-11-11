/* Catálogo → Contenido por aprobar (O3-11) contra el API local de pruebas: va con `npm run e2e:compra`, que levanta el
   API, el portal y crea el administrador de prueba (playwright.compra.config.ts). Nada se aplica a la tienda:
   - un lote de nombres comerciales se crea en borrador desde la pantalla y la tienda sigue igual;
   - sin scroll de lado a 320, 375 y 768 px (las tablas pasan a tarjetas con table--stack). */
import type { APIRequestContext, Page } from "@playwright/test";
import { expect, test } from "./soporte/compra";

const ADMIN = "panel.e2e@example.com";
const CLAVE = process.env.E2E_PANEL_PASSWORD || "";
const panel = (baseURL: string | undefined) => (baseURL || "").replace(/\/\/([a-z0-9-]+\.)*localhost/, "//127.0.0.1");

async function entrar(page: Page, base: string) {
  await page.goto(`${base}/login`);
  await page.locator('input[type="email"]').fill(ADMIN);
  await page.locator('input[autocomplete="current-password"]').fill(CLAVE);
  await page.locator("button.lg-go").click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));
  const hola = page.locator(".welcome");
  if (await hola.isVisible().catch(() => false) || await hola.waitFor({ timeout: 3000 }).then(() => true, () => false)) {
    await hola.getByRole("button", { name: /Empezar/ }).click();
    await expect(hola).toBeHidden();
  }
  await page.evaluate(() => { history.pushState({}, "", "/productos/contenido"); dispatchEvent(new PopStateEvent("popstate")); });
  await expect(page.getByRole("heading", { name: "Contenido por aprobar" })).toBeVisible();
}

async function token(servidor: APIRequestContext): Promise<string> {
  const r = await servidor.post("/api/auth/login", { data: { email: ADMIN, password: CLAVE } });
  expect(r.ok(), await r.text()).toBeTruthy();
  return ((await r.json()) as { access_token: string }).access_token;
}

const sinScrollDeLado = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test.beforeEach(({}, info) => {
  test.skip(!CLAVE, "sin administrador de prueba (API ya levantado aparte): ver playwright.compra.config.ts");
  test.skip(info.project.name !== "compra-w768", "el panel se mira a 768 px y aqui mismo se achica a 375 y 320");
});

test("[O3-11] crear un lote en borrador no cambia la tienda y la pantalla no tiene scroll de lado", async ({ page, baseURL, servidor }) => {
  const base = panel(baseURL);
  await entrar(page, base);
  await page.getByRole("button", { name: "Ver vista previa" }).click();
  await expect(page.getByText(/productos revisados/)).toBeVisible();
  const crear = page.getByRole("button", { name: "Crear lote en borrador" });
  if (await crear.isVisible()) {
    await crear.click();
    await expect(page.getByText(/Lote #\d+ · Nombre comercial/)).toBeVisible();
  }
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  const lotes = (await (await servidor.get("/api/store/content/batches", { headers: h })).json()) as { status: string }[];
  expect(lotes.every((l) => l.status !== "aplicado")).toBe(true); // nada se aplica solo
  const snap = (await (await servidor.get("/api/public/store/crimson/snapshot")).json()) as { products: { display_name?: string }[] };
  expect(snap.products.some((p) => p.display_name)).toBe(false); // la tienda sigue con los nombres de siempre
  for (const ancho of [768, 375, 320]) {
    await page.setViewportSize({ width: ancho, height: 900 });
    expect(await sinScrollDeLado(page), `${ancho} px`).toBeLessThanOrEqual(0);
  }
});
