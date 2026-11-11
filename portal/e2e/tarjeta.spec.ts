/* Tarjeta de producto (O2-06): seis elementos y cero adornos, dos columnas en celular sin recortes, el dato clave que
   distingue a cada camara, el nombre una sola vez para el lector y «Comparar» alcanzable con teclado en escritorio. */
import { CATEGORIA } from "./soporte/pantallas";
import { expect, medirAncho, test, tiendaLista } from "./soporte/tienda";

const CAMARAS = "/c/camaras-ip";

test("[O2-06] la tarjeta tiene seis elementos o menos y el lector oye el nombre una sola vez", async ({ page }) => {
  await page.goto(CAMARAS);
  await tiendaLista(page);
  const tarjeta = page.locator(".vt-grid .vt-card").first();
  // un solo enlace para foto y nombre, foto sin texto alternativo, sin rotulo de visor
  await expect(tarjeta.locator("a.vt-card__link")).toHaveCount(1);
  await expect(tarjeta.locator(".vt-card__link img")).toHaveAttribute("alt", "");
  await expect(tarjeta.locator(".vt-vf__hud, .vt-vf__corners")).toHaveCount(0);
  const nombre = (await tarjeta.locator(".vt-card__name").textContent())!.trim();
  const accesible = await tarjeta.locator(".vt-card__link").evaluate((el) => (el.textContent || "").replace(/\s+/g, " ").trim());
  expect(accesible.split(nombre).length - 1).toBe(1);
  // hijos visibles de la tarjeta: enlace (foto, marca, nombre), datos clave, precio y accion, comparar
  const visibles = await tarjeta.evaluate((el) => [...el.children].filter((c) => getComputedStyle(c).display !== "none").length);
  expect(visibles).toBeLessThanOrEqual(4);
});

test("[O2-06] a 320 y 375 px dos columnas sin recortes, y cada cámara dice su dato clave", async ({ page }) => {
  const ancho = page.viewportSize()!.width;
  test.skip(ancho > 375, "criterio de celular");
  await page.goto(CAMARAS);
  await tiendaLista(page);
  const tarjetas = page.locator(".vt-grid .vt-card");
  const [a, b] = [await tarjetas.nth(0).boundingBox(), await tarjetas.nth(1).boundingBox()];
  expect(Math.abs(a!.y - b!.y)).toBeLessThan(2); // misma fila
  expect(b!.x).toBeGreaterThan(a!.x + a!.width - 1);
  const m = await medirAncho(page);
  expect(m.documento).toBeLessThanOrEqual(m.ventana);
  for (let i = 0; i < 3; i++) await expect(tarjetas.nth(i).locator(".vt-card__ks span").first()).toHaveText(/\d MP/);
  await expect(tarjetas.nth(0).locator(".vt-card__ks-more").first()).toBeHidden(); // un solo dato en celular
});

test("[O2-06] al pasar el puntero nada se mueve y «Comparar» se alcanza con el teclado en escritorio", async ({ page }) => {
  test.skip(page.viewportSize()!.width < 1024, "comparar en la tarjeta es de escritorio");
  await page.goto(CATEGORIA.ruta);
  await tiendaLista(page);
  const tarjeta = page.locator(".vt-grid .vt-card").first();
  const antes = await tarjeta.boundingBox();
  await tarjeta.hover();
  expect(await tarjeta.boundingBox()).toEqual(antes);
  expect(await tarjeta.evaluate((el) => getComputedStyle(el).transform)).toBe("none");
  const casilla = tarjeta.getByRole("checkbox", { name: "Comparar" });
  await page.mouse.move(0, 0);
  await casilla.focus();
  await expect(casilla).toBeFocused();
  expect(Number(await tarjeta.locator(".vt-cmp").evaluate((el) => getComputedStyle(el).opacity))).toBe(1);
});

test("[O2-06] el «+» dice qué agrega", async ({ page }) => {
  await page.goto(CATEGORIA.ruta);
  await tiendaLista(page);
  await expect(page.getByRole("button", { name: /^Agregar .+ al carrito$/ }).first()).toBeVisible();
});
