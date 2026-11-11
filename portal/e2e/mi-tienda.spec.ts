/* Mi Tienda (panel) contra el API local de pruebas (O1-11b), a 768 px: va con `npm run e2e:compra`, que levanta el
   API, el portal y crea un administrador de prueba con una contrasena al azar (playwright.compra.config.ts).
   - No se puede despublicar con un solo clic: primero una ventana con el efecto escrito.
   - Escribir un titular y luego guardar un banner no pierde nada (antes, el guardado del titular reiniciaba el
     editor de banners y lo escrito ahi se perdia). */
import { createHmac } from "node:crypto";
import type { APIRequestContext, Page } from "@playwright/test";
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

async function entrar(page: Page, base: string) {
  await page.goto(`${base}/login`);
  await page.locator('input[type="email"]').fill(ADMIN);
  await page.locator('input[autocomplete="current-password"]').fill(CLAVE);
  await page.locator("button.lg-go").click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));
  const hola = page.locator(".welcome");  // la bienvenida del primer ingreso
  if (await hola.isVisible().catch(() => false) || await hola.waitFor({ timeout: 3000 }).then(() => true, () => false)) {
    await hola.getByRole("button", { name: /Empezar/ }).click();
    await expect(hola).toBeHidden();
  }
  // dentro de la aplicacion (el token vive en memoria: un goto cargaria la pagina de nuevo y volveria al login)
  await page.evaluate(() => { history.pushState({}, "", "/mi-tienda"); dispatchEvent(new PopStateEvent("popstate")); });
  await expect(page.getByText("Vitrina de la tienda")).toBeVisible();
}

async function token(servidor: APIRequestContext): Promise<string> {
  const r = await servidor.post("/api/auth/login", { data: { email: ADMIN, password: CLAVE } });
  expect(r.ok(), await r.text()).toBeTruthy();
  return ((await r.json()) as { access_token: string }).access_token;
}

test.beforeEach(({}, info) => {
  test.skip(!CLAVE, "sin administrador de prueba (API ya levantado aparte): ver playwright.compra.config.ts");
  test.skip(info.project.name !== "compra-w768", "criterio del panel: 768 px");
});

test("[O1-11b] despublicar pide confirmación con el efecto escrito", async ({ page, baseURL, servidor }) => {
  const base = panel(baseURL);
  await entrar(page, base);
  await page.getByRole("button", { name: "Despublicar" }).click();
  const ventana = page.locator(".modal, [role=dialog]").filter({ hasText: "¿Despublicar la tienda?" });
  await expect(ventana).toBeVisible();
  await expect(ventana).toContainText("La tienda dejará de verse para sus clientes");
  await ventana.getByRole("button", { name: "Cancelar" }).click();
  await expect(ventana).toBeHidden();
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  expect(((await (await servidor.get("/api/store", { headers: h })).json()) as { published: boolean }).published).toBe(true);
});

test("[O1-11b] escribir un titular y luego guardar un banner no pierde nada", async ({ page, baseURL, servidor }) => {
  const base = panel(baseURL);
  await entrar(page, base);
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
  // sin scroll de lado a 768 px
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
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
