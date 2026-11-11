/* Mi Tienda (panel) contra el API local de pruebas, a 768 px (y el scroll, tambien a 375): va con
   `npm run e2e:compra`, que levanta el API, el portal y crea un administrador de prueba con una contrasena al azar
   (playwright.compra.config.ts).
   - O1-11b: no se puede despublicar con un solo clic; escribir un titular y guardar un banner no pierde nada.
   - O4-01: cada seccion tiene su direccion (recargar deja en la misma), la bandera panel_v2 apagada vuelve al panel de
     una pagina y ninguna seccion tiene scroll de lado a 375 ni a 768 px. */
import { createHmac } from "node:crypto";
import type { APIRequestContext, Page, TestInfo } from "@playwright/test";
import { expect, test } from "./soporte/compra";

const ADMIN = "panel.e2e@example.com";
const CLAVE = process.env.E2E_PANEL_PASSWORD || "";
/* O2-01d: segundo administrador, con el 2FA activo (secreto TOTP al azar en cada corrida: playwright.compra.config.ts) */
const ADMIN_2FA = "panel.2fa.e2e@example.com";
const TOTP = process.env.E2E_PANEL_TOTP || "";

/** Codigo TOTP de 6 digitos (RFC 6238, 30 s, SHA-1), el mismo que muestra la app de autenticacion. */
function codigoTotp(secreto: string, paso = Math.floor(Date.now() / 30_000)): string {
  const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secreto.replace(/=+$/, "").toUpperCase()) bits += B32.indexOf(c).toString(2).padStart(5, "0");
  const llave = Buffer.from((bits.match(/.{8}/g) || []).map((x) => parseInt(x, 2)));
  const contador = Buffer.alloc(8);
  contador.writeBigUInt64BE(BigInt(paso));
  const h = createHmac("sha1", llave).update(contador).digest();
  const o = h[h.length - 1] & 0xf;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}
/* el panel no vive en tienda.localhost (ese nombre monta la tienda): mismo servidor, por 127.0.0.1 */
/* los rotulos del panel (Field) no van atados al campo: se busca el campo por el rotulo de su caja */
const campo = (page: Page, rotulo: string, dentro = "") =>
  page.locator(`${dentro} .field`.trim()).filter({ has: page.locator(`label:text-is("${rotulo}")`) }).locator("input, textarea");
const panel = (baseURL: string | undefined) => (baseURL || "").replace(/\/\/([a-z0-9-]+\.)*localhost/, "//127.0.0.1");
const solo768 = (info: TestInfo) => test.skip(info.project.name !== "compra-w768", "criterio del panel: 768 px");

/* dentro de la aplicacion (el token vive en memoria: un goto cargaria la pagina de nuevo) */
async function ir(page: Page, ruta: string) {
  await page.evaluate((r) => { history.pushState({}, "", r); dispatchEvent(new PopStateEvent("popstate")); }, ruta);
}

async function login(page: Page) {
  await page.locator('input[type="email"]').fill(ADMIN);
  await page.locator('input[autocomplete="current-password"]').fill(CLAVE);
  await page.locator("button.lg-go").click();
  const hola = page.locator(".welcome");  // la bienvenida del primer ingreso
  if (await hola.isVisible().catch(() => false) || await hola.waitFor({ timeout: 3000 }).then(() => true, () => false)) {
    await hola.getByRole("button", { name: /Empezar/ }).click();
    await expect(hola).toBeHidden();
  }
}

async function entrar(page: Page, base: string, ruta = "/mi-tienda") {
  await page.goto(`${base}/login`);
  await login(page);
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));
  await ir(page, ruta);
  await expect(page.locator(".sh-hub")).toBeVisible();
}

async function token(servidor: APIRequestContext): Promise<string> {
  const r = await servidor.post("/api/auth/login", { data: { email: ADMIN, password: CLAVE } });
  expect(r.ok(), await r.text()).toBeTruthy();
  return ((await r.json()) as { access_token: string }).access_token;
}

const sinScrollDeLado = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test.beforeEach(() => {
  test.skip(!CLAVE, "sin administrador de prueba (API ya levantado aparte): ver playwright.compra.config.ts");
});

