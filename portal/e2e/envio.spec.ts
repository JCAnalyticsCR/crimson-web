/* O1-09: envio gratis, tarifas y plazo visibles antes del checkout, todo con lo que manda el API (shipping_public), y
   sin la palabra «retiro» mientras la tienda no tenga direccion de retiro. Montos ficticios, de 2 000 colones o menos. */
import type { AjustesE2e, ShipE2e } from "./app/api";
import { REF } from "./app/datos";
import { linea, rutaFicha } from "./soporte/pantallas";
import { expect, medirAncho, sembrarCarrito, test, tiendaLista } from "./soporte/tienda";
import type { Page } from "@playwright/test";

const ENVIO: ShipE2e = {
  free_from: 2000,
  rates: [
    { name: "Correos de prueba", zone: null, area: "a todo el país", amount: 395 },
    { name: "Mensajería de prueba", zone: "gam", area: "en la GAM", amount: 531, lead: "1 a 2 días hábiles" },
  ],
  pickup: null,
  invoice: true,
};
const ajustar = (page: Page, a: AjustesE2e) =>
  page.addInitScript((x: AjustesE2e) => { (window as Window & { __e2eAjustes?: AjustesE2e }).__e2eAjustes = x; }, a);
const nbsp = (s: string | null) => (s || "").replace(/\s/g, " ");

test("[O1-09] la franja dice desde cuánto el envío es gratis, sin moverse ni desbordar", async ({ page }, info) => {
  await ajustar(page, { envio: ENVIO });
  await page.goto("/");
  await tiendaLista(page);
  const franja = page.locator(".vt-strip");
  await expect(franja).toBeVisible();
  expect(nbsp(await franja.textContent())).toContain("Envío gratis desde ₡2 000");
  expect(await franja.evaluate((el) => getComputedStyle(el).position)).not.toBe("fixed");
  const ancho = info.project.use.viewport!.width;
  await expect(franja.locator(".vt-strip__m").first()).toBeVisible({ visible: ancho >= 768 }); // «Factura electrónica» desde 768 px
  const m = await medirAncho(page);
  expect(m.documento, "scroll horizontal").toBeLessThanOrEqual(m.ventana);
});

test("[O1-09] la ficha comprable muestra las tarifas completas con su zona y el envío gratis desde", async ({ page }) => {
  await ajustar(page, { envio: ENVIO });
  await page.goto(rutaFicha(REF.comprable));
  await tiendaLista(page);
  const envio = nbsp(await page.locator(".vt-ship").textContent());
  expect(envio).toContain("₡395 a todo el país (Correos de prueba)");
  expect(envio).toContain("₡531 en la GAM en 1 a 2 días hábiles (Mensajería de prueba)");
  expect(envio).toContain("gratis desde ₡2 000");
  expect(envio).toContain("El plazo de entrega te lo confirmamos al despachar");
  expect(envio).not.toMatch(/desde ₡395/); // nunca «desde» para la tarifa: el monto completo
  // la ficha por confirmar no promete envio (no se compra en linea)
  await page.goto(rutaFicha(REF.porConfirmar));
  await tiendaLista(page);
  await expect(page.locator(".vt-ship")).toHaveCount(0);
});

test("[O1-09] el carrito dice cuánto falta para el envío gratis y, al llegar, que ya lo tiene", async ({ page }) => {
  await ajustar(page, { envio: ENVIO });
  const l = linea(REF.comprable, 1);
  await sembrarCarrito(page, [l]);
  await page.goto("/");
  await tiendaLista(page);
  await page.locator(".sh-cartbtn").click();
  const aviso = page.locator(".vt-freeship");
  await expect(aviso).toHaveAttribute("role", "status");
  const falta = Math.round((2000 - l.precio) * 100) / 100;
  expect(nbsp(await aviso.textContent())).toBe(`Te faltan ${nbsp(new Intl.NumberFormat("es-CR", { style: "currency", currency: "CRC", currencyDisplay: "narrowSymbol", maximumFractionDigits: 0, minimumFractionDigits: 0 }).format(falta))} para el envío gratis`);
  await expect(page.locator(".sh-drawer__foot .sh-note")).not.toContainText("El envío se calcula al finalizar");
  await page.getByRole("button", { name: "Agregar uno" }).click();
  if (l.precio * 2 >= 2000) await expect(aviso).toHaveText("Ya tenés envío gratis");
});

test("[O1-09] sin dirección de retiro, la palabra «retiro» no aparece en portada, ficha ni envíos", async ({ page }) => {
  await ajustar(page, { envio: ENVIO });
  for (const ruta of ["/", rutaFicha(REF.comprable), "/info/envios"]) {
    await page.goto(ruta);
    await tiendaLista(page);
    await expect(page.locator("main")).not.toContainText(/retir/i);
  }
});
