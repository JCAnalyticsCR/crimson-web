/* Sistema visual de la Ola 2 (O2-03): la regla del rojo, objetivos tactiles de 44 px y el foco atrapado en el cajon
   del carrito y en la hoja de filtros. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { CARRITO, CATEGORIA } from "./soporte/pantallas";
import { expect, sembrarCarrito, test, tiendaLista } from "./soporte/tienda";
import type { Locator, Page } from "@playwright/test";

/* Playwright corre desde portal/ (la carpeta de playwright.config.ts) */
const leerCss = () => ["shop.css", "vitrina.css"].map((f) => readFileSync(resolve("src/modules/store", f), "utf8"));
/** usos del carmesi fuera de la declaracion de los tokens (bloque .shop { --... }) */
const usosDeRojo = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "").split("\n")
  .filter((l) => !/^\s*--/.test(l))
  .reduce((n, l) => n + (l.match(/var\(--crimson[a-z-]*\)|var\(--rose\)|#e2233a|#ff8a98|#ff6b7d|rgba\(226, 35, 58/g)?.length ?? 0), 0);

test("[O2-03] el carmesí es escaso: 15 usos o menos en shop.css y vitrina.css", async ({}, info) => {
  test.skip(info.project.name !== "w1280", "lee los archivos: un solo ancho");
  const n = leerCss().reduce((a, c) => a + usosDeRojo(c), 0);
  expect(n).toBeLessThanOrEqual(15);
});

async function alto(loc: Locator, nombre: string) {
  const n = await loc.count();
  expect(n, `${nombre}: no hay ninguno en la página`).toBeGreaterThan(0);
  for (let i = 0; i < n; i++) {
    const el = loc.nth(i);
    if (!(await el.isVisible())) continue;
    const box = (await el.boundingBox())!;
    expect(box.height, `${nombre} #${i + 1} mide ${box.height} px de alto`).toBeGreaterThanOrEqual(44);
    expect(box.width, `${nombre} #${i + 1} mide ${box.width} px de ancho`).toBeGreaterThanOrEqual(44);
  }
}

test("[O2-03] a 375 px todo lo que se toca mide 44 px o más", async ({ page }, info) => {
  test.skip(info.project.name !== "w375", "criterio a 375 px");
  await sembrarCarrito(page, CARRITO);
  await page.goto(`${CATEGORIA.ruta}?marca=Vig%C3%ADa`);
  await tiendaLista(page);
  await alto(page.locator(".vt-catnav .sh-chip"), "chip de categoría");
  await alto(page.locator(".vt-crumbs a"), "miga");
  await alto(page.locator(".vt-active .vt-tag"), "etiqueta de filtro");
  await alto(page.locator(".vt-active .vt-clear"), "«Limpiar todo»");
  await alto(page.locator(".sh-foot a, .sh-foot button"), "enlace del pie");
  await page.getByRole("button", { name: /^Carrito:/ }).click();
  const cajon = page.locator(".sh-drawer");
  await expect(cajon).toHaveAttribute("data-open", "true");
  await alto(cajon.locator(".sh-qty button"), "botón de cantidad");
  await alto(cajon.locator(".sh-rm"), "«Quitar»");
});

async function tabsDentro(page: Page, caja: Locator, veces: number) {
  for (let i = 0; i < veces; i++) {
    await page.keyboard.press(i % 3 === 2 ? "Shift+Tab" : "Tab");
    expect(await caja.evaluate((el) => el.contains(document.activeElement)), `Tab ${i + 1}: el foco salió`).toBe(true);
  }
}

test("[O2-03] el cajón del carrito atrapa el foco y lo devuelve al cerrarse", async ({ page }) => {
  await sembrarCarrito(page, CARRITO);
  await page.goto(CATEGORIA.ruta);
  await tiendaLista(page);
  const boton = page.getByRole("button", { name: /^Carrito:/ });
  await boton.focus();
  await page.keyboard.press("Enter");
  const cajon = page.locator(".sh-drawer");
  await expect(cajon).toHaveAttribute("data-open", "true");
  await expect.poll(() => cajon.evaluate((el) => el.contains(document.activeElement))).toBe(true);
  await tabsDentro(page, cajon, 24);
  await page.keyboard.press("Escape");
  await expect(cajon).not.toHaveAttribute("data-open", "true");
  await expect(boton).toBeFocused();
});

test("[O2-03] la hoja de filtros atrapa el foco y lo devuelve al cerrarse", async ({ page }) => {
  test.skip(page.viewportSize()!.width >= 1024, "la hoja es de celular y tableta");
  await page.goto(CATEGORIA.ruta);
  await tiendaLista(page);
  const filtros = page.getByRole("button", { name: /^Filtros/ });
  await filtros.focus();
  await page.keyboard.press("Enter");
  const hoja = page.locator(".vt-sheet");
  await expect(hoja).toHaveAttribute("data-open", "true");
  await expect.poll(() => hoja.evaluate((el) => el.contains(document.activeElement))).toBe(true);
  await tabsDentro(page, hoja, 30);
  await page.keyboard.press("Escape");
  await expect(filtros).toBeFocused();
});