test("[O1-11b] despublicar pide confirmación con el efecto escrito", async ({ page, baseURL, servidor }, info) => {
  solo768(info);
  await entrar(page, panel(baseURL), "/mi-tienda/publicacion");
  await page.getByRole("button", { name: "Despublicar" }).click();
  const ventana = page.locator(".modal, [role=dialog]").filter({ hasText: "¿Despublicar la tienda?" });
  await expect(ventana).toBeVisible();
  await expect(ventana).toContainText("La tienda dejará de verse para sus clientes");
  await ventana.getByRole("button", { name: "Cancelar" }).click();
  await expect(ventana).toBeHidden();
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  expect(((await (await servidor.get("/api/store", { headers: h })).json()) as { published: boolean }).published).toBe(true);
});

test("[O1-11b] escribir un titular y luego guardar un banner no pierde nada", async ({ page, baseURL, servidor }, info) => {
  solo768(info);
  await entrar(page, panel(baseURL), "/mi-tienda/portada");
  const titular = `Titular de prueba ${Date.now() % 100000}`;
  await campo(page, "Titular de la portada").fill(titular);
  // sin salir del titular: el clic en «+ Banner» lo guarda (al perder el foco) y a la vez agrega el banner
  await page.getByRole("button", { name: "Banner", exact: true }).click();
  await expect(page.getByText("Vitrina actualizada").first()).toBeVisible();
  const nuevo = campo(page, "Título", '[data-editor="banners"]').last();
  await nuevo.fill("Promo de prueba");
  await expect(page.getByText("Este banner no se muestra hasta que tenga título e imagen.").last()).toBeVisible();
  const guardado = page.waitForResponse((r) => r.url().endsWith("/store/showcase") && r.request().method() === "PUT");
  await page.getByRole("button", { name: "Guardar banners" }).click();
  expect((await guardado).ok()).toBeTruthy();  // nace inactivo (O1-04): se guarda sin aviso y la tienda no lo pinta
  await expect(campo(page, "Título", '[data-editor="banners"]').last()).toHaveValue("Promo de prueba");
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  const sc = (await (await servidor.get("/api/store/showcase", { headers: h })).json()) as { headline: string; banners: { title: string; active: boolean }[] };
  expect(sc.headline).toBe(titular);
  expect(sc.banners.map((b) => [b.title, b.active])).toContainEqual(["Promo de prueba", false]);
  expect(await sinScrollDeLado(page)).toBeLessThanOrEqual(0);
});

test("[O4-01] cada sección tiene su dirección y recargar deja en la misma", async ({ page, baseURL }, info) => {
  solo768(info);
  await entrar(page, panel(baseURL));
  const menu = page.getByRole("navigation", { name: "Secciones de Mi Tienda" });
  await menu.getByRole("link", { name: "Pagos y envíos" }).click();
  await expect(page).toHaveURL(/\/mi-tienda\/pagos-envios$/);
  await expect(page.getByRole("heading", { level: 1, name: "Pagos y envíos" })).toBeVisible();
  await menu.getByRole("link", { name: "Páginas" }).click();
  await expect(page).toHaveURL(/\/mi-tienda\/paginas$/);
  await page.reload();
  // el token vive en memoria: si la recarga pide entrar de nuevo, la direccion no cambia y vuelve a la misma seccion
  await page.locator('.sh-hub, input[type="email"]').first().waitFor();
  if (await page.locator('input[type="email"]').isVisible()) await login(page);
  await expect(page).toHaveURL(/\/mi-tienda\/paginas$/);
  await expect(page.getByRole("heading", { level: 1, name: "Páginas" })).toBeVisible();
});

test("[O4-01] con la bandera apagada el panel es el de una sola página", async ({ page, baseURL, servidor }, info) => {
  solo768(info);
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  expect((await servidor.put("/api/store", { headers: h, data: { panel_v2: false } })).ok()).toBeTruthy();
  try {
    await page.goto(`${panel(baseURL)}/login`);
    await login(page);
    await page.waitForURL((u) => !u.pathname.startsWith("/login"));
    await ir(page, "/mi-tienda");
    await expect(page.getByText("Configura el sitio a tu gusto")).toBeVisible();
    await expect(page.locator(".sh-hub")).toHaveCount(0);
    await page.getByRole("button", { name: "Usar Mi Tienda por secciones" }).click();
    await expect(page.locator(".sh-hub")).toBeVisible();
  } finally {
    await servidor.put("/api/store", { headers: h, data: { panel_v2: true } });
  }
});

