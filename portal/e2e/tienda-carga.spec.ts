/* O1-18: la tienda no se queda en blanco si falla la primera llamada (/store-domain). Un reintento automatico a los
   2 s y, si vuelve a fallar, «No pudimos cargar la tienda.» con «Reintentar» (con el foco). Perillas: e2e/app/api.ts. */
import type { AjustesE2e } from "./app/api";
import { expect, test, tiendaLista } from "./soporte/tienda";

type Ventana = Window & { __e2eAjustes?: AjustesE2e };
const fijar = (page: import("@playwright/test").Page, a: AjustesE2e) =>
  page.addInitScript((x: AjustesE2e) => { (window as Ventana).__e2eAjustes = x; }, a);

test("[O1-18] /store-domain falla una vez: la tienda carga sola", async ({ page }) => {
  await fijar(page, { fallarDominio: 1 });
  await page.goto("/");
  await tiendaLista(page);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.locator(".vt-card").first()).toBeVisible();
});

test("[O1-18] /store-domain falla siempre: «Reintentar» con el foco, y funciona al restablecerse", async ({ page }) => {
  await fijar(page, { fallarDominio: 1000 });
  await page.goto("/");
  const aviso = page.getByRole("alert");
  await expect(aviso).toContainText("No pudimos cargar la tienda.", { timeout: 10_000 });
  const btn = aviso.getByRole("button", { name: "Reintentar" });
  await expect(btn).toBeFocused();
  await page.evaluate(() => { (window as Ventana).__e2eAjustes!.fallarDominio = 0; });
  await page.keyboard.press("Enter");
  await tiendaLista(page);
  await expect(page.locator(".vt-card").first()).toBeVisible();
});
