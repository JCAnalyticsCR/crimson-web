/* Portada honesta (O1-04): sin «Lo más pedido» ni «Cam · en vivo», nada parpadea fuera del hero, destacados con su
   origen y la carrera snapshot / /showcase que dejaba la portada sin kits. */
import type { AjustesE2e } from "./app/api";
import { expect, test, tiendaLista } from "./soporte/tienda";

const fijar = (page: import("@playwright/test").Page, a: AjustesE2e) =>
  page.addInitScript((x: AjustesE2e) => { (window as Window & { __e2eAjustes?: AjustesE2e }).__e2eAjustes = x; }, a);

test("[O1-04 j] 10 recargas seguidas: la sección de kits siempre está", async ({ page }, info) => {
  test.skip(info.project.name !== "w1280", "la carrera no depende del ancho: un solo ancho");
  // /showcase llega entre 0 y 12 ms despues del snapshot: justo la ventana en que el snapshot pisaba la portada
  await fijar(page, { portadaTras: -1 });
  await page.goto("/");
  for (let i = 0; i < 10; i++) {
    if (i) await page.reload();
    await tiendaLista(page);
    await expect(page.locator(".vt-kits .vt-kit:not(.vt-kit--skel)").first(), `recarga ${i + 1}`).toBeVisible();
    await page.waitForTimeout(150); // si el snapshot llegara a pisarla, seria despues de pintar los kits
    await expect(page.locator(".vt-kits .vt-kit:not(.vt-kit--skel)").first(), `recarga ${i + 1}`).toBeVisible();
  }
});

test("[O1-04] sin relleno: ni «Lo más pedido», ni «Cam · en vivo», ni «Marcas que instalamos»; nada parpadea fuera del hero", async ({ page }) => {
  await page.goto("/");
  await tiendaLista(page);
  const main = page.locator("main");
  for (const t of ["Lo más pedido", "Cam · en vivo", "Marcas que instalamos", "Nuevo banner", "Arma tu sistema"]) await expect(main).not.toContainText(t);
  await expect(page.locator("#vt-feat")).toHaveText("Del catálogo"); // la lista de la tienda de pruebas es automatica
  await expect(page.locator("#vt-new")).toHaveText("Recién agregados al catálogo");
  await expect(page.locator("#vt-brands")).toHaveText("Marcas");
  await expect(page.locator(".vt-kit__terms").first()).toContainText("Solo equipo · la instalación se cotiza aparte");
  const parpadean = await page.evaluate(() => [...document.querySelectorAll("main *")].filter((el) => !el.closest(".vt-hero") && getComputedStyle(el).animationName !== "none" && getComputedStyle(el).animationIterationCount === "infinite").map((el) => el.className));
  expect(parpadean).toEqual([]);
});