test("[O4-01] ninguna sección tiene scroll de lado", async ({ page, baseURL }) => {
  await entrar(page, panel(baseURL));
  for (const ruta of ["/mi-tienda", "/mi-tienda/apariencia", "/mi-tienda/portada", "/mi-tienda/kits", "/mi-tienda/paginas", "/mi-tienda/catalogo", "/mi-tienda/categorias", "/mi-tienda/pagos-envios", "/mi-tienda/seo", "/mi-tienda/publicacion"]) {
    await ir(page, ruta);
    await expect(page.locator(".sh-hub__body")).toBeVisible();
    await page.waitForLoadState("networkidle");
    expect(await sinScrollDeLado(page), ruta).toBeLessThanOrEqual(0);
  }
  // a 375 px el menu es un selector; a 768, la fila de fichas
  const w = page.viewportSize()?.width || 0;
  if (w <= 480) await expect(page.getByLabel("Sección de Mi Tienda")).toBeVisible();
  else await expect(page.getByRole("navigation", { name: "Secciones de Mi Tienda" })).toBeVisible();
});

test("[O4-03] el resumen dice qué falta y lleva a corregirlo", async ({ page, baseURL, servidor }, info) => {
  solo768(info);
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  const salud = (await (await servidor.get("/api/store/health", { headers: h })).json()) as { indicadores: { publicados: number; visibles: number }; avance: { listos: number; total: number }; items: { ok: boolean; to: string; texto: string }[] };
  const inv = (await (await servidor.get("/api/store/inventory", { headers: h })).json()) as { summary: { publicados: number } };
  expect(salud.indicadores.publicados).toBe(inv.summary.publicados);
  await entrar(page, panel(baseURL));
  await expect(page.getByRole("list", { name: "Indicadores de la tienda" })).toContainText("Visibles en la tienda");
  await expect(page.getByText(`${salud.avance.listos} de ${salud.avance.total} listos`)).toBeVisible();
  const falta = salud.items.find((x) => !x.ok);
  if (falta) {
    const fila = page.locator(".rs-item").filter({ hasText: falta.texto });
    await fila.getByRole("link", { name: "Corregir" }).click();
    await expect(page).toHaveURL((u) => u.pathname + u.search === falta.to);
  }
  expect(await sinScrollDeLado(page)).toBeLessThanOrEqual(0);
});

/* O2-01d: el panel ya no viene en la entrada (src/app/PanelApp.tsx, con lazy()). Su arranque completo, con segundo
   factor: correo y contrasena, el paso «Verificación en dos pasos», el codigo de la app y adentro del panel. */
test("[O2-01d] inicio de sesión del panel con segundo factor (2FA)", async ({ page, baseURL }) => {
  test.skip(!TOTP, "sin administrador con 2FA (API ya levantado aparte)");
  const base = panel(baseURL);
  await page.goto(`${base}/login`);
  await page.locator('input[type="email"]').fill(ADMIN_2FA);
  await page.locator('input[autocomplete="current-password"]').fill(CLAVE);
  await page.locator("button.lg-go").click();
  await expect(page.getByRole("heading", { name: "Verificación en dos pasos" })).toBeVisible();
  // un codigo de una ventana de 30 s que no sea la que esta por vencer (el API acepta un paso a cada lado)
  if (30 - (Math.floor(Date.now() / 1000) % 30) < 3) await page.waitForTimeout(3500);
  await page.locator('input[autocomplete="one-time-code"]').fill(codigoTotp(TOTP));
  await page.locator("button.lg-go").click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));
  const hola = page.locator(".welcome");
  if (await hola.waitFor({ timeout: 3000 }).then(() => true, () => false)) await hola.getByRole("button", { name: /Empezar/ }).click();
  // el cascaron del panel (menu lateral con sus secciones) esta montado
  await expect(page.getByRole("complementary").getByRole("link", { name: "Inicio" })).toBeAttached();
  await expect(page.getByRole("heading", { name: "Verificación en dos pasos" })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
});
