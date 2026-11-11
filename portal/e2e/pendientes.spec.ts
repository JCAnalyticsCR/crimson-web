/* Fallos conocidos de la tienda, escritos como la prueba que deberia pasar y marcados test.fixme con la tarea del plan
   que los resuelve (entre corchetes al inicio del nombre). Quien haga la tarea cambia `test.fixme` por `test`: si la
   prueba pasa, el fallo quedo resuelto; si el criterio de la tarea cambio, ajusta la prueba en ese mismo cambio.
   Cada una se verifico en rojo el 10/11 antes de marcarla. La lista completa sale en e2e/informes/pendientes.md.
   (Los de contenido cortado por el borde estan en scroll.spec.ts, en CONOCIDOS.) */
import { REF } from "./app/datos";
import { PANTALLAS, rutaFicha } from "./soporte/pantallas";
import { expect, test, tiendaLista } from "./soporte/tienda";

const abrir = (id: string) => PANTALLAS.find((p) => p.id === id)!.abrir;
const px = (s: string) => Number.parseFloat(s);

test("[O1-02] ficha por confirmar: ningún botón apagado y la barra fija nunca dice «Sin existencias»", async ({ page }) => {
  // antes: «Consultar disponibilidad» deshabilitado en la zona de compra y, en celular, barra fija con «Sin existencias»
  await abrir("ficha-por-confirmar")(page);
  await expect(page.locator(".vt-buybox button:disabled")).toHaveCount(0);
  await expect(page.locator(".vt-stickybuy")).not.toContainText("Sin existencias");
  await expect(page.locator(".vt-stickybuy button:disabled")).toHaveCount(0);
});

test("[O1-03] ficha en celular: precio de 30 px o más e «IVA incluido» de 14 px o más", async ({ page }, info) => {
  // antes, a 375 px: precio 16,5 px e «IVA incluido» 11 px (una regla movil de vitrina.css le gana al precio grande)
  test.skip(info.project.use.viewport!.width > 375, "criterio de celular: 320, 360 y 375 px");
  await abrir("ficha-comprable")(page);
  const precio = page.locator(".vt-info__price .vt-price");
  expect(px(await precio.locator("b").evaluate((el) => getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(30);
  expect(px(await precio.locator("small").evaluate((el) => getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(14);
});

test("[O1-08] la tarjeta de una barra de sonido no dice «Cámara»", async ({ page }) => {
  // antes: «Resonar · Cámara WiFi» (la palabra «inalámbrico» del nombre la clasificaba como cámara)
  await page.goto("/c/audio");
  await tiendaLista(page);
  const tarjeta = page.locator(".vt-card", { hasText: "Barra de sonido" });
  await expect(tarjeta).toHaveCount(1);
  await expect(tarjeta.locator(".vt-card__meta")).not.toContainText(/c[aá]mara/i);
});

test("[O1-14] buscar «camaras» encuentra las cámaras", async ({ page }) => {
  // hoy el plural no encuentra el singular: solo sale el producto que dice «cámaras» en el nombre
  await page.goto("/buscar?q=camaras");
  await tiendaLista(page);
  await expect(page.locator(".vt-grid .vt-card", { hasText: /C[aá]mara (bullet|domo)/i }).first()).toBeVisible();
});

test("[O1-14] una categoría que no existe lo dice, en vez de listar todo el catálogo", async ({ page }) => {
  // hoy depende de una carrera: si el snapshot llega antes que /catalog lista todo; si llega despues, sale el aviso
  await page.goto("/c/no-existe");
  await tiendaLista(page);
  await expect(page.locator("main")).toContainText("Esta categoría ya no está disponible");
  await expect(page.locator(".vt-grid .vt-card")).toHaveCount(0);
});

test("[O1-14] «Ver más» queda en la dirección y volver de una ficha conserva el lugar", async ({ page }) => {
  await page.goto("/catalogo");
  await tiendaLista(page);
  const tarjetas = page.locator(".vt-grid .vt-card:not(.vt-card--skel)");
  await expect(tarjetas).toHaveCount(24);
  await page.locator(".vt-pager a").click(); // O2-01c: enlace real a ?pagina=2
  await expect(page).toHaveURL(/[?&]pagina=2/);
  const n = await tarjetas.count();
  expect(n).toBeGreaterThan(24);
  await tarjetas.nth(n - 1).locator(".vt-card__name").click();
  await expect(page.locator(".vt-buybox")).toBeVisible();
  await page.goBack();
  await tiendaLista(page);
  await expect(page).toHaveURL(/[?&]pagina=2/);
  await expect(tarjetas).toHaveCount(n);
});

// una prueba viva en el archivo: la ficha «por confirmar» del catalogo de pruebas esta en el estado que las de arriba suponen
test("la ficha por confirmar del catálogo de pruebas está en «consultar»", async ({ page }) => {
  await page.goto(rutaFicha(REF.porConfirmar));
  await tiendaLista(page);
  await expect(page.locator(".vt-info__price .vt-avail")).toHaveAttribute("data-s", "consultar");
});

test("[O2-01c] «Ver 24 más» es un enlace real a ?pagina=2 y la página 2 tiene su canónica y su título (catálogo)", async ({ page }) => {
  await page.goto("/catalogo?orden=price_asc");
  await tiendaLista(page);
  const tarjetas = page.locator(".vt-grid .vt-card:not(.vt-card--skel)");
  const total = Number((await page.locator(".vt-bar .sh-meta").first().textContent())?.match(/\d+/)?.[0] || 0);
  expect(total, "el catálogo de pruebas tiene más de una página").toBeGreaterThan(24);
  const mas = page.locator(".vt-pager a");
  await expect(mas).toHaveAttribute("href", /\/catalogo\?(.*&)?pagina=2/);
  await mas.click();
  await expect(page).toHaveURL(/[?&]pagina=2/);
  await expect(page).toHaveURL(/orden=price_asc/); // los filtros siguen
  await expect(tarjetas).toHaveCount(Math.min(48, total));
  // entrar directo a la página 2: canónica propia (sin los demás parámetros) y título con la página
  await page.goto("/catalogo?pagina=2&utm_source=x");
  await tiendaLista(page);
  await expect(tarjetas).toHaveCount(Math.min(48, total));
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute("href", /\/catalogo\?pagina=2$/);
  await expect(page).toHaveTitle(/· Página 2$/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
});
