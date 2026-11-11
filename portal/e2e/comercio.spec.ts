/* O1-17: quien vende y a nombre de quien se paga. Con los datos llenos el pie y el checkout los muestran; vacios, no
   aparece ninguna etiqueta suelta. Datos ficticios (perilla `comercio` de e2e/app/api.ts). */
import type { AjustesE2e } from "./app/api";
import { COMERCIO_E2E } from "./app/datos";
import { expect, medirAncho, sembrarCarrito, sembrarCheckout, test, tiendaLista } from "./soporte/tienda";

const fijar = (page: import("@playwright/test").Page, a: AjustesE2e) =>
  page.addInitScript((x: AjustesE2e) => { (window as Window & { __e2eAjustes?: AjustesE2e }).__e2eAjustes = x; }, a);
const CARRITO = [{ id: 68, nombre: "Producto de prueba", precio: 1500, cantidad: 1 }];

test("[O1-17] con datos: el pie dice quién vende", async ({ page }) => {
  await fijar(page, { comercio: true });
  await page.goto("/");
  await tiendaLista(page);
  const pie = page.locator(".sh-foot__biz");
  await expect(pie).toContainText(`${COMERCIO_E2E.legal_name} · Cédula jurídica ${COMERCIO_E2E.legal_id}`);
  await expect(pie).toContainText(`Tel. ${COMERCIO_E2E.phone} · ${COMERCIO_E2E.email} · ${COMERCIO_E2E.hours}`);
  await expect(pie).toContainText("Precios en colones con IVA incluido");
  const m = await medirAncho(page);
  expect(m.documento).toBeLessThanOrEqual(m.ventana);
});

test("[O1-17] sin datos: ninguna etiqueta vacía en el pie", async ({ page }) => {
  await page.goto("/");
  await tiendaLista(page);
  const pie = page.locator(".sh-foot__biz");
  await expect(pie).toHaveText("Precios en colones con IVA incluido");
  await expect(pie).not.toContainText("Cédula jurídica");
  await expect(pie).not.toContainText("Tel.");
});

test("[O1-17] pago SINPE: titular y plazo de la reserva antes de confirmar", async ({ page }) => {
  await fijar(page, { comercio: true });
  await sembrarCarrito(page, CARRITO);
  await sembrarCheckout(page, 3);
  await page.goto("/?v=pagar");
  await tiendaLista(page);
  await expect(page.locator(".ck-review")).toContainText("Vas a pagar por SINPE Móvil a nombre de Comercio de Prueba SRL");
  await expect(page.locator(".ck-hold")).toHaveText("Te guardamos los productos 48 horas para pagar.");
  const m = await medirAncho(page);
  expect(m.documento).toBeLessThanOrEqual(m.ventana);
  // paso 1: para que se usan los datos
  await page.getByRole("button", { name: /^Paso 1:/ }).click();
  await expect(page.locator(".ck-privacy")).toContainText("Usamos tus datos para este pedido y su factura.");
});

test("[O1-17] sin datos del comercio el checkout no promete titular ni plazo", async ({ page }) => {
  await sembrarCarrito(page, CARRITO);
  await sembrarCheckout(page, 3);
  await page.goto("/?v=pagar");
  await tiendaLista(page);
  await expect(page.locator(".ck-review")).not.toContainText("a nombre de");
  await expect(page.locator(".ck-hold")).toHaveCount(0);
});
