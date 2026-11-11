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

test("[O2-08] a 375x812 caben en la primera pantalla el titular, el botón y el producto con su precio", async ({ page }) => {
  test.skip(page.viewportSize()!.width !== 375, "criterio a 375 px");
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await tiendaLista(page);
  const hero = page.locator(".vt-hero");
  for (const el of [hero.getByRole("heading", { level: 1 }), hero.getByRole("link", { name: "Armá tu sistema" }), hero.locator(".vt-hero__prod .vt-price")]) {
    const b = (await el.boundingBox())!;
    expect(b.y + b.height).toBeLessThanOrEqual(812);
  }
  // un solo llamado rojo en el hero y la imagen del producto es la LCP (prioridad alta, sin lazy, con medidas)
  await expect(hero.locator(".sh-btn--crimson")).toHaveCount(1);
  const img = hero.locator(".vt-hero__prod img");
  await expect(img).toHaveAttribute("fetchpriority", "high");
  await expect(img).toHaveAttribute("loading", "eager");
  await expect(img).toHaveAttribute("width", /\d+/);
});

test("[O2-08] sin producto comprable la portada va sin producto ni precio, y un solo botón rojo en toda la página", async ({ page }) => {
  await fijar(page, { sinHeroe: true });
  await page.goto("/");
  await tiendaLista(page);
  await expect(page.locator(".vt-kits .vt-kit:not(.vt-kit--skel)").first()).toBeVisible();
  await expect(page.locator(".vt-hero__prod")).toHaveCount(0);
  await expect(page.locator(".vt-hero .vt-price")).toHaveCount(0);
  await expect(page.locator("main .sh-btn--crimson")).toHaveCount(1);
});

test("[O2-08] con «reducir movimiento» nada se anima en la portada", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await tiendaLista(page);
  const animadas = await page.evaluate(() => [...document.querySelectorAll("main *")].filter((el) => getComputedStyle(el).animationName !== "none" && parseFloat(getComputedStyle(el).animationDuration) > 0.01).length);
  expect(animadas).toBe(0);
});
