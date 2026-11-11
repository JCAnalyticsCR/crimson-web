/* Prueba de humo (O1-18): la portada pinta en todos los navegadores declarados. En los proyectos de Chromium corre
   como una mas; el proyecto "webkit-375" de playwright.config.ts corre solo este archivo (Safari de iPhone). */
import { expect, test, tiendaLista } from "./soporte/tienda";

test("humo: la portada pinta con productos", async ({ page }) => {
  const errores: string[] = [];
  page.on("pageerror", (e) => errores.push(e.message));
  await page.goto("/");
  await tiendaLista(page);
  await expect(page.locator(".vt-card").first()).toBeVisible();
  expect(errores, "errores de JavaScript en la portada").toEqual([]);
});
