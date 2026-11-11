/* Cabecera compacta, navegacion por prioridad y pie con identidad (O2-05). */
import { expect, test, tiendaLista } from "./soporte/tienda";

test("[O2-05] en celular el bloque fijo mide 60 px o menos y la lupa abre el buscador", async ({ page }) => {
  test.skip(page.viewportSize()!.width >= 1024, "celular y tableta");
  await page.goto("/");
  await tiendaLista(page);
  const cab = page.locator(".shop .sh-head");
  expect((await cab.boundingBox())!.height).toBeLessThanOrEqual(60);
  await expect(cab.getByRole("searchbox")).toHaveCount(0);
  await cab.getByRole("button", { name: "Buscar productos" }).click();
  await expect(cab.getByRole("searchbox", { name: "Buscar productos" })).toBeFocused();
  await expect(cab.getByRole("link", { name: /WhatsApp/ })).toHaveCount(1);
});

test("[O2-05] Seguridad es la primera categoría en la cabecera y está en el pie", async ({ page }) => {
  await page.goto("/");
  await tiendaLista(page);
  const chips = page.getByRole("navigation", { name: "Categorías" }).getByRole("link");
  await expect(chips.nth(0)).toHaveText("Armá tu sistema");
  await expect(chips.nth(1)).toHaveText("Seguridad electrónica");
  await expect(page.locator(".sh-foot").getByRole("link", { name: "Seguridad electrónica" })).toHaveCount(1);
  await expect(page.locator(".sh-foot")).not.toContainText("Garantía ·"); // sin sellos sin dato
});

test("[O2-05] a 1025 px el buscador se lee completo y el pie va en 4 columnas", async ({ page }) => {
  test.skip(page.viewportSize()!.width < 1024, "escritorio");
  await page.setViewportSize({ width: 1025, height: 800 });
  await page.goto("/");
  await tiendaLista(page);
  const input = page.locator(".sh-head .sh-search input");
  const cabe = await input.evaluate((el: HTMLInputElement) => {
    const cs = getComputedStyle(el);
    const ctx = document.createElement("canvas").getContext("2d")!;
    ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    const libre = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    return libre - ctx.measureText(el.placeholder).width;
  });
  expect(cabe).toBeGreaterThanOrEqual(0);
  await expect(page.locator(".sh-head").getByRole("button", { name: "Mi pedido" })).toBeVisible();
  const tops = await page.locator(".sh-foot__grid > .vt-foot__col").evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top)));
  expect(tops).toHaveLength(4);
  expect(new Set(tops).size).toBe(1);
});
